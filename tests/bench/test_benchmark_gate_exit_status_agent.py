"""The nightly benchmark gate must be able to fail the run.

Motivation
----------
``tests/bench/conftest.py`` detects benchmark regressions, but it used to report
them by incrementing ``terminalreporter._session.testsfailed`` from
``pytest_terminal_summary``. That bookkeeping could never reach the process exit
code. pytest fixes the exit status in ``_pytest.main._main``, which runs *before*
any ``pytest_terminal_summary`` hook fires: TerminalReporter's own
``pytest_sessionfinish`` wrapper calls ``pytest_terminal_summary`` from inside
itself, and ``_main`` has already returned by then. So the nightly printed
``REGRESSION ...`` and exited 0 -- a green build that measured nothing.

Method
------
Two layers, neither of which needs the benchmark suite, a subprocess, or the
timed tier:

* **Behavioural** - both hooks are called directly against lightweight
  stand-ins for the session and the reporter. This exercises the real signalling
  path in milliseconds, which is what makes the property testable at all under
  a RAM ceiling that forbids running the benchmark suite.
* **Structural** (AST) - the exit-status assignment must live in
  ``pytest_sessionfinish`` and must NOT appear in ``pytest_terminal_summary``,
  and no code anywhere in the module may mutate ``testsfailed`` again. The
  behavioural tests alone would not catch a future refactor that moved the
  signalling behind a helper invoked from the wrong hook.

These tests assert *where the verdict is signalled*, not that any particular
benchmark regressed: they need no baseline, no timing, and no network.

``baseline.json`` is redirected to a tmp path for every test, so a bug in the
merged ``pytest_sessionfinish`` can never overwrite the committed baseline.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from . import conftest as bench_conftest

_CONFTEST_PATH = Path(bench_conftest.__file__)

_COMPARE_ON = {"validsim_benchmark_compare": True}


@pytest.fixture(autouse=True)
def _isolate_session_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Give each test private ``_RESULTS`` / ``_REGRESSIONS`` / ``BASELINE_PATH``.

    ``conftest`` accumulates results in module globals. Without this, a test that
    records a fake measurement would leak into the next test and could be
    written into a real baseline. Autouse so no test can forget it.
    """
    monkeypatch.setattr(bench_conftest, "_RESULTS", {})
    monkeypatch.setattr(bench_conftest, "_REGRESSIONS", None)
    monkeypatch.setattr(bench_conftest, "BASELINE_PATH", tmp_path / "baseline.json")
    yield


class _FakeConfig:
    """Minimal stand-in for ``pytest.Config`` exposing only ``getoption``."""

    def __init__(self, **options: bool) -> None:
        self._options = options

    def getoption(self, name: str) -> bool:
        return self._options.get(name, False)


class _FakeSession:
    """Minimal stand-in for ``pytest.Session``."""

    def __init__(self, exitstatus: int = 0, **options: bool) -> None:
        self.config = _FakeConfig(**options)
        self.exitstatus = exitstatus
        # pytest.Session always carries these; mirroring them is what lets a
        # test assert the hook left them untouched.
        self.testsfailed = 0


class _FakeReporter:
    """Minimal stand-in for ``TerminalReporter``, recording what operators see."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self.seps: list[tuple[str, str]] = []
        self.lines: list[str] = []

    def write_sep(self, sep: str, title: str, **_kwargs: Any) -> None:
        self.seps.append((sep, title))

    def write_line(self, line: str, **_kwargs: Any) -> None:
        self.lines.append(line)


def _regress(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make the gate observe exactly ``names`` as regressions."""
    monkeypatch.setattr(bench_conftest, "_RESULTS", dict.fromkeys(names, 1.0))
    monkeypatch.setattr(
        bench_conftest,
        "compare_to_baseline",
        lambda: [f"{n}: 100.0ms vs baseline 10.0ms (+90.0ms, +900%)" for n in names],
    )


# --------------------------------------------------------------------------
# Behavioural: the exit status is actually set, and only when it should be.
# --------------------------------------------------------------------------


