"""Optional config-file override loader for ValidSim — **NOT WIRED**.

.. warning::
   **Status: no production caller.** Nothing in the shipped execution path
   (CLI, API, job worker) imports this module. Its only importer is
   ``tests/test_config_loader.py``. It is retained as tested groundwork, and
   ``tests/test_wiring_reachability_agent.py`` tracks it on its
   ``DEAD_BUT_ALLOWED`` list, which fails if it is ever wired without the
   documentation being corrected.

   ``VALIDSIM_CONFIG`` is therefore **not** advertised in ``.env.example``,
   even though :data:`CONFIG_ENV` and :func:`discover_config_file` read it. An
   operator who set that variable against the previous documentation would get
   silently inert behaviour — the most expensive form of dead code, because it
   looks like a working feature. ``tests/test_delivery_pipeline.py`` now asserts
   that ``.env.example`` advertises no variable without a reader.

   When a consumer does land, the merge policy is not a detail this module can
   decide: it must be applied by the consumer (see
   :mod:`validsim.project_config` for the policy/infra precedence table), and
   :func:`load_config_file` must keep returning *overrides* rather than
   mutating ``os.environ``.

Purpose
-------
This module bridges ValidSim's flat, typed configuration surface with an
optional on-disk override file so operators can tune a deployment (episode
counts, thresholds, backend endpoints, ...) without editing code.

Two formats are supported, resolved by file extension:

* ``.toml`` — parsed with the standard-library :mod:`tomllib` when available
  (Python 3.11+). ValidSim itself supports Python 3.10, so the import is
  gated; on 3.10 a clear :class:`ConfigFileError` is raised instead. TOML is
  intentionally *preferred* because it yields properly-typed values for free.
* ``.yaml`` / ``.yml`` — parsed by a *minimal, flat* YAML subset (``key:
  value`` scalar lines) implemented here so we do not depend on PyYAML.

This module is intentionally a **bridge**: the YAML reader only understands
flat scalar entries (``str``/``int``/``float``/``bool``) and raises a clear
error on nested maps/sequences, keeping the public :func:`load_config_file`
contract stable so a fuller parser (PyYAML or ruamel) can be dropped in
behind it without changing callers.

Scope note: :mod:`validsim.project_config` is the newer, TOML-only reader and
is the one to prefer for a future consumer — it supports nested tables and
``[[tasks]]``, which the flat-YAML reader here structurally cannot.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - depends on interpreter (3.10)
    tomllib = None  # type: ignore[assignment]

#: Default config file names, in discovery priority order. TOML is preferred
#: because it carries types natively; YAML is a fallback for 3.10 deployments.
DEFAULT_CONFIG_NAMES: tuple[str, ...] = (
    "validsim.config.toml",
    "validsim.config.yaml",
    "validsim.config.yml",
)

#: Environment variable carrying an explicit config path (CLI ``--config``
#: equivalent). Checked before the default names above.
#:
#: .. warning:: Read only by :func:`discover_config_file`, which itself has no
#:    production caller. This module is **not wired** — see the module
#:    docstring — so the variable is intentionally absent from ``.env.example``.
CONFIG_ENV = "VALIDSIM_CONFIG"

_BOOL_TRUE = frozenset({"true", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "no", "off"})
_NULL_WORDS = frozenset({"null", "~"})


class ConfigFileError(ValueError):
    """Raised when a config file exists but cannot be parsed.

    Subclasses :class:`ValueError` so callers can catch either this precise
    type or the broader builtin.
    """


def _strip_comment(line: str) -> str:
    """Remove an inline ``#`` comment, respecting single/double quotes."""
    in_single = in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _coerce_scalar(value: str) -> Any:
    """Coerce a bare YAML scalar into a Python str/int/float/bool/None."""
    if value == "":
        return None
    # Quoted strings stay strings (no escape processing in the minimal reader).
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]

    lowered = value.lower()
    if lowered in _NULL_WORDS:
        return None
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_yaml(text: str, source: str | Path) -> dict[str, Any]:
    """Parse a flat ``key: value`` YAML subset.

    This is deliberately tiny: only top-level scalar entries are supported.
    Nested maps/sequences and flow collections raise :class:`ConfigFileError`
    so operators get a clear signal rather than silently-stripped data.
    """
    result: dict[str, Any] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        uncommented = _strip_comment(raw)
        if not uncommented.strip():
            continue

        # Indentation on a non-empty line means a nested map, which the
        # minimal reader does not support.
        if uncommented[0] in (" ", "\t"):
            raise ConfigFileError(
                f"{source}:{lineno}: nested mappings are not supported by the "
                "minimal YAML reader (flat 'key: value' entries only)"
            )

        line = uncommented.strip()
        if line in ("---", "..."):
            continue  # document start/end markers
        if ":" not in line:
            raise ConfigFileError(
                f"{source}:{lineno}: expected 'key: value' but got {raw!r}"
            )

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ConfigFileError(f"{source}:{lineno}: empty key in {raw!r}")
        if key in result:
            raise ConfigFileError(f"{source}:{lineno}: duplicate key {key!r}")
        if value.startswith("[") or value.startswith("{"):
            raise ConfigFileError(
                f"{source}:{lineno}: flow collections are not supported by the "
                "minimal YAML reader"
            )

        result[key] = _coerce_scalar(value)
    return result


