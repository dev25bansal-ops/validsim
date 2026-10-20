"""Persistent, operator-managed webhook destinations.

The notification layer already knew how to *dispatch* to a hook and how to
*resolve* hooks from environment variables
(:func:`validsim.notify.config.notify_config_from_env`). What it could not do
was let an operator **manage** those hooks: there was no way to add a
destination without editing an environment variable, and no way to remove one
without unsetting it.

This module fills that gap with a small JSON-backed registry.

Why a file
----------

The destinations are **infrastructure**, not policy, so they belong to the
environment rather than a committed project config. But an operator still
needs a durable place to declare them. The registry is therefore:

* **outside** the project config tree, so a hook URL (which usually embeds a
  provider token) can never be committed by accident;
* **outside** the validation store, because a webhook is not a run;
* keyed by an explicit path, defaulting to ``.validsim/hooks.json`` inside the
  working directory.

Why not just read the environment
---------------------------------

Because the environment cannot express *identity*. A hook needs a name that
survives reordering, so that ``validsim webhook remove slack`` removes Slack
and not "whatever ended up second in the list". The environment gives
positional companions (``VALIDSIM_WEBHOOK_SECRETS`` pairs by index), so
removing the first destination would silently re-sign the second one with the
first one's key. A named registry removes that hazard entirely.

Secrets
-------

A hook's shared secret is a bare credential with no ``user:pass@`` section, so
the URL-userinfo redaction used elsewhere in this codebase would print it
verbatim. The registry therefore:

* writes the secret to the file with owner-only permissions (``0600``);
* **never** prints it — ``list`` shows ``"set"`` / ``"unset"`` instead;
* refuses to write a file whose permissions are group- or world-readable.

.. note::
   A registry file is still a secret at rest. The recommended deployment is a
   mounted secret or a file written by a CI secret-store step, not a file
   checked into the repository.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from validsim.notify.config import _valid_url
from validsim.notify.dispatcher import HookFormat, Severity

__all__ = [
    "ENV_REGISTRY_FILE",
    "HookRecord",
    "HookRegistry",
    "HookRegistryError",
    "HookValidationError",
    "default_registry_path",
    "registry_path_from_env",
]

#: Env var overriding the registry path (same shape as ``VALIDSIM_CACHE_FILE``).
ENV_REGISTRY_FILE = "VALIDSIM_HOOKS_FILE"

#: Default registry location, relative to the working directory.
DEFAULT_REGISTRY_NAME = Path(".validsim") / "hooks.json"

#: Registry schema version, written into the file so a future format change
#: can be detected rather than silently mis-parsed.
SCHEMA_VERSION = 1

#: Accepted payload formats.
_FORMATS: tuple[str, ...] = ("json", "slack")

#: Accepted severity thresholds. These are the *notification* severities
#: (``info`` / ``warn`` / ``critical``) used by the dispatcher — note that the
#: regression engine's own vocabulary is ``warning``, which is why the CLI
#: accepts both spellings and normalises to the dispatcher's.
_SEVERITIES: tuple[str, ...] = ("info", "warn", "critical")

#: Severity spellings accepted on input, mapped to the dispatcher's spelling.
_SEVERITY_ALIASES: dict[str, str] = {
    "info": "info",
    "warn": "warn",
    "warning": "warn",
    "critical": "critical",
}


class HookRegistryError(Exception):
    """Raised when the registry file cannot be read, written or understood."""


class HookValidationError(HookRegistryError):
    """Raised when a hook's fields are not acceptable.

    A subclass of :class:`HookRegistryError` so a caller can distinguish "the
    value you gave me is wrong" (a usage error) from "the file on disk is
    broken" (an operational problem).
    """


@dataclass(frozen=True)
class HookRecord:
    """One named webhook destination.

    Attributes:
        name: Stable identifier. This is what an operator types into
            ``validsim webhook remove <name>``.
        url: Destination URL.
        format: ``"json"`` (raw scorecard) or ``"slack"`` (Block Kit).
        secret: Optional HMAC-SHA256 shared secret. ``None`` means unsigned.
        min_severity: Lowest severity this hook wants (``info`` receives all).
    """

    name: str
    url: str
    format: HookFormat = "json"
    secret: str | None = field(default=None, repr=False)
    min_severity: Severity = "info"

    def to_dict(self, *, include_secret: bool = True) -> dict[str, Any]:
        """Render this hook as a JSON-safe dict.

        Args:
            include_secret: When ``False``, the secret is replaced by a
                boolean marker instead of its value. This is what
                ``validsim webhook list`` uses.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "format": self.format,
            "min_severity": self.min_severity,
        }
        if include_secret:
            payload["secret"] = self.secret
        else:
            payload["secret_set"] = self.secret is not None
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> HookRecord:
        """Build a record from a decoded JSON object, validating every field.

        Raises:
            HookValidationError: If any field is missing or malformed.
        """
        if not isinstance(raw, dict):
            raise HookValidationError(
                f"hook entry must be an object, got {type(raw).__name__}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HookValidationError("hook entry is missing a non-empty 'name'")
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HookValidationError(f"hook {name!r} is missing a non-empty 'url'")
        fmt = raw.get("format", "json")
        if fmt not in _FORMATS:
            raise HookValidationError(
                f"hook {name!r} has unsupported format {fmt!r}; "
                f"expected one of {', '.join(_FORMATS)}"
            )
        severity = _SEVERITY_ALIASES.get(
            str(raw.get("min_severity", "info")).strip().lower()
        )
        if severity is None:
            raise HookValidationError(
                f"hook {name!r} has unsupported min_severity "
                f"{raw.get('min_severity')!r}; expected one of {', '.join(_SEVERITIES)}"
            )
        secret = raw.get("secret")
        if secret is not None and not isinstance(secret, str):
            raise HookValidationError(f"hook {name!r} has a non-string secret")
        return cls(
            name=name.strip(),
            url=url.strip(),
            format=fmt,  # type: ignore[arg-type]
            secret=secret or None,
            min_severity=severity,  # type: ignore[arg-type]
        )


def default_registry_path() -> Path:
    """Return the default registry path (``.validsim/hooks.json``)."""
    return Path.cwd() / DEFAULT_REGISTRY_NAME


def registry_path_from_env(environ: dict[str, str] | None = None) -> Path:
    """Resolve the registry path, honouring :data:`ENV_REGISTRY_FILE`.

    Args:
        environ: Environment mapping to read (defaults to :data:`os.environ`).
    """
    if environ is None:
        environ = dict(os.environ)
    override = environ.get(ENV_REGISTRY_FILE, "").strip()
    if override:
        return Path(override).expanduser()
    return default_registry_path()


class HookRegistry:
    """A named collection of webhook destinations, persisted as JSON.

    Args:
        path: Where the registry lives on disk.
        hooks: The hooks it holds.

    .. note::
       The registry is **not** a live configuration source by itself: the
       dispatcher resolves destinations from the environment. This registry is
       the operator-facing management surface, and :meth:`HookRegistry.to_env`
       renders it in the positional form the dispatcher understands, so an
       operator can pipe one into the other deliberately.
    """

    def __init__(self, path: Path, hooks: tuple[HookRecord, ...] = ()) -> None:
        self.path = path
        self._hooks: list[HookRecord] = list(hooks)

    # -- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> HookRegistry:
        """Load a registry from disk.

        A **missing** file is not an error: it yields an empty registry, so
        ``validsim webhook list`` works before anything has been added.

        Raises:
            HookRegistryError: If the file exists but cannot be parsed or
                contains an invalid hook.
        """
        resolved = Path(path) if path is not None else registry_path_from_env()
        if not resolved.exists():
            return cls(resolved)
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise HookRegistryError(f"cannot read {resolved}: {exc}") from exc
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise HookRegistryError(f"{resolved}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise HookRegistryError(
                f"{resolved}: expected a JSON object at the document root"
            )
        version = data.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            raise HookRegistryError(
                f"{resolved}: schema_version={version!r} is not supported by this build "
                f"(expected {SCHEMA_VERSION})"
            )
        raw_hooks = data.get("hooks", [])
        if not isinstance(raw_hooks, list):
            raise HookRegistryError(f"{resolved}: 'hooks' must be a list")
        hooks = tuple(HookRecord.from_dict(item) for item in raw_hooks)
        return cls(resolved, hooks)

    # -- accessors -------------------------------------------------------

    @property
    def hooks(self) -> tuple[HookRecord, ...]:
        """The hooks in this registry, in declaration order."""
        return tuple(self._hooks)

    def __len__(self) -> int:
        return len(self._hooks)

    def get(self, name: str) -> HookRecord | None:
        """Return the hook called *name*, or ``None`` when there is no such hook."""
        for hook in self._hooks:
            if hook.name == name:
                return hook
        return None

    def names(self) -> list[str]:
        """Every hook name, in declaration order."""
        return [hook.name for hook in self._hooks]

    # -- mutations -------------------------------------------------------

    def add(
        self,
        name: str,
        url: str,
        *,
        format: str = "json",  # noqa: A002 - keep kwarg friendly
        secret: str | None = None,
        min_severity: str = "info",
        replace_existing: bool = False,
    ) -> HookRecord:
        """Add a hook, validating every field first.

        Args:
            name: The hook's stable identifier.
            url: Destination URL (must be an absolute ``http``/``https`` URL).
            format: ``"json"`` or ``"slack"``.
            secret: Optional HMAC shared secret.
            min_severity: ``"info"`` / ``"warn"`` / ``"critical"``. The
                regression engine's spelling ``"warning"`` is accepted and
                normalised to ``"warn"``.
            replace_existing: When ``True``, an existing hook with the same
                name is replaced. When ``False`` (default), adding a duplicate
                name is an error — silently replacing a destination would
                change where a team's alerts go.

        Returns:
            The stored :class:`HookRecord`.

        Raises:
            HookValidationError: If any field is invalid, or the name already
                exists and ``replace_existing`` is ``False``.
        """
        record = self._validated_record(name, url, format, secret, min_severity)
        existing = self.get(record.name)
        if existing is not None and not replace_existing:
            raise HookValidationError(
                f"a hook named {record.name!r} already exists "
                f"(use --replace to overwrite it, or "
                f"'validsim webhook remove {record.name}' first)"
            )
        if existing is not None:
            self._hooks = [h for h in self._hooks if h.name != record.name]
        self._hooks.append(record)
        return record

    def remove(self, name: str) -> HookRecord:
        """Remove the hook called *name* and return it.

        Raises:
            HookValidationError: If no hook has that name.
        """
        record = self.get(name)
        if record is None:
            known = ", ".join(self.names()) or "none"
            raise HookValidationError(f"no hook named {name!r} (known hooks: {known})")
        self._hooks = [h for h in self._hooks if h.name != name]
        return record

    def update(
        self,
        name: str,
        *,
        url: str | None = None,
        format: str | None = None,  # noqa: A002 - keep kwarg friendly
        secret: str | None = None,
        min_severity: str | None = None,
        clear_secret: bool = False,
    ) -> HookRecord:
        """Update an existing hook in place.

        Only the fields that are supplied are changed, so a partial update
        cannot silently reset a hook's format or severity.

        Raises:
            HookValidationError: If the hook does not exist, or a supplied
                field is invalid.
        """
        record = self.get(name)
        if record is None:
            known = ", ".join(self.names()) or "none"
            raise HookValidationError(f"no hook named {name!r} (known hooks: {known})")
        new_url = url if url is not None else record.url
        new_format = format if format is not None else record.format
        new_secret = record.secret
        if clear_secret:
            new_secret = None
        elif secret is not None:
            new_secret = secret
        new_severity = min_severity if min_severity is not None else record.min_severity
        updated = self._validated_record(
            record.name, new_url, new_format, new_secret, new_severity
        )
        self._hooks = [updated if h.name == record.name else h for h in self._hooks]
        return updated

    # -- validation ------------------------------------------------------

    @staticmethod
    def _validated_record(
        name: str,
        url: str,
        format: str,  # noqa: A002 - keep kwarg friendly
        secret: str | None,
        min_severity: str,
    ) -> HookRecord:
        """Validate and normalise the fields of a hook.

        Raises:
            HookValidationError: If any field is invalid.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise HookValidationError("hook name must not be empty")
        # A name is used as a CLI argument and as a filename-safe key; a
        # whitespace-bearing or over-long name would make the command
        # awkward to type and the file awkward to read.
        if len(clean_name) > 64:
            raise HookValidationError("hook name must be at most 64 characters")
        if any(character.isspace() for character in clean_name):
            raise HookValidationError("hook name must not contain whitespace")
        if clean_name.startswith("-"):
            raise HookValidationError("hook name must not start with '-'")

        clean_url = (url or "").strip()
        if not clean_url:
            raise HookValidationError("hook url must not be empty")
        if not _valid_url(clean_url):
            raise HookValidationError(
                f"hook url must be an absolute http(s) URL, got {clean_url!r}"
            )

        clean_format = (format or "json").strip().lower()
        if clean_format not in _FORMATS:
            raise HookValidationError(
                f"unsupported hook format {format!r}; expected one of {', '.join(_FORMATS)}"
            )

        clean_severity = (min_severity or "info").strip().lower()
        resolved_severity = _SEVERITY_ALIASES.get(clean_severity)
        if resolved_severity is None:
            raise HookValidationError(
                f"unsupported min_severity {min_severity!r}; "
                f"expected one of {', '.join(_SEVERITIES)}"
            )

        if secret is not None:
            if not isinstance(secret, str):
                raise HookValidationError("hook secret must be a string")
            secret = secret.strip() or None

        return HookRecord(
            name=clean_name,
            url=clean_url,
            format=clean_format,  # type: ignore[arg-type]
            secret=secret,
            min_severity=resolved_severity,  # type: ignore[arg-type]
        )

    # -- persistence -----------------------------------------------------

    def to_dict(self, *, include_secret: bool = True) -> dict[str, Any]:
        """Render the whole registry as a JSON-safe dict."""
        return {
            "schema_version": SCHEMA_VERSION,
            "hooks": [
                hook.to_dict(include_secret=include_secret) for hook in self._hooks
            ],
        }

    def save(self, path: Path | None = None) -> Path:
        """Write the registry to disk.

        The file is written with owner-only permissions (``0600``) because it
        holds shared secrets.

        Raises:
            HookRegistryError: If the directory cannot be created or the file
                cannot be written.
        """
        target = Path(path) if path is not None else self.path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Create with restrictive permissions *before* writing, so the
            # secret is never briefly world-readable.
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, sort_keys=False)
                handle.write("\n")
        except OSError as exc:
            raise HookRegistryError(f"cannot write {target}: {exc}") from exc
        # Enforce the mode even when the file already existed with a wider
        # one: O_CREAT does not change the mode of an existing file.
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:  # pragma: no cover - defensive
            raise HookRegistryError(
                f"cannot set permissions on {target}: {exc}"
            ) from exc
        self.path = target
        return target

    # -- interop with the environment-based resolver ---------------------

    def to_env(self) -> dict[str, str]:
        """Render this registry in the positional form the dispatcher reads.

        Returns:
            A mapping of the four ``VALIDSIM_WEBHOOK_*`` env vars.

        .. note::
           The companions are positional, so the order of this registry is
           the order the secrets will pair in. Reordering the registry
           reorders the pairing — which is exactly why the registry exists in
           named form in the first place.
        """
        return {
            "VALIDSIM_WEBHOOK_URLS": ",".join(hook.url for hook in self._hooks),
            "VALIDSIM_WEBHOOK_SECRETS": ",".join(hook.secret or "" for hook in self._hooks),
            "VALIDSIM_WEBHOOK_FORMATS": ",".join(hook.format for hook in self._hooks),
            "VALIDSIM_WEBHOOK_MIN_SEVERITY": ",".join(
                hook.min_severity for hook in self._hooks
            ),
        }