def test_a_regression_makes_the_session_exit_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the gate: a regression must change the exit status."""
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession(**_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert int(session.exitstatus) != 0


def test_a_clean_run_leaves_the_exit_status_untouched() -> None:
    """No regressions must not fail the build -- an always-red gate gets ignored."""
    session = _FakeSession(**_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == 0


def test_a_pre_existing_failure_is_not_overwritten_back_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression may only escalate the status, never mask a real test failure."""
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession(exitstatus=pytest.ExitCode.USAGE_ERROR, **_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_nothing_is_signalled_when_the_compare_flag_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--validsim-benchmark-compare`` the gate must stay inert.

    A plain ``pytest tests/bench`` run skips every benchmark, so any residual
    measurement would otherwise be compared against a baseline the operator
    never asked to check.
    """
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession()

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == 0


# --------------------------------------------------------------------------
# Behavioural: reporting stays, and stays side-effect free.
# --------------------------------------------------------------------------


def test_regressions_are_still_named_for_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators must still see WHICH benchmark regressed, not just a red build."""
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    assert any(line.startswith("REGRESSION ") and "slow_thing" in line for line in reporter.lines)
    assert ("=", "benchmark regression gate") in reporter.seps


def test_the_terminal_summary_hook_signals_nothing_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Printing must not mutate the session -- that was the original defect.

    ``testsfailed`` is reachable here because the fake reporter mirrors the real
    ``TerminalReporter._session`` attribute; incrementing it is precisely the
    dead bookkeeping that made this gate report green.
    """
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    assert session.exitstatus == 0
    assert session.testsfailed == 0


def test_a_clean_run_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The passing branch must stay a visible all-clear, not silence."""
    _regress(monkeypatch)  # compare_to_baseline -> []
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    assert "all benchmarks within tolerance" in reporter.lines
    assert not any(line.startswith("REGRESSION ") for line in reporter.lines)


# --------------------------------------------------------------------------
# The merge must not have cost us baseline saving.
# --------------------------------------------------------------------------


def test_the_baseline_is_still_written_by_the_merged_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Baseline saving moved into the same function; prove it survived.

    Guards the shadowing trap: a second ``pytest_sessionfinish`` definition
    would silently disable this entirely.
    """
    monkeypatch.setattr(bench_conftest, "_RESULTS", {"a": 1.234, "b": 5.678})
    session = _FakeSession(validsim_benchmark_save=True)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    written = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert written["benchmarks"] == {"a": 1.23, "b": 5.68}


def test_a_regression_does_not_prevent_the_baseline_being_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate must not early-return past the save when both flags are set."""
    _regress(monkeypatch, "slow_thing")
    session = _FakeSession(validsim_benchmark_save=True, **_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert (tmp_path / "baseline.json").exists()


def test_the_regression_verdict_is_computed_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both hooks must agree, rather than each deriving its own verdict.

    ``_RESULTS`` is mutable module state; recomputing per hook risks the printed
    list and the exit-status decision describing different sets of benchmarks.
    """
    calls: list[int] = []
    real = bench_conftest.compare_to_baseline

    def counting() -> list[str]:
        calls.append(1)
        return real()

    _regress(monkeypatch, "slow_thing")
    monkeypatch.setattr(bench_conftest, "compare_to_baseline", counting)
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)
    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    assert len(calls) == 1
    assert any(line.startswith("REGRESSION ") for line in reporter.lines)


# --------------------------------------------------------------------------
# Structural: the invariant survives a refactor that the fakes would miss.
# --------------------------------------------------------------------------


def _hooks(name: str) -> list[ast.FunctionDef]:
    tree = ast.parse(_CONFTEST_PATH.read_text(encoding="utf-8"))
    return [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == name
    ]


def _single_hook(name: str) -> ast.FunctionDef:
    found = _hooks(name)
    assert len(found) == 1, f"expected exactly one {name} definition, found {len(found)}"
    return found[0]


def _assignment_targets(node: ast.AST) -> set[str]:
    """Attribute names assigned to anywhere inside ``node``."""
    targets: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            targets.update(t.id for t in n.targets if isinstance(t, ast.Name))
            targets.update(
                t.attr for t in n.targets if isinstance(t, ast.Attribute)
            )
        elif isinstance(n, ast.AugAssign):
            if isinstance(n.target, ast.Attribute):
                targets.add(n.target.attr)
            elif isinstance(n.target, ast.Name):
                targets.add(n.target.id)
    return targets


def test_each_hook_is_defined_exactly_once() -> None:
    """Two ``pytest_sessionfinish`` defs would shadow; the second wins silently."""
    for name in ("pytest_sessionfinish", "pytest_terminal_summary"):
        assert len(_hooks(name)) == 1


def test_exit_status_is_assigned_in_sessionfinish() -> None:
    """The load-bearing line must exist in the hook pytest reads back."""
    assert "exitstatus" in _assignment_targets(_single_hook("pytest_sessionfinish"))


def test_the_terminal_summary_hook_assigns_no_exit_status() -> None:
    """Printing must never again be the thing that decides the exit code.

    Scoped to exit-status targets rather than "assigns nothing": the hook
    legitimately binds a local ``failures``, which is reporting, not signalling.
    """
    assigned = _assignment_targets(_single_hook("pytest_terminal_summary"))
    assert assigned.isdisjoint({"exitstatus", "testsfailed"})


def test_nothing_anywhere_mutates_testsfailed() -> None:
    """``testsfailed`` is dead bookkeeping after the exit status is decided.

    It is also a lie to the operator: it inflates the reported failure count
    with benchmarks that never ran as tests. Any future write to it should fail
    this test rather than pass review.
    """
    tree = ast.parse(_CONFTEST_PATH.read_text(encoding="utf-8"))
    offenders = sorted(
        {
            n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, (ast.Attribute, ast.Name))
            and (n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id)
            == "testsfailed"
        }
        | set(_assignment_targets(tree) & {"testsfailed"})
    )
    assert offenders == []


# --------------------------------------------------------------------------
# A gate that cannot measure must not report green.
#
# ``baseline.json`` is UNTRACKED, so on a fresh checkout -- and today on this
# very repo -- the compare gate had no baseline to read. It returned ``[]``,
# ``pytest_sessionfinish`` saw an empty verdict, and the nightly printed
# "all benchmarks within tolerance" and exited 0 after taking measurements and
# checking none of them. A missing signal read as a clean one.
# --------------------------------------------------------------------------

def _with_results(monkeypatch: pytest.MonkeyPatch, **measurements: float) -> None:
    """Record measurements WITHOUT stubbing ``compare_to_baseline``.

    ``_regress`` replaces the function outright, which would hide the very
    behaviour under test. These tests exercise the real implementation.
    """
    monkeypatch.setattr(bench_conftest, "_RESULTS", dict(measurements))


def _write_baseline(tmp_path: Path, **benchmarks: float) -> None:
    (tmp_path / "baseline.json").write_text(
        json.dumps({"benchmarks": benchmarks}), encoding="utf-8"
    )


def test_a_missing_baseline_is_a_failure_not_an_empty_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect itself: measurements taken, nothing to check them against."""
    assert not (tmp_path / "baseline.json").exists(), "fixture must start with no baseline"
    _with_results(monkeypatch, some_benchmark=500.0)

    failures = bench_conftest.compare_to_baseline()

    assert len(failures) == 1, failures
    assert bench_conftest.MISSING_BASELINE_LABEL in failures[0]


def test_a_missing_baseline_makes_the_session_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmeasurable gate must exit non-zero, the same as a real regression."""
    _with_results(monkeypatch, some_benchmark=500.0)
    session = _FakeSession(**_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_a_missing_baseline_is_named_for_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A red build with no explanation is as useless as a green one."""
    _with_results(monkeypatch, some_benchmark=500.0)
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    assert any(bench_conftest.MISSING_BASELINE_LABEL in line for line in reporter.lines)
    assert not any(line == "all benchmarks within tolerance" for line in reporter.lines)


def test_the_printed_verdict_and_the_exit_status_cannot_disagree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both hooks must report the SAME thing about an absent baseline.

    This is the trap the fix has to respect: the verdict is memoised and read by
    two hooks. If the missing-baseline case were computed independently in each,
    the operator could be shown one list while the exit code reflected another.
    """
    _with_results(monkeypatch, some_benchmark=500.0)
    session = _FakeSession(**_COMPARE_ON)
    reporter = _FakeReporter(session)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)
    bench_conftest.pytest_terminal_summary(
        terminalreporter=reporter, exitstatus=0, config=session.config
    )

    memoised = bench_conftest._REGRESSIONS
    assert memoised, "the verdict must be memoised, not recomputed per hook"
    printed = [ln for ln in reporter.lines if ln.startswith("REGRESSION ")]
    assert len(printed) == 1
    assert printed[0] == f"REGRESSION {memoised[0]}"
    # And the exit status was decided from that same non-empty memo.
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_nothing_measured_with_no_baseline_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: a run that measured nothing has nothing to complain about.

    Without this, every plain ``pytest`` run in a fresh checkout would go red for
    a condition it never asked to check -- which is how gates get ignored.
    """
    _with_results(monkeypatch)
    session = _FakeSession(**_COMPARE_ON)

    bench_conftest.pytest_sessionfinish(session=session, exitstatus=0)

    assert bench_conftest.compare_to_baseline() == []
    assert session.exitstatus == 0


def test_a_present_baseline_still_reports_only_real_regressions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fix must not make the healthy path noisy or the lossy path quiet."""
    _write_baseline(tmp_path, fast=10.0, regressed=10.0)

    _with_results(monkeypatch, fast=12.0, regressed=900.0)
    failures = bench_conftest.compare_to_baseline()

    assert len(failures) == 1, failures
    assert failures[0].startswith("regressed:"), failures


def _calls(fn: ast.FunctionDef, name: str) -> int:
    return sum(
        1
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    )


def test_the_no_baseline_verdict_flows_through_the_memoised_path() -> None:
    """Structural: one memo, one verdict, two consumers.

    Guards the decision documented in ``_benchmark_regressions``: the
    missing-baseline verdict is produced by ``compare_to_baseline`` (not
    separately in each hook) and reaches both hooks via ``_benchmark_regressions``.
    A bypass here is where the printed list and the exit status could drift apart.
    """
    assert _calls(_single_hook("compare_to_baseline"), "_missing_baseline_failure") == 1
    # sessionfinish must read the memo, never the raw comparator.
    assert _calls(_single_hook("pytest_sessionfinish"), "_benchmark_regressions") == 1
    assert _calls(_single_hook("pytest_sessionfinish"), "compare_to_baseline") == 0
    assert _calls(_single_hook("pytest_terminal_summary"), "_benchmark_regressions") == 1
    assert _calls(_single_hook("pytest_terminal_summary"), "compare_to_baseline") == 0