def _parse_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file with :mod:`tomllib`; clear error when unavailable."""
    if tomllib is None:
        raise ConfigFileError(
            f"{path}: TOML support requires Python 3.11+ (tomllib); "
            "use a .yaml file instead on Python 3.10"
        )
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:  # type: ignore[attr-defined]
        raise ConfigFileError(f"{path}: invalid TOML: {exc}") from exc


def _parse(path: Path) -> dict[str, Any]:
    """Dispatch to the YAML or TOML parser based on the file suffix."""
    suffix = path.suffix.lower()
    if suffix == ".toml":
        return _parse_toml(path)
    if suffix in (".yaml", ".yml"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigFileError(
                f"{path}: file is not valid UTF-8 text"
            ) from exc
        return _parse_yaml(text, path)
    raise ConfigFileError(
        f"{path}: unsupported config format {suffix!r}; expected .toml, "
        ".yaml, or .yml"
    )


def load_config_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load override ``key -> value`` entries from a single config file.

    Args:
        path: Path to a ``.toml``, ``.yaml``, or ``.yml`` file (typically the
            value of a CLI ``--config`` option).

    Returns:
        A dict of overrides. An empty dict is returned when the file does not
        exist, so a missing config file is always a no-op.

    Raises:
        ConfigFileError: If the file exists but cannot be parsed, has an
            unsupported suffix, or is not valid UTF-8 text. This subclasses
            :class:`ValueError`.
    """
    config_path = Path(path)
    if not config_path.is_file():
        return {}
    return _parse(config_path)


def discover_config_file(
    search_dir: str | os.PathLike[str] | None = None,
    names: Sequence[str] = DEFAULT_CONFIG_NAMES,
    env_var: str = CONFIG_ENV,
) -> Path | None:
    """Locate the config file to load, or ``None`` if it is fully optional.

    Resolution order:

    1. ``VALIDSIM_CONFIG`` environment variable (explicit path, the ``--config``
       equivalent), if it points at an existing file.
    2. Each name in ``names`` under ``search_dir`` (defaults to the process
       working directory), in priority order.
    """
    explicit = os.environ.get(env_var)
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate

    base = Path(search_dir) if search_dir is not None else Path.cwd()
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def load_default_config(
    search_dir: str | os.PathLike[str] | None = None,
    names: Sequence[str] = DEFAULT_CONFIG_NAMES,
    env_var: str = CONFIG_ENV,
) -> dict[str, Any]:
    """Load overrides from the discovered default config file (see
    :func:`discover_config_file`), or ``{}`` when none is present."""
    path = discover_config_file(search_dir=search_dir, names=names, env_var=env_var)
    if path is None:
        return {}
    return load_config_file(path)


__all__ = [
    "CONFIG_ENV",
    "DEFAULT_CONFIG_NAMES",
    "ConfigFileError",
    "discover_config_file",
    "load_config_file",
    "load_default_config",
]