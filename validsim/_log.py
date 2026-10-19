"""Structured JSON logging for the ValidSim platform.

The platform emits a single machine-parseable line per log record so that a
container/CI log pipeline can index ``timestamp``/``level``/``logger``/
``message`` plus any structured ``extra`` fields a caller attaches via the
stdlib ``extra={...}`` argument. Configuration is deliberately minimal and
idempotent: :func:`configure_logging` attaches exactly one JSON handler to the
``validsim`` logger, so repeated calls (e.g. one per :func:`create_app`
invocation) neither duplicate handlers nor raise.

This module is named ``logging`` inside the ``validsim`` package. Because
Python 3 uses absolute imports, ``import logging`` *within* this file still
resolves to the standard library, and other modules that want the stdlib get it
unchanged; only ``from validsim import logging`` / ``validsim.logging`` reach
this module.

Redaction
---------
:class:`JsonLogFormatter` applies a **redaction layer** to every structured
field before serialising it: a field whose name :func:`is_sensitive_key`
recognises has its value replaced with :data:`REDACTED`, recursively through
nested containers. Without it, ``extra={"api_key": key}`` wrote the credential
to stdout and every downstream aggregator with no error and no visible
mistake — the log line *was* the leak. The matching rules and their two
deliberate non-goals (no prose scanning, no permissive substring matching) are
documented on the class and on :data:`SENSITIVE_KEY_NAMES`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

__all__ = [
    "JsonLogFormatter",
    "LOGGER_NAME",
    "LOG_LEVEL_ENV",
    "REDACTED",
    "SENSITIVE_KEY_NAMES",
    "configure_logging",
    "get_logger",
    "is_sensitive_key",
]

#: Name of the package root logger every ValidSim logger descends from.
LOGGER_NAME = "validsim"
#: Env var naming the default level when none is passed explicitly.
LOG_LEVEL_ENV = "VALIDSIM_LOG_LEVEL"
#: Level used when neither an argument nor :data:`LOG_LEVEL_ENV` is supplied.
DEFAULT_LEVEL = logging.INFO

#: Attribute marker identifying the handler this module installs, so repeated
#: configuration can find and replace its own handler without disturbing any
#: handlers a host application (or the test runner) attached independently.
_HANDLER_MARKER = "_validsim_json_handler"
#: Flag attribute set on the logger once configuration has run.
_CONFIGURED_MARKER = "_validsim_logging_configured"

#: Standard :class:`logging.LogRecord` attributes that are *not* caller extras.
#: Computed once from a throwaway record so the formatter never serialises the
#: record's built-in fields (``msg``, ``args``, ``module``, ``lineno``, ...) as
#: if they were structured context.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__.keys()
) | {"asctime", "message", "taskName"}

#: Placeholder substituted for any value whose field name is known-sensitive.
REDACTED = "***REDACTED***"

#: Canonical names of fields whose value must never be written to a log.
#: Matched against a *normalised* key (see :func:`is_sensitive_key`), so
#: ``api_key``, ``API-KEY`` and ``apiKey`` all resolve to one entry here.
#:
#: The set is an explicit allow-list of *names*, not a substring rule. A
#: substring match on "auth" would eat ``auth_enabled`` — a boolean this
#: codebase logs on purpose — and would mask every field that merely mentions a
#: sensitive word, destroying the diagnostics the log exists to provide. The
#: trade is deliberate: an unlisted derived spelling (``csrftoken``) leaks,
#: while a listed word never eats an innocent field.
#:
#: ``credentials`` and ``cookie`` are included because a header/connection bag
#: is a secret container by nature — you cannot redact a bag field-by-field
#: and still have called it redacted.
SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passwd",
        "password",
        "privatekey",
        "secret",
        "secretkey",
        "setcookie",
        "token",
    }
)

#: Suffixes that mark a (possibly namespaced) field as a credential. A field
#: whose normalised name ends with one of these is sensitive, so
#: ``VALIDSIM_API_KEY`` and ``smtp_password`` are caught without listing every
#: possible namespace prefix.
#:
#: Credential-bearing ``*key`` names are listed **compound**
#: (``accesskey``, ``privatekey``, …) rather than as a bare ``key``: a bare
#: ``key`` suffix would mask ``monkey``, ``keyboard`` and ``hockey``, and a
#: logger that eats three innocent fields per day gets turned off entirely.
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "apikey",
    "accesskey",
    "credential",
    "credentials",
    "encryptionkey",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "signingkey",
    "sshkey",
    "token",
)

#: Guards the cycle-walk: deep in a pathological structure we stop masking
#: rather than recurse without bound. Real ``extra`` payloads are a handful of
#: levels deep, so this is a safety valve, not a working limit.
_MAX_REDACT_DEPTH = 12


def _normalise_key(key: str) -> str:
    """Reduce a field name to its comparison form.

    Lower-cases and deletes every character that is not ``a-z0-9``, so
    ``X-Api_Key``, ``x.api.key`` and ``API KEY`` all collapse to ``xapikey``.
    Substring-ish shapes (``api_key``, ``apikey``) are handled by the caller
    through a small alias table rather than by guessing a word boundary.
    """
    return "".join(char for char in str(key).lower() if char.isalnum())


def is_sensitive_key(key: str) -> bool:
    """Return whether a structured log field named *key* must be redacted.

    Args:
        key: The field name as it would appear in the emitted JSON object.

    Returns:
        ``True`` when the name is a known credential field.

    The rule is two-part and both parts are necessary:

    * an **exact** match on :data:`SENSITIVE_KEY_NAMES` (``authorization``,
      ``cookie``, ``set-cookie``, ``credentials``), and
    * a **suffix** match on :data:`_SENSITIVE_SUFFIXES`, so a namespaced field
      like ``VALIDSIM_API_KEY`` or ``smtp_password`` is caught.

    A bare ``key`` is deliberately *not* a suffix: ``monkey``, ``keyboard``
    and ``hockey`` all end in it, so the credential-bearing ``*key`` names are
    listed compound (``accesskey``, ``privatekey``) instead.
    """
    normalised = _normalise_key(key)
    if not normalised:
        return False
    if normalised in SENSITIVE_KEY_NAMES:
        return True
    return any(normalised.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Return *value* with every known-sensitive field name replaced.

    Walks dicts, lists and tuples recursively, so a credential nested one level
    down (``{"auth": {"api_key": ...}}``) is masked too. Non-container values are
    returned unchanged; a scalar cannot carry a *field name*, so there is
    nothing to match on.

    A self-referential structure is replaced wholesale rather than followed
    forever: ``record.__dict__`` is caller-controlled, and the formatter's
    contract is to emit a line, not to raise or hang.
    """
    if _depth >= _MAX_REDACT_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact_value(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        rendered = [redact_value(item, _depth=_depth + 1) for item in value]
        return type(value)(rendered) if isinstance(value, tuple) else rendered
    return value


def _resolve_level(level: int | str | None) -> int:
    """Resolve an explicit ``level`` to an int, falling back to env then default.

    ``level`` may be an int or a level name (``"DEBUG"``, ``"warning"``...). An
    unset or unparseable value falls back to :data:`LOG_LEVEL_ENV`, and finally
    to :data:`DEFAULT_LEVEL`, so misconfiguration degrades to INFO rather than
    crashing app startup.
    """
    candidate: int | str | None = level
    if candidate is None:
        candidate = os.environ.get(LOG_LEVEL_ENV)
    if candidate is None:
        return DEFAULT_LEVEL
    if isinstance(candidate, int):
        return candidate
    resolved = logging.getLevelName(str(candidate).strip().upper())
    return resolved if isinstance(resolved, int) else DEFAULT_LEVEL


class JsonLogFormatter(logging.Formatter):
    """Render each record as a single line of JSON.

    The emitted object always carries ``timestamp`` (ISO-8601 UTC), ``level``,
    ``logger`` and ``message``; any structured fields a caller passed through
    ``extra={...}`` are merged in. Exceptions are formatted into an
    ``exc_info`` string so a traceback stays on one JSON line.

    **Redaction.** Before serialisation, every structured field whose name
    :func:`is_sensitive_key` recognises has its value replaced with
    :data:`REDACTED`, recursively through dicts, lists and tuples. The stdlib's
    ``extra=`` argument is precisely the mechanism by which credentials
    *accidentally* reach a log — ``logger.info("auth", extra={"api_key": k})``
    needs no mistake anywhere, it just attaches a field the operator will
    later want, and the credential goes to stdout and every aggregator
    downstream. Without this layer the log line is the leak.

    Two deliberate boundaries:

    * The free-text ``message`` is **not** scanned. Pattern-matching prose
      produces false positives on ordinary English and gives no real guarantee;
      the reliable signal is a structured field *name*.
    * Only *known* names are masked. See :data:`SENSITIVE_KEY_NAMES` for why a
      permissive substring rule was rejected instead of shipped.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record`` to a one-line JSON string, redacting secrets."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            if isinstance(key, str) and is_sensitive_key(key):
                payload[key] = REDACTED
                continue
            payload[key] = redact_value(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exc_info"] = record.exc_text
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # ``default=str`` keeps an unexpected non-JSON-serialisable extra from
        # breaking the log line (and, transitively, the request that logged it).
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: int | str | None = None,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> logging.Logger:
    """Attach a single JSON handler to the ``validsim`` logger.

    Idempotent: calling it repeatedly leaves exactly one ValidSim handler
    installed and does not raise, so it is safe to invoke from every
    :func:`~validsim.api.main.create_app` call. Pass ``force=True`` to rebuild
    the handler (e.g. to change the output stream) even when already configured.

    Args:
        level: Desired level as an int or name; ``None`` defers to
            :data:`LOG_LEVEL_ENV` then :data:`DEFAULT_LEVEL`.
        stream: Output stream for the handler (defaults to ``sys.stdout``).
        force: Reconfigure even if a previous call already set things up.

    Returns:
        The configured ``validsim`` :class:`logging.Logger`.
    """
    logger = logging.getLogger(LOGGER_NAME)
    resolved = _resolve_level(level)

    if getattr(logger, _CONFIGURED_MARKER, False) and not force:
        # Already wired up: honour a level change but keep the single handler.
        logger.setLevel(resolved)
        return logger

    # Drop any handler a prior (possibly forced) call installed, then add ours,
    # so the count of ValidSim JSON handlers is always exactly one.
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    handler.setLevel(resolved)
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.setLevel(resolved)
    # We own output for this subtree; do not also bubble to the root logger,
    # which would double-log under a default-configured host process.
    logger.propagate = False
    setattr(logger, _CONFIGURED_MARKER, True)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under ``validsim``.

    Bare names (``"api"``) become ``"validsim.api"`` so every ValidSim record is
    captured by the handler installed in :func:`configure_logging`; a name that
    already starts with ``validsim`` is used verbatim. Configuration is ensured
    lazily so the helper is usable before :func:`create_app` runs.
    """
    configure_logging()
    if name == LOGGER_NAME or name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
