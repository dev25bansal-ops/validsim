"""Timing / memory helpers and the baseline-comparison gate for ``tests/bench``.

Design constraints:
* **stdlib only** - the repo ships no benchmark dependency, and adding one to
  the CI install line costs wheel-build time on every runner.
* **budget-aware** - each benchmark gets a wall-clock budget; a cheap operation
  keeps full statistical confidence, an expensive one is sampled the minimum
  number of times rather than blowing the CI budget.
* **ratio-based comparison, not absolute** - GitHub-hosted runners are 2-4x
  slower and noisy; an absolute millisecond budget is a flaky gate. We compare
  each measurement against the committed baseline and only fail when it
  regresses by more than ``REGRESSION_TOLERANCE`` *and* the absolute delta is
  large enough not to be noise.
* **no silent pass** - a gate that cannot measure must not report green. A
  missing ``baseline.json`` is an explicit, labelled failure under
  ``--validsim-benchmark-compare``, not a shrug that happens to look like a pass.
* **warmup + median** - the first call pays allocator costs; we discard warmups
  and compare medians, which is far more stable than means for a pure-Python
  hot loop.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

#: Where the committed baseline lives (next to this file).
BASELINE_PATH = Path(__file__).with_name("baseline.json")

#: A benchmark may regress by this fraction before it is reported.
REGRESSION_TOLERANCE = 0.25  # 25%

#: ...but a regression smaller than this many milliseconds is noise, not a
#: regression, so it never fails the gate regardless of ratio.
ABSOLUTE_FLOOR_MS = 20.0

#: Requested measured iterations per benchmark (warmups excluded).
DEFAULT_ROUNDS = 5

#: Number of discarded warmup iterations.
DEFAULT_WARMUP = 2

#: Wall-clock budget for a single benchmark.
PER_BENCHMARK_BUDGET_S = 3.0

#: Never take fewer than this many measured samples - below 3 the median is
#: not meaningful.
MIN_ROUNDS = 3

#: Collected results for this session, written by ``--benchmark-save``.
_RESULTS: dict[str, float] = {}

#: Prefix of the failure emitted when the compare gate has no baseline to check
#: against. A constant (not a formatted string) because the message is built at
#: call time -- ``_RESULTS`` keeps growing after import, so an f-string evaluated
#: at module scope would permanently report a count of zero. Tests match on this
#: prefix, so the gate and its tests agree on the wording by construction.
MISSING_BASELINE_LABEL = "BENCHMARK GATE CANNOT MEASURE: no baseline to compare against"


def _missing_baseline_failure() -> str:
    """The single failure message for "the gate has no baseline to compare to".

    Built per call, not at import: ``_RESULTS`` is mutable module state that is
    populated as benchmarks run, and the whole point of the message is to say how
    many measurements went unchecked.
    """
    return (
        f"{MISSING_BASELINE_LABEL}: {BASELINE_PATH} does not exist, so "
        f"{len(_RESULTS)} measurement(s) were taken and NONE of them could be "
        f"checked against a prior run. This is a green build that measured "
        f"nothing. Regenerate the baseline on a real runner with adequate RAM: "
        f"pytest tests/bench -m 'benchmark and not slow' --validsim-benchmark-save"
    )

#: Regressions found by ``pytest_sessionfinish``, shared with
#: ``pytest_terminal_summary`` so the verdict is computed exactly once. If both
#: hooks called ``compare_to_baseline()`` independently, the printed list and
#: the exit-status decision would be two independent verdicts derived from the
#: same mutable ``_RESULTS`` dict -- and could disagree.
_REGRESSIONS: list[str] | None = None


def record(name: str, median_ms: float) -> None:
    """Store a benchmark result for the session."""
    _RESULTS[name] = median_ms


def plan_rounds(
    single_s: float, rounds: int, warmup: int, budget_s: float
) -> tuple[int, int]:
    """Split ``budget_s`` into (warmup, measured) call counts for one call cost.

    Pure arithmetic -- no clock, no I/O -- so the budget policy is testable
    without sleeping. ``budget_s`` bounds the *total* wall clock (warmups
    included), so it is a ceiling on how many calls are affordable, not a floor
    under the measured count. The previous
    ``max(MIN_ROUNDS, min(rounds, budget // single))`` had no ceiling at all:
    when a single call already cost more than the whole budget it still forced
    ``MIN_ROUNDS`` measured calls *plus* the warmups, so an expensive benchmark
    overran by an unbounded factor. That is not hypothetical -- the 100k-episode
    bootstrap costs ~12s per call, ran five of them for a nominal 3s budget, and
    the "fast" tier never finished, which is why no ``baseline.json`` could be
    generated and the compare gate was permanently stuck at "cannot measure".

    ``MIN_ROUNDS`` is still honoured whenever it fits: warmups are sacrificed
    first (they only stabilise allocator/cache state, which matters far less
    than having a median at all), and only when the budget cannot fit
    ``MIN_ROUNDS`` measured samples does the count drop below it -- never below
    one, because a single sample is still a measurement.

    Args:
        single_s: Measured cost of one call, in seconds.
        rounds: Maximum measured samples wanted (``DEFAULT_ROUNDS``).
        warmup: Maximum discarded warmup calls wanted (``DEFAULT_WARMUP``).
        budget_s: Total wall-clock budget for this benchmark, in seconds.

    Returns:
        ``(warmup_calls, measured_rounds)``; both non-negative and
        ``measured_rounds >= 1``. Their sum never exceeds ``budget_s/single_s``
        (rounded up), except that at least one call is always taken so an
        over-budget benchmark is still measured once.
    """
    if single_s <= 0:  # pragma: no cover - a call cannot cost zero time
        return warmup, rounds
    total = max(1, int(budget_s / single_s))
    # Keep a warmup only while MIN_ROUNDS measured samples still fit alongside
    # it; otherwise spend the whole budget on measurement.
    warm = min(warmup, max(0, total - MIN_ROUNDS))
    measured = min(rounds, max(1, total - warm))
    return warm, measured


def compare_to_baseline() -> list[str]:
    """Return human-readable failure messages for regressions vs the baseline.

    An absent baseline is itself a failure, not an empty pass. Returning ``[]``
    here used to let ``--validsim-benchmark-compare`` report success on a run
    that took measurements and checked none of them -- the same "a missing signal
    read as a clean one" family this suite keeps guarding against. The caller
    (:func:`_benchmark_regressions`) still gates on the compare flag, so the gate
    stays inert on a plain ``pytest tests/bench`` run.
    """
    if not BASELINE_PATH.exists():
        if _RESULTS:
            return [_missing_baseline_failure()]
        # Nothing was measured, so there is nothing a baseline could have been
        # compared against. Reporting a failure here would make every plain
        # ``pytest`` run in a fresh checkout red for a condition it never asked
        # to check -- which is how gates learn to be ignored.
        return []
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("benchmarks", {})
    failures: list[str] = []
    for name, current in sorted(_RESULTS.items()):
        prior = baseline.get(name)
        if prior is None:
            # Known, deliberate non-failure. A benchmark missing from the baseline
            # is also unmeasurable, but escalating it here would be WRONG for this
            # repo: the nightly runs two jobs with different `-m` selections
            # ("benchmark and not slow", then "slow") against ONE baseline, and
            # the documented save command regenerates only the fast tier. Every
            # slow-tier benchmark would therefore be "absent from the baseline"
            # and turn the slow job red on a perfectly healthy tree. Making that
            # case loud needs a per-tier baseline, not a flag flip. Reported as a
            # residual instead.
            continue
        delta = current - prior
        if delta <= 0 or delta < ABSOLUTE_FLOOR_MS:
            continue
        # A zero prior means the baseline run measured below timer resolution
        # (``memstore_count_n*`` does: it is O(1) under a lock). The ratio is
        # then undefined and ``delta / prior`` raised ZeroDivisionError, turning
        # a measurable regression into a crashed gate -- the one outcome worse
        # than a missed regression, because the whole run dies and reports
        # nothing. Fall back to the absolute test that already admitted this
        # branch: with no ratio to normalise by, any increase over the noise
        # floor is a regression.
        if prior > 0 and delta / prior <= REGRESSION_TOLERANCE:
            continue
        # The percentage is only meaningful with a non-zero prior, so the
        # message must not divide either -- guarding the comparison above but
        # not this f-string would still crash on exactly the rows the guard
        # exists for.
        ratio = f"+{100 * delta / prior:.0f}%" if prior > 0 else "no baseline ratio"
        failures.append(
            f"{name}: {current:.1f}ms vs baseline {prior:.1f}ms "
            f"(+{delta:.1f}ms, {ratio})"
        )
    return failures


def pytest_addoption(parser: pytest.Parser) -> None:
    # Namespaced (``--validsim-benchmark-*``) because the ``pytest-benchmark``
    # plugin may also be installed in the environment and already owns the
    # un-prefixed ``--benchmark-save`` / ``--benchmark-compare`` names. Sharing
    # those strings aborts pytest at argument-parsing time (argparse raises
    # ``conflicting option string``), which would break *every* collection --
    # not just the benchmarks. Prefixing keeps both usable and collision-free.
    group = parser.getgroup("validsim-benchmark")
    group.addoption(
        "--validsim-benchmark-save",
        action="store_true",
        default=False,
        help="Write this run's measurements to tests/bench/baseline.json.",
    )
    group.addoption(
        "--validsim-benchmark-compare",
        action="store_true",
        default=False,
        help="Fail the run when a benchmark regresses beyond tolerance.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip benchmarks unless one of the benchmark flags is passed."""
    if config.getoption("validsim_benchmark_save") or config.getoption(
        "validsim_benchmark_compare"
    ):
        return
    skip = pytest.mark.skip(
        reason="benchmark (pass --validsim-benchmark-compare or --validsim-benchmark-save)"
    )
    for item in items:
        if "benchmark" in item.keywords:
            item.add_marker(skip)


def _benchmark_regressions(config: pytest.Config) -> list[str]:
    """Return this run's regressions, computing them at most once per session.

    Memoised in :data:`_REGRESSIONS` because the verdict is needed by two hooks
    that run at different points in shutdown. ``compare_to_baseline`` is a pure
    function of ``_RESULTS`` and the on-disk baseline, but ``_RESULTS`` is
    module-global and mutable, so recomputing risks the printed list and the
    exit-status decision describing different sets of benchmarks.

    The "no baseline" verdict flows THROUGH this memo, deliberately, rather than
    bypassing it. A bypass would mean the printed list and the exit-status
    decision came from two different computations -- exactly the disagreement this
    function exists to prevent -- and a bypass is also where a future refactor
    would be tempted to reintroduce ``testsfailed`` as a shortcut. One memo, one
    verdict, two consumers: ``pytest_sessionfinish`` turns a non-empty result into
    a non-zero exit status, ``pytest_terminal_summary`` only prints it.
    """
    global _REGRESSIONS
    if _REGRESSIONS is None:
        _REGRESSIONS = compare_to_baseline() if config.getoption(
            "validsim_benchmark_compare"
        ) else []
    return _REGRESSIONS


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # --- exit-code signalling: MUST happen here, not in pytest_terminal_summary.
    # pytest fixes the process exit status in _pytest.main._main, which runs
    # BEFORE any pytest_terminal_summary hook (TerminalReporter.pytest_sessionfinish
    # calls pytest_terminal_summary *from inside* its own sessionfinish wrapper,
    # and _main already returned by then). Bumping testsfailed from there is
    # therefore pure bookkeeping that can no longer reach the exit code: the run
    # printed "REGRESSION" and still exited 0. Setting session.exitstatus here is
    # read back by _pytest.main.wrap_session, which returns it as the process exit
    # code -- and because TerminalReporter's sessionfinish is a hookimpl wrapper,
    # mutating it after `yield` is the supported place to do it.
    if _benchmark_regressions(session.config):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

    # --- baseline saving (unchanged behaviour).
    if not session.config.getoption("validsim_benchmark_save") or not _RESULTS:
        return
    payload = {
        "generated_by": "pytest tests/bench -m benchmark --validsim-benchmark-save",
        "benchmarks": {k: round(v, 2) for k, v in sorted(_RESULTS.items())},
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {BASELINE_PATH} ({len(_RESULTS)} benchmarks)")


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """Human-readable reporting ONLY.

    Deliberately contains no exit-status signalling: see the comment in
    ``pytest_sessionfinish``. It still prints the same ``REGRESSION ...`` lines
    the nightly workflow greps for, so the operator-facing output is unchanged.
    """
    if not config.getoption("validsim_benchmark_compare"):
        return
    failures = _benchmark_regressions(config)
    terminalreporter.write_sep("=", "benchmark regression gate")
    if failures:
        for f in failures:
            terminalreporter.write_line(f"REGRESSION {f}", red=True)
    else:
        terminalreporter.write_line("all benchmarks within tolerance", green=True)


def time_benchmark(
    name: str,
    func: Callable[[], Any],
    rounds: int = DEFAULT_ROUNDS,
    warmup: int = DEFAULT_WARMUP,
) -> float:
    """Time ``func``, record the median, and return it in milliseconds.

    ``name`` is the stable key written to ``baseline.json``; keep it descriptive
    and stable across refactors (e.g. ``bootstrap_ci_n10000_resamples500``).

    The first call doubles as the cost probe: :func:`plan_rounds` turns its
    duration into a (warmup, measured) split that fits
    :data:`PER_BENCHMARK_BUDGET_S` in total, so an expensive benchmark is
    sampled once instead of overrunning the budget by a multiple.
    """
    probe_start = time.perf_counter()
    func()
    single_s = time.perf_counter() - probe_start
    warm, measured = plan_rounds(single_s, rounds, warmup, PER_BENCHMARK_BUDGET_S)
    for _ in range(warm):
        func()
    samples: list[float] = []
    for _ in range(measured):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000.0)
    median_ms = statistics.median(samples)
    record(name, median_ms)
    print(
        f"[bench] {name}: median {median_ms:.2f}ms "
        f"(min {min(samples):.2f} / max {max(samples):.2f}, "
        f"{measured} rounds, {warm} warmup)"
    )
    return median_ms


def peak_rss_bytes() -> int:
    """Peak resident set size in bytes (stdlib; psutil is not a dependency)."""
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        p = _PMC()
        p.cb = ctypes.sizeof(p)
        ctypes.WinDLL("psapi").GetProcessMemoryInfo(
            ctypes.WinDLL("kernel32").GetCurrentProcess(), ctypes.byref(p), p.cb
        )
        return int(p.PeakWorkingSetSize)
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
