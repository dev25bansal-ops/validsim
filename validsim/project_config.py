"""Committed per-project validation policy: parsing, classification, redaction.

.. warning::
   **Status: not yet wired into production.** Nothing in the shipped execution
   path (API, CLI, job worker) imports this module; it is currently reachable
   only from ``tests/test_project_config.py``. It is retained deliberately as
   tested groundwork for the ``validsim.config.toml`` file support described
   below, and because its key-classification table doubles as the drift guard
   that catches a newly added ``VALIDSIM_*`` key which has not been classified
   as policy, infra, or secret.

   ``VALIDSIM_CONFIG`` is consequently **not** advertised in ``.env.example``,
   even though :data:`CONFIG_ENV` and :func:`discover_config_file` read it.
   Advertising it would hand an operator the belief that a committed config
   file changes behaviour, and it does not: the CLI, the API and the worker all
   read the environment only. ``tests/test_delivery_pipeline.py`` asserts that
   every variable in ``.env.example`` has a reader in ``validsim/``.

   When a consumer lands, this module's public API is the contract; do not
   change the classification or redaction behaviour without updating
   ``docs/ISSUE_CATALOG.md`` and the security docs, since redaction is a
   security boundary.

   :func:`resolve_config` and :func:`config_to_env` are the unwired pair. The
   rest of this module is **not** dead: :func:`effective_to_env`,
   :func:`classify_key`, :func:`mask_secret` and :data:`known_env_keys` are the
   redaction and drift-guard machinery that the class table above depends on,
   and they are exercised by the same suite.

This module turns a ``validsim.config.toml`` file into the two things the rest
of the platform needs from it:

* :func:`resolve_config` — the parsed file as a natively-typed ``dict``, and
* :func:`config_to_env` — a ``VALIDSIM_*`` string mapping for display, with
  secrets masked.

It is deliberately **not** a settings loader. It does not read the
environment, does not merge defaults, does not validate against
:class:`~validsim.config.TaskConfig`, and **never mutates ``os.environ``**.
Those are separate concerns owned by :mod:`validsim.settings`; keeping them
here means a config file can be parsed, inspected, redacted and tested with
no ambient state at all.

Two key namespaces
------------------
This module deliberately spans **two** key namespaces, and mixing them is a
bug (not a style preference) because secrets live in only one of them:

* **Policy names** — TOML-native: ``threshold``, ``episodes``, ``robot``.
  What a v1 config *file* may contain. See :func:`resolve_config`.
* **Env names** — literal ``VALIDSIM_*`` strings: ``VALIDSIM_PG_URL``,
  ``VALIDSIM_API_KEY``. What the *environment* (and therefore the merged
  effective config) uses. These are the only keys that can be secrets.

Hence the two serializers:

* :func:`config_to_env` renders a **config file** (policy names). Config files
  are policy-only, so no key here can ever be a secret.
* :func:`effective_to_env` renders the **merged effective** config (env names)
  and is where :data:`SECRET_KEYS` redaction actually applies. This is the
  serializer ``validsim config show`` and ``validsim support-bundle`` must
  use, because the environment half is where credentials live.

Passing a policy-named mapping to :func:`effective_to_env` (or an env-named
one to :func:`config_to_env`) silently renders nothing useful, because each
only recognises its own namespace.

Precedence (applied by the consumer, not here)
---------------------------------------------
::

    CLI flag / --set   >   policy key -> config file   >   environment
                       >   infra key  -> environment   >   other source
                       >   builtin default

The asymmetry is deliberate. See :data:`POLICY_KEYS` and :data:`INFRA_KEYS`.

Format
------
TOML only. :mod:`tomllib` is used on Python 3.11+; on 3.10 the ``tomli``
backport is imported (declared as ``tomli; python_version < "3.11"``). The
former hand-rolled flat-YAML reader is intentionally **not** reimplemented
here: it could not express nested tables or arrays of tables, so it could
not represent a multi-task project file.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if sys.version_info >= (3, 11):  # pragma: no cover - version dependent
    import tomllib
else:  # pragma: no cover - version dependent
    import tomli as tomllib

__all__ = [
    "CONFIG_ENV",
    "DEFAULT_CONFIG_NAMES",
    "INFRA_KEYS",
    "POLICY_KEYS",
    "PROJECT_CONFIG_SCHEMA_VERSION",
    "SECRET_KEYS",
    "ConfigFileError",
    "classify_key",
    "config_to_env",
    "discover_config_file",
    "effective_to_env",
    "known_env_keys",
    "mask_secret",
    "redact_value",
    "resolve_config",
]

#: Config-file schema version this build understands. A file declaring a
#: *different* major version is refused rather than partially interpreted —
#: silently ignoring keys a future release added is how a validation policy
#: quietly stops being enforced.
PROJECT_CONFIG_SCHEMA_VERSION = 1

#: Config file names, in discovery priority order.
DEFAULT_CONFIG_NAMES: tuple[str, ...] = ("validsim.config.toml",)

#: Bootstrap variable naming an explicit config path. Read directly from the
#: environment by :func:`discover_config_file` — it is how you *find* the file
#: and so cannot itself be governed by the file's contents.
CONFIG_ENV = "VALIDSIM_CONFIG"

#: Keys that express a team's **validation policy**. These take their value
#: from the committed config file so that ambient environment state cannot
#: quietly weaken an agreed safety bar. Expressed with TOML-native names
#: because they are file content, not deployment coordinates.
POLICY_KEYS: frozenset[str] = frozenset(
    {
        "adversarial",
        "checkpoints",
        "episodes",
        "environment",
        "id",
        "profile",
        "randomization",
        "robot",
        "scenarios",
        "tags",
        "task_id",
        "threshold",
    }
)

#: Top-level keys and sections a v1 project file may declare. Scalar policy
#: keys (``threshold``, ``episodes``, ...) are legal at the top level because
#: that is where the documented schema puts them; the remaining entries are
#: tables, or ``schema_version``/``tasks`` which are neither.
_KNOWN_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "schema_version",
        "tasks",
        "defaults",
        "checkpoints",
        "adversarial",
        "store",
        "backend",
        "notify",
        "junit",
    } | POLICY_KEYS
)

#: Keys that express **deployment infrastructure**: where things live, which
#: backend runs, how hard the API throttles. These take their value from the
#: environment so a committed file can never pin a host path, a DSN, a
#: credential, or a CI override. Every key is the literal env-var name.
INFRA_KEYS: frozenset[str] = frozenset(
    {
        "VALIDSIM_API_KEY",
        "VALIDSIM_ASSET_ROOT",
        "VALIDSIM_BACKEND",
        "VALIDSIM_CACHE_FILE",
        "VALIDSIM_CONFIG",
        "VALIDSIM_CORS_ORIGINS",
        "VALIDSIM_ENV",
        "VALIDSIM_ISAAC_WORKER_KEY",
        "VALIDSIM_ISAAC_WORKER_URL",
        "VALIDSIM_JOB_LEASE_SECONDS",
        "VALIDSIM_JOB_MAX_ATTEMPTS",
        "VALIDSIM_JOB_MAX_RECLAIMS",
        "VALIDSIM_JOB_QUEUE",
        "VALIDSIM_JOB_QUEUE_MAX_DEPTH",
        "VALIDSIM_JOB_RETRY_BACKOFF_SECONDS",
        "VALIDSIM_LLM_API_KEY",
        "VALIDSIM_LLM_BASE_URL",
        "VALIDSIM_LLM_ENABLED",
        "VALIDSIM_LLM_MODEL",
        "VALIDSIM_LOG_LEVEL",
        "VALIDSIM_NOTIFY_ENABLED",
        "VALIDSIM_PG_URL",
        "VALIDSIM_PIPELINE_CONCURRENCY",
        "VALIDSIM_RATE_LIMIT",
        "VALIDSIM_RATE_WINDOW_SECONDS",
        "VALIDSIM_REDIS_URL",
        "VALIDSIM_SMTP_FROM",
        "VALIDSIM_SMTP_HOST",
        "VALIDSIM_SMTP_PASSWORD",
        "VALIDSIM_SMTP_PORT",
        "VALIDSIM_SMTP_TLS",
        "VALIDSIM_SMTP_USER",
        "VALIDSIM_SQLITE_PATH",
        "VALIDSIM_STORE",
        "VALIDSIM_THREAD_LIMITER_TOKENS",
        "VALIDSIM_WEBHOOKS_LIVE",
        "VALIDSIM_WEBHOOK_FORMATS",
        "VALIDSIM_WEBHOOK_MIN_SEVERITY",
        "VALIDSIM_WEBHOOK_SECRETS",
        "VALIDSIM_WEBHOOK_URLS",
    }
)

#: Infrastructure keys whose **value** is a credential or carries one inside a
#: URL. These are masked by :func:`config_to_env`. Membership is a subset of
#: :data:`INFRA_KEYS`; :data:`_KEY_CLASSIFICATION` enforces that.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "VALIDSIM_API_KEY",
        "VALIDSIM_ISAAC_WORKER_KEY",
        "VALIDSIM_LLM_API_KEY",
        "VALIDSIM_PG_URL",
        "VALIDSIM_REDIS_URL",
        "VALIDSIM_SMTP_PASSWORD",
        "VALIDSIM_WEBHOOK_SECRETS",
    }
)

#: Placeholder substituted for any redacted value.
REDACTED = "***REDACTED***"

#: Full classification, used by the drift guard and by :func:`classify_key`.
_KEY_CLASSIFICATION: dict[str, str] = {
    **{key: "policy" for key in POLICY_KEYS},
    **{key: "infra" for key in INFRA_KEYS},
}

#: Matches the ``user:password@host`` userinfo section of a URL.
_URL_USERINFO_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<creds>[^/@]+)@")


class ConfigFileError(ValueError):
    """Raised when a project config file exists but cannot be used.

    Subclasses :class:`ValueError` so callers may catch either this precise
    type or the broader builtin.
    """


def classify_key(key: str) -> str:
    """Classify *key* as ``"policy"``, ``"infra"`` or ``"unknown"``.

    Args:
        key: A TOML-native policy name (e.g. ``"threshold"``) or a
            ``VALIDSIM_*`` env-var name (e.g. ``"VALIDSIM_PG_URL"``).

    Returns:
        ``"policy"`` when the committed file should win, ``"infra"`` when the
        environment should win, and ``"unknown"`` when the key is not
        classified. ``"unknown"`` is returned rather than guessed so that
        :func:`config_to_env` can fail closed.
    """
    return _KEY_CLASSIFICATION.get(key, "unknown")


def known_env_keys() -> frozenset[str]:
    """Return every ``VALIDSIM_*`` key this build classifies.

    The drift guard in ``tests/test_project_config.py`` scans ``validsim/`` for
    env-var string literals and asserts each one is a member, so a newly
    added key cannot silently inherit a bucket.
    """
    return INFRA_KEYS


def redact_value(value: Any) -> Any:
    """Return *value* with any embedded credential removed.

    A URL has only its **userinfo** section replaced
    (``scheme://***REDACTED***@host/path``), which keeps the host and database
    visible — those are exactly the facts a support thread needs — while
    removing the password. A value with no embedded credential is returned
    unchanged, so this never mangles ordinary settings.

    .. warning::
       This is **only** correct for URL-shaped secrets. For a bare secret
       (an API key) there is nothing to strip, so the value passes through
       intact — see :func:`mask_secret` for the correct behaviour.
    """
    if not isinstance(value, str) or not value:
        return value
    match = _URL_USERINFO_RE.match(value)
    if match:
        return f"{match.group('scheme')}{REDACTED}@{value[match.end():]}"
    return value


def mask_secret(value: Any) -> Any:
    """Mask a value that is *known* to be a secret.

    Used for every key in :data:`SECRET_KEYS`. If the value is URL-shaped its
    userinfo is stripped via :func:`redact_value` (so the host remains visible
    for diagnosis); otherwise it is replaced wholesale with :data:`REDACTED`.

    This distinction is the whole point: routing a bare API key through
    :func:`redact_value` would emit it in **plaintext**, because there is no
    ``user:pass@`` section to strip. Anything whose key is in
    :data:`SECRET_KEYS` must go through here.
    """
    if not isinstance(value, str) or not value:
        return value
    if _URL_USERINFO_RE.match(value):
        return redact_value(value)
    return REDACTED


def _parse_toml(path: Path) -> dict[str, Any]:
    """Parse *path* as TOML, converting parser errors to :class:`ConfigFileError`."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileError(f"{path}: invalid TOML: {exc}") from exc
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise ConfigFileError(f"{path}: file is not valid UTF-8 text") from exc
    if not isinstance(data, dict):  # pragma: no cover - tomllib always returns dict
        raise ConfigFileError(f"{path}: expected a TOML table at the document root")
    return data


def _validate_sections(data: Mapping[str, Any], path: Path) -> None:
    """Reject unknown top-level sections and an unsupported schema version.

    Unknown sections are an error rather than a warning so a typo cannot
    leave a policy silently unenforced, and so a file written for a newer
    release fails loudly on an older one instead of being half-applied.
    """
    unknown = sorted(set(data) - _KNOWN_TOP_LEVEL)
    if unknown:
        raise ConfigFileError(
            f"{path}: unknown top-level section(s): {', '.join(unknown)}; "
            f"known sections are {', '.join(sorted(_KNOWN_TOP_LEVEL))}"
        )
    version = data.get("schema_version")
    if version is not None and version != PROJECT_CONFIG_SCHEMA_VERSION:
        raise ConfigFileError(
            f"{path}: schema_version={version!r} is not supported by this build "
            f"(expected {PROJECT_CONFIG_SCHEMA_VERSION})"
        )


def discover_config_file(
    search_dir: str | os.PathLike[str] | None = None,
    names: Sequence[str] = DEFAULT_CONFIG_NAMES,
    env_var: str = CONFIG_ENV,
) -> Path | None:
    """Locate the project config file, or ``None`` when there is none.

    Resolution order: an explicit path in *env_var* (read from
    ``os.environ``), then each of *names* under *search_dir* (default: the
    current working directory).

    A missing file is never an error — the platform is fully functional
    without one, so discovery returns ``None`` and the caller proceeds on
    defaults and environment.
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


def resolve_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load a project config file and return its contents as typed data.

    Args:
        path: Explicit path to a ``.toml`` file. When ``None``, discovery runs
            via :func:`discover_config_file` (i.e. ``VALIDSIM_CONFIG`` then
            ``validsim.config.toml`` in the working directory).

    Returns:
        A ``dict`` holding **only the keys the file explicitly set**, with
        values in their native TOML types — an ``int`` stays an ``int``, a
        ``float`` a ``float``, an array a ``list``. Nothing is merged with
        defaults, renamed, or coerced to ``str``. **An absent file yields
        ``{}``**, so a caller can always treat the result as "overrides".

    Raises:
        ConfigFileError: The file exists but is not valid TOML, declares an
            unknown top-level section, or declares an unsupported
            ``schema_version``.

    Note:
        This function performs **no environment merging and never writes to
        ``os.environ``**. Mutating the environment as a side effect of
        loading a file is how a configuration that works locally ends up
        silently different in CI.
    """
    resolved = Path(path) if path is not None else discover_config_file()
    if resolved is None or not resolved.is_file():
        return {}
    if resolved.suffix.lower() != ".toml":
        raise ConfigFileError(
            f"{resolved}: unsupported config format {resolved.suffix!r}; "
            "only .toml is supported (add 'tomli; python_version<\"3.11\"' for "
            "Python 3.10)"
        )
    data = _parse_toml(resolved)
    _validate_sections(data, resolved)
    return data


def config_to_env(
    config: Mapping[str, Any],
    *,
    redact: bool = True,
    flatten: bool = False,
) -> dict[str, str]:
    """Render a project config as a ``VALIDSIM_*`` string mapping for display.

    This is the single serializer that knows how configuration becomes
    ``VALIDSIM_*`` strings. Consumers (:command:`validsim config show`,
    ``validsim run --print-effective-config`` and ``validsim support-bundle``)
    all render through it, so there is exactly one place that decides what a
    key is called and whether its value is sensitive.

    Args:
        config: A mapping as returned by :func:`resolve_config`, i.e. **policy
            names** (``threshold``, ``episodes``...). Config files are policy
            only — see the v1 restriction in :func:`resolve_config` — so this
            function renders *policy* and is **not** where secrets appear.
            Use :func:`effective_to_env` for the merged, env-named mapping
            that :command:`validsim config show` and
            ``validsim support-bundle`` display.
        redact: Accepted for signature symmetry. Policy keys are not secrets,
            so this has no effect here; it is the operative flag on
            :func:`effective_to_env`.
        flatten: Reserved for nested-section flattening. When ``False`` (the
            default) only top-level scalar entries are rendered.

    Returns:
        A ``dict[str, str]``. Emission is restricted to classified keys, so an
        unclassified key is silently not emitted rather than leaked. The
        filter is allowlist-shaped and therefore fails *closed*.

    Raises:
        ValueError: ``flatten=True`` is passed. Nested-section flattening is
            not implemented yet; raising beats returning a half-rendered map.
    """
    if flatten:
        raise ValueError("flatten=True is not implemented yet; pass flatten=False")

    rendered: dict[str, str] = {}
    for key, value in config.items():
        if classify_key(key) != "policy":
            # Includes env-named keys: those belong to ``effective_to_env``.
            # Skipping rather than guessing is what keeps the two
            # namespaces from being silently interleaved.
            continue
        if isinstance(value, (list, dict)):
            # Structured values (e.g. [[tasks]]) have no single env-var
            # equivalent; they are rendered by their owning command instead.
            continue
        if isinstance(value, bool):
            rendered[key] = "1" if value else "0"
        else:
            rendered[key] = str(value)
    return rendered


def effective_to_env(
    effective: Mapping[str, Any],
    *,
    redact: bool = True,
) -> dict[str, str]:
    """Render the **merged, env-named** effective configuration for display.

    This is the serializer that redaction actually applies to. Its input is
    the ``VALIDSIM_*``-named mapping produced by
    ``Settings.load(config, environ)`` — i.e. the environment (where the
    secrets live) already merged with the project config file. The env half is
    what carries credentials, so redaction is reachable **here** and not in
    :func:`config_to_env`, whose input is policy-only by construction.

    Args:
        effective: Merged mapping keyed by literal ``VALIDSIM_*`` env-var
            names. Extra keys (not in :data:`INFRA_KEYS`) are ignored.
        redact: When ``True`` (the default) mask every value whose key is in
            :data:`SECRET_KEYS` and strip embedded URL credentials. When
            ``False``, values pass through unchanged — for a local
            ``--show-secrets`` inspection only, **never** for a support bundle.

    Returns:
        A ``dict[str, str]`` of display strings with secrets masked and
        non-secret values unchanged. Emission is allowlist-shaped (keys must
        be in :data:`INFRA_KEYS`), so it fails closed for unclassified keys.
    """
    rendered: dict[str, str] = {}
    for key, value in effective.items():
        if key not in INFRA_KEYS:
            continue
        if isinstance(value, (list, dict)):
            continue
        if redact and key in SECRET_KEYS:
            rendered[key] = mask_secret(value)
            continue
        if isinstance(value, bool):
            rendered[key] = "1" if value else "0"
        else:
            rendered[key] = str(value)
    return rendered
