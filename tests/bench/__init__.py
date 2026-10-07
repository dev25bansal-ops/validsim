"""Performance benchmark suite for ValidSim.

Separate from the correctness suite in ``tests/`` and opt-in via markers so the
normal suite stays fast. Run with::

    # capture / refresh the committed baseline
    pytest tests/bench -m "benchmark and not slow" --validsim-benchmark-save

    # fail the build on a regression (ratio-based, see conftest.py)
    pytest tests/bench -m "benchmark and not slow" --validsim-benchmark-compare

    # the slow tier (max-episode runs, store growth) - nightly only
    pytest tests/bench -m slow --validsim-benchmark-compare

Every benchmark is **stdlib-only** (no ``pytest-benchmark`` dependency), so the
CI cost is a handful of seconds and no new wheels are needed. Results are
written to ``baseline.json`` and compared on subsequent runs.

If you prefer the real ``pytest-benchmark`` plugin, it is a drop-in replacement
for ``time_benchmark`` in ``conftest.py``; docs/PERFORMANCE.md has the
tradeoff table.
"""
