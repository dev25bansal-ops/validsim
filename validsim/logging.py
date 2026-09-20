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
    "configure_logging",
    "get_logger",
    "LOGGER_NAME",
    "LOG_LEVEL_ENV",
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
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record`` to a one-line JSON string."""
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
            payload[key] = value
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
