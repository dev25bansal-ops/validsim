"""Shared numeric coercion helpers for the validation engine.

Several engine modules (PDF rendering, head-to-head benchmarking, anomaly
detection and trend analysis) read numeric fields out of loosely-typed
scorecard / run dictionaries. Those mappings may legitimately carry ``None``,
stray strings or other unexpected types, and the engine must degrade
gracefully rather than raise in the middle of a report.

This module centralises that tolerant coercion so the behaviour is defined
once instead of reimplemented in every renderer. The semantics intentionally
match the most defensive of the previous local copies (the PDF float
coercion): a value is parsed with :func:`float` / :func:`int` and, on
``TypeError`` or ``ValueError`` -- which also covers ``None`` -- the
caller-supplied ``default`` is returned instead of raising.
"""

from __future__ import annotations

from typing import Any

__all__ = ["as_float", "as_int"]


def as_float(value: Any, default: float | None = 0.0) -> float | None:
    """Best-effort ``float`` coercion, returning ``default`` on bad input.

    Args:
        value: The value to coerce. ``None``, non-numeric strings and other
            bad types never raise.
        default: Value returned when ``value`` cannot be parsed as a float.
            Pass ``None`` to let callers distinguish "absent / unparseable"
            from a real ``0.0``.

    Returns:
        ``float(value)`` when parseable, otherwise ``default``.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    """Best-effort ``int`` coercion, returning ``default`` on bad input.

    Mirrors :func:`as_float` for integer fields (e.g. episode counts). The
    coercion uses :func:`int` directly -- so a fractional float truncates and
    a non-integer string such as ``"3.5"`` falls back to ``default`` -- which
    preserves the exact behaviour of the anomaly detector's episode-total
    reader that this helper replaced.

    Args:
        value: The value to coerce. ``None`` and non-numeric strings never
            raise.
        default: Value returned when ``value`` cannot be parsed as an int.

    Returns:
        ``int(value)`` when parseable, otherwise ``default``.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
