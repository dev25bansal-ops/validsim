"""Tests for the tolerant numeric coercion helpers in :mod:`validsim.engine._coerce`.

``as_float`` / ``as_int`` read numeric fields out of loosely-typed scorecard /
run mappings that may legitimately carry ``None``, stray strings or other
unexpected types. The whole point of the module is that it must *never raise*
on such input -- it degrades to the caller-supplied ``default`` instead of
blowing up mid-report. These tests pin that contract directly:

* happy path -- real ints / floats / bools / numeric strings coerce correctly;
* tolerance -- ``None`` and non-numeric strings fall back to ``default``;
* out-of-range -- extreme values are handled without raising;
* garbage -- any unparseable type returns ``default`` (never an exception);
* the ``default`` itself is honoured verbatim (including ``None``).

The two genuinely-overflowing inputs (``int(float('inf'))`` and
``float(10**1000)``, both ``OverflowError``) were previously *excluded* from the
"never raises" sweep on the grounds that the module only promised to guard
``TypeError`` / ``ValueError``. That exclusion was wrong: ``OverflowError`` is
not a programming error here, it is simply another shape of bad data, and it
escaped the documented contract all the way to a public entry point -- see
``TestOverflowIsAlsoTolerated`` below and the ``detect_anomalies`` crash pinned
in ``tests/test_anomaly.py``.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from validsim.engine._coerce import as_float, as_int

# ---------------------------------------------------------------------------
# Shared adversarial fixtures
# ---------------------------------------------------------------------------

#: Values that are *not* parseable as a number by either helper. Every one of
#: these must yield the caller's ``default`` and never raise.
UNPARSEABLE: list[Any] = [
    None,
    "",
    "   ",
    "abc",
    "not-a-number",
    "12abc",
    "nan-ish",
    [],
    [1, 2],
    {},
    {"composite": 91.0},
    (1, 2),
    set(),
    {1, 2, 3},
    object(),
    b"abc",  # non-numeric bytes: float()/int() raise ValueError -> default
    complex(1, 2),
]

#: Values both helpers accept as a *number* (bools/int/float). Used for the
#: "never raises" sweep and the bool-handling checks.
NUMERICISH: list[Any] = [True, False, 0, 1, -7, 42, 3.14, -2.5, 0.0, 1_000_000]


# ---------------------------------------------------------------------------
# as_float -- happy path
# ---------------------------------------------------------------------------


class TestAsFloatHappyPath:
    def test_int_becomes_float(self) -> None:
        assert as_float(42) == 42.0
        assert isinstance(as_float(42), float)

    def test_float_is_preserved(self) -> None:
        assert as_float(3.14) == 3.14

    def test_negative_float(self) -> None:
        assert as_float(-2.5) == -2.5

    def test_zero(self) -> None:
        assert as_float(0) == 0.0

    def test_numeric_string_parses(self) -> None:
        assert as_float("3.5") == 3.5

    def test_integer_string_parses(self) -> None:
        assert as_float("42") == 42.0

    def test_surrounding_whitespace_is_stripped(self) -> None:
        # ``float`` tolerates leading/trailing whitespace; the helper inherits it.
        assert as_float("  12.5  ") == 12.5


class TestAsFloatBools:
    def test_true_is_one(self) -> None:
        assert as_float(True) == 1.0

    def test_false_is_zero(self) -> None:
        assert as_float(False) == 0.0


# ---------------------------------------------------------------------------
# as_float -- None / default
# ---------------------------------------------------------------------------


class TestAsFloatNone:
    def test_none_returns_default_zero(self) -> None:
        assert as_float(None) == 0.0

    def test_none_returns_custom_default(self) -> None:
        assert as_float(None, default=-1.0) == -1.0

    def test_none_returns_none_default(self) -> None:
        # ``default=None`` lets callers distinguish "absent" from a real 0.0.
        assert as_float(None, default=None) is None


# ---------------------------------------------------------------------------
# as_float -- non-numeric / garbage -> default
# ---------------------------------------------------------------------------


class TestAsFloatGarbage:
    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_garbage_returns_default_zero(self, value: Any) -> None:
        assert as_float(value) == 0.0

    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_garbage_returns_custom_default(self, value: Any) -> None:
        assert as_float(value, default=99.0) == 99.0

    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_garbage_returns_none_default(self, value: Any) -> None:
        assert as_float(value, default=None) is None

    def test_default_is_returned_verbatim(self) -> None:
        # The exact object passed as ``default`` comes back untouched.
        sentinel = -0.5
        assert as_float("nope", default=sentinel) is sentinel


# ---------------------------------------------------------------------------
# as_float -- out-of-range / special floats (never raise)
# ---------------------------------------------------------------------------


class TestAsFloatOutOfRange:
    def test_overflow_string_becomes_inf(self) -> None:
        assert as_float("1e400") == math.inf

    def test_negative_overflow_string_becomes_neg_inf(self) -> None:
        assert as_float("-1e400") == -math.inf

    def test_inf_passes_through(self) -> None:
        assert as_float(math.inf) == math.inf

    def test_nan_passes_through(self) -> None:
        assert math.isnan(as_float(math.nan))

    def test_very_large_finite_string(self) -> None:
        assert as_float("9" * 40) == float("9" * 40)


# ---------------------------------------------------------------------------
# as_int -- happy path
# ---------------------------------------------------------------------------


class TestAsIntHappyPath:
    def test_int_is_preserved(self) -> None:
        assert as_int(42) == 42
        assert isinstance(as_int(42), int)

    def test_negative_int(self) -> None:
        assert as_int(-7) == -7

    def test_zero(self) -> None:
        assert as_int(0) == 0

    def test_integer_string_parses(self) -> None:
        assert as_int("42") == 42

    def test_whitespace_string_parses(self) -> None:
        assert as_int("  7 ") == 7

    def test_leading_zeros_string(self) -> None:
        assert as_int("007") == 7


class TestAsIntFloatTruncation:
    def test_positive_float_truncates_toward_zero(self) -> None:
        # ``int(3.9)`` truncates -- it does NOT round.
        assert as_int(3.9) == 3

    def test_negative_float_truncates_toward_zero(self) -> None:
        assert as_int(-3.9) == -3

    def test_whole_float(self) -> None:
        assert as_int(5.0) == 5

    def test_fractional_string_falls_back_to_default(self) -> None:
        # ``int("3.5")`` raises ValueError -> default (this is the documented
        # asymmetry vs. as_float, which accepts "3.5").
        assert as_int("3.5") == 0


class TestAsIntBools:
    def test_true_is_one(self) -> None:
        assert as_int(True) == 1

    def test_false_is_zero(self) -> None:
        assert as_int(False) == 0


# ---------------------------------------------------------------------------
# as_int -- None / default / garbage
# ---------------------------------------------------------------------------


class TestAsIntNone:
    def test_none_returns_default_zero(self) -> None:
        assert as_int(None) == 0

    def test_none_returns_custom_default(self) -> None:
        assert as_int(None, default=-1) == -1


class TestAsIntGarbage:
    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_garbage_returns_default_zero(self, value: Any) -> None:
        assert as_int(value) == 0

    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_garbage_returns_custom_default(self, value: Any) -> None:
        assert as_int(value, default=7) == 7

    def test_default_is_returned_verbatim(self) -> None:
        sentinel = -12345
        assert as_int("nope", default=sentinel) is sentinel


# ---------------------------------------------------------------------------
# as_int -- out-of-range / special floats (never raise)
# ---------------------------------------------------------------------------


class TestAsIntOutOfRange:
    def test_huge_integer_string(self) -> None:
        # Python ints are arbitrary precision, so a 50-digit string is fine.
        assert as_int("9" * 50) == int("9" * 50)

    def test_nan_falls_back_to_default(self) -> None:
        # ``int(nan)`` raises ValueError (caught) -> default, never Overflow.
        assert as_int(math.nan) == 0

    def test_scientific_string_falls_back_to_default(self) -> None:
        # ``int("1e400")`` raises ValueError -> default.
        assert as_int("1e400", default=-5) == -5


# ---------------------------------------------------------------------------
# Cross-cutting: the "never raises" guarantee
# ---------------------------------------------------------------------------


class TestNeverRaises:
    @pytest.mark.parametrize(
        "value", [*UNPARSEABLE, *NUMERICISH, "3.5", "1e400", math.nan, math.inf]
    )
    def test_as_float_never_raises(self, value: Any) -> None:
        # Whatever the input, as_float must return a float (or the default) --
        # it must never propagate an exception to the report renderer.
        result = as_float(value, default=None)
        assert result is None or isinstance(result, float)

    @pytest.mark.parametrize("value", [*UNPARSEABLE, *NUMERICISH, "3.5", "1e400", math.nan])
    def test_as_int_never_raises(self, value: Any) -> None:
        result = as_int(value, default=0)
        assert isinstance(result, int)

    @pytest.mark.parametrize("value", UNPARSEABLE)
    def test_both_helpers_tolerate_every_garbage_input(self, value: Any) -> None:
        # A single sweep proving neither helper explodes on any junk value.
        assert as_float(value, default=0.0) == 0.0
        assert as_int(value, default=0) == 0


# ---------------------------------------------------------------------------
# OverflowError is bad *data*, not a programming error
# ---------------------------------------------------------------------------

#: Inputs that overflow ``int()``. ``int(inf)`` raises ``OverflowError`` --
#: neither ``TypeError`` nor ``ValueError`` -- and therefore escaped the
#: helpers' except clause. Note ``int(10 ** 400)`` does *not* appear here:
#: Python ints are arbitrary precision, so that conversion succeeds and the
#: value must be preserved (see the control test below).
INT_OVERFLOWING: list[Any] = [math.inf, -math.inf, float("nan")]

#: Inputs that overflow ``float()``. ``float(10 ** 400)`` raises
#: ``OverflowError`` ("int too large to convert to float"). ``inf`` and
#: ``Decimal("1e400")`` are *not* here -- ``float(inf)`` succeeds and must
#: keep passing through.
FLOAT_OVERFLOWING: list[Any] = [10 ** 400]


class TestOverflowIsAlsoTolerated:
    @pytest.mark.parametrize("value", INT_OVERFLOWING)
    def test_as_int_overflow_returns_default(self, value: Any) -> None:
        # ``int(inf)`` -> OverflowError. An episode count of "infinity" is a
        # corrupt record, so the caller's default (0) is the correct degrade:
        # it makes the run look empty, which every reader already handles.
        assert as_int(value) == 0
        assert as_int(value, default=-1) == -1
        assert as_int(value, default=None) is None

    @pytest.mark.parametrize("value", FLOAT_OVERFLOWING)
    def test_as_float_overflow_returns_default(self, value: Any) -> None:
        # ``float(10 ** 400)`` -> OverflowError. The helper must swallow it
        # rather than letting a 400-digit integer blow up a report renderer.
        assert as_float(value) == 0.0
        assert as_float(value, default=99.0) == 99.0
        assert as_float(value, default=None) is None

    def test_overflow_inputs_join_the_never_raises_sweep(self) -> None:
        """The sweep itself must now cover them, not carve them out."""
        for value in INT_OVERFLOWING:
            assert isinstance(as_int(value, default=0), int)
        for value in FLOAT_OVERFLOWING:
            assert as_float(value, default=None) is None

    def test_infinity_is_still_parsed_by_as_float(self) -> None:
        """CONTROL: a float input that fits is returned, not defaulted.

        Only the *conversion overflow* is swallowed. ``as_float(math.inf)``
        succeeds and must keep returning ``inf`` -- the existing
        ``TestAsFloatOutOfRange`` contract is unchanged. A helper that
        defaulted ``inf`` would corrupt every legitimate infinity in a report.
        """
        assert as_float(math.inf) == math.inf
        assert as_float("-1e400") == -math.inf
        assert as_float(Decimal("1e400")) == math.inf

    def test_finite_large_int_still_parsed_by_as_int(self) -> None:
        """CONTROL: big ints are exact in Python and must not be defaulted.

        ``as_int("9" * 50)`` -- and even ``as_int(10 ** 400)`` -- are legal
        episode counts that convert cleanly. Only values that genuinely do not
        fit are degraded, so the fix cannot be mistaken for a range clamp.
        """
        assert as_int("9" * 50) == int("9" * 50)
        assert as_int(10 ** 20) == 10 ** 20
        assert as_int(10 ** 400) == 10 ** 400
