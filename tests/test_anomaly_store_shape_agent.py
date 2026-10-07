"""The store's *production* record shape is the one shape ``detect_anomalies`` never sees.

The gap this pins
-----------------
:func:`validsim.engine.anomaly.detect_anomalies` documents its input schema as
``{"run_id", "total_episodes", "failure_taxonomy"}``, and all 18 pre-existing
tests in ``test_anomaly.py`` build their records through a ``_run()`` helper that
always sets ``total_episodes``. So ``total_episodes`` is the only denominator the
anomaly suite ever exercises.

``total_episodes`` is not what the store serialises.
:meth:`validsim.store.memory.StoredRun.summary` -- the compact per-run record the
store and the list/dashboard endpoints hand to every consumer -- emits
``episode_count`` and carries no ``total_episodes`` and no ``failure_taxonomy``.
The shape the detector actually receives from production is therefore::

    {"run_id", "checkpoint_id", "task_id", "created_at", "baseline_run_id",
     "composite_score", "deploy_decision", "episode_count"}

which makes the ``episode_count`` fallback in ``_total`` not an edge case but
*the* branch -- and it is the one branch of ``_total`` no test reaches.

Why the gap is dangerous rather than benign
-------------------------------------------
``_total`` returns 0 when it finds no denominator, and ``detect_anomalies``
short-circuits on ``_total(current) <= 0``. A record it cannot size is therefore
indistinguishable from an *empty run*, and the detector returns ``[]`` silently:
no exception, no log line, no partial report.

That makes the fallback a single line of code whose deletion is invisible to the
suite. Deleting it flips every production-shaped history from "1 critical
anomaly" to "no findings" while every existing test still passes, because every
existing test uses ``total_episodes``. Verified by monkeypatching a
fallback-less ``_total`` over the live module: ``detect_anomalies`` then returned
0 anomalies for a history that really contains a 10x collision-rate spike, and
the pre-existing anomaly tests were unaffected.

So the tests below are anchored on the summary shape specifically: they are the
ones that fail if the fallback is removed.

Measured, not inferred
----------------------
Every expected number in this file was produced by running the detector (rates,
z-scores, sentinel values) rather than derived by hand. The ``StoredRun.summary()``
premise is itself asserted at runtime by
:func:`test_real_store_summary_carries_the_key_the_detector_falls_back_to`, so
this file cannot silently rot if ``summary()`` changes shape.

Reproduce::

    python -m pytest tests/test_anomaly_store_shape_agent.py -q
"""

from __future__ import annotations

import json
from typing import Any

from validsim.engine.anomaly import detect_anomalies
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.store.memory import StoredRun, ValidationStore

#: Keys :meth:`StoredRun.summary` emits. Duplicated here deliberately: the point
#: of the parity test below is to assert this list against the live object, so a
#: silent change to ``summary()`` is caught here rather than invalidating the
#: premise of every test in this file without anyone noticing.
SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "checkpoint_id",
        "task_id",
        "created_at",
        "baseline_run_id",
        "composite_score",
        "deploy_decision",
        "episode_count",
    }
)


def _summary_record(run_id: str, *, count: int, episodes: int = 100) -> dict[str, Any]:
    """A ``StoredRun.summary()``-shaped record plus the taxonomy the detector reads.

    ``total_episodes`` is deliberately absent, which is what forces ``_total``
    onto its ``episode_count`` fallback.
    """
    record: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "created_at": "2026-01-01T00:00:00+00:00",
        "baseline_run_id": None,
        "composite_score": 90.0,
        "deploy_decision": "APPROVE",
        "episode_count": episodes,
    }
    record["failure_taxonomy"] = {"collision": count}
    return record


def _spike_history() -> list[dict[str, Any]]:
    """7 baseline runs at a 5% collision rate, then one at 50%.

    Same arithmetic as the ``total_episodes`` tests in ``test_anomaly.py``, so
    the only variable between those tests and these is the denominator key.
    """
    history = [_summary_record(f"vrun-{i}", count=5) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=50))
    return history


# ---------------------------------------------------------------------------
# 1. The premise: does summary() really emit episode_count and not total_episodes?
# ---------------------------------------------------------------------------


def test_real_store_summary_carries_the_key_the_detector_falls_back_to() -> None:
    """Assert the premise of this file against the live ``summary()``.

    If ``summary()`` ever starts emitting ``total_episodes`` -- or drops
    ``episode_count`` -- the fallback stops being the production path and these
    tests would be guarding a shape nothing uses. Failing here says "revisit
    this file", which is the correct outcome.
    """
    run = StoredRun(
        run_id="vrun-shape01",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        created_at="2026-01-01T00:00:00+00:00",
        scorecard=_scorecard(),
        evaluation=EvaluationResult(
            total_episodes=100,
            success_count=95,
            success_rate=0.95,
            failure_taxonomy={"collision": 5},
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )
    summary = run.summary()

    assert set(summary) == SUMMARY_KEYS, (
        "StoredRun.summary() changed shape; the episode_count fallback is no "
        f"longer the production path this file tests. Got: {sorted(summary)}"
    )
    assert "total_episodes" not in summary, (
        "summary() now emits total_episodes, so the fallback branch is dead in "
        "production and these tests are guarding an unreachable shape"
    )
    assert summary["episode_count"] == 100


def _scorecard():
    from validsim.engine.scorecard import Scorecard

    return Scorecard(
        run_id="vrun-shape01",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.95,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 5},
    )


# ---------------------------------------------------------------------------
# 2. The headline: a spike in production shape is reported
# ---------------------------------------------------------------------------


def test_spike_in_summary_shape_is_detected() -> None:
    """A 10x collision-rate spike must be reported on summary-shaped records.

    This is the test that fails if the ``episode_count`` fallback is deleted.
    Without the fallback every record here reads as a 0-episode run and the
    detector returns ``[]`` -- silence where a critical finding belongs.
    """
    anomalies = detect_anomalies(_spike_history())

    assert len(anomalies) == 1, (
        "the production record shape produced no finding; the episode_count "
        f"fallback in _total is not doing its job (got {anomalies!r})"
    )
    finding = anomalies[0]
    assert finding.run_id == "vrun-8"
    assert finding.failure_mode == "collision"
    # 50/100 vs a 5/100 baseline: the rates must come from episode_count.
    assert finding.observed == 0.5
    assert finding.expected == 0.05
    assert finding.severity == "critical"
    assert finding.z_score == 20.6474


def test_summary_shape_ignores_a_genuine_improvement() -> None:
    """CONTROL: the fallback must not manufacture findings out of noise.

    A detector that reported everything would also pass the test above. Here the
    current run is *better* than the baseline, which is not a spike, so the
    correct answer on production shape is silence.
    """
    history = [_summary_record(f"vrun-{i}", count=50) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=5))

    assert detect_anomalies(history) == []


def test_summary_shape_reports_a_warning_band_spike() -> None:
    """The severity boundary must be reached through the fallback too.

    Uses the same 11/100 current count as the ``total_episodes`` warning test.
    Measured z on the summary shape is 2.753, inside ``[2.0, 4.0)``. Sharing the
    count keeps the two shapes directly comparable, so a future divergence in the
    fallback shows up as a severity or z mismatch rather than as a silently
    different verdict.
    """
    history = [_summary_record(f"vrun-{i}", count=5) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=11))

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].severity == "warning"
    assert anomalies[0].z_score == 2.753


def test_both_shapes_agree_on_the_same_numbers() -> None:
    """The two denominator keys must be interchangeable, not merely both present.

    Builds the same history twice -- once per key -- and requires identical
    output. This is the strongest form of the claim: the fallback is not a
    degraded approximation, it is the same computation.
    """
    from_total: list[dict[str, Any]] = []
    from_count: list[dict[str, Any]] = []
    for i in range(1, 8):
        base = _summary_record(f"vrun-{i}", count=5)
        total_form = dict(base, total_episodes=100)
        del total_form["episode_count"]
        from_total.append(total_form)
        from_count.append(dict(base))
    current_base = _summary_record("vrun-8", count=50)
    total_current = dict(current_base, total_episodes=100)
    del total_current["episode_count"]
    from_total.append(total_current)
    from_count.append(dict(current_base))

    assert detect_anomalies(from_total) == detect_anomalies(from_count)
    assert detect_anomalies(from_count), "the control itself must find the spike"


# ---------------------------------------------------------------------------
# 3. Precedence when both keys are present
# ---------------------------------------------------------------------------


def test_total_episodes_wins_when_both_keys_are_present() -> None:
    """``total_episodes`` takes precedence; ``episode_count`` is the fallback.

    The two are given *different* values so the assertion cannot pass by
    coincidence. With ``total_episodes=200`` the 50 failures are a 0.25 rate;
    with ``episode_count=100`` they would be 0.5. Measured observed rate on the
    both-present record is 0.25, i.e. ``total_episodes`` won.

    The precedence direction matters: it is what lets a caller that knows the
    true episode total override the summary field without the detector
    silently preferring the lossy one.
    """
    history = []
    for i in range(1, 8):
        record = _summary_record(f"vrun-{i}", count=5)
        record["total_episodes"] = 200
        history.append(record)
    current = _summary_record("vrun-8", count=50)
    current["total_episodes"] = 200
    history.append(current)

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].observed == 0.25, "total_episodes must take precedence"
    assert anomalies[0].expected == 0.025


# ---------------------------------------------------------------------------
# 4. The silent-degradation boundary: what "no denominator" costs
# ---------------------------------------------------------------------------


def test_zero_episode_count_is_no_evidence_not_a_crash() -> None:
    """``episode_count = 0`` means an empty run, so silence is the right answer.

    Distinct from the missing-key case below: here the record is *sized* and the
    size is zero. The fallback read it correctly rather than failing to find it.
    """
    history = [_summary_record(f"vrun-{i}", count=5, episodes=0) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=50, episodes=0))

    assert detect_anomalies(history) == []


def test_record_with_no_recognised_denominator_is_reported_as_empty() -> None:
    """Documents the silent failure this file exists to make visible.

    A record carrying neither key is treated as a 0-episode run, so the detector
    reports nothing -- no exception, no log line. Pinning the behaviour is the
    point: it is the reason the ``episode_count`` fallback is load-bearing. If
    this ever starts raising or returning a partial report, the risk it guards
    has changed and the fallback needs re-examining.
    """
    history = [
        {"run_id": f"vrun-{i}", "failure_taxonomy": {"collision": 5}} for i in range(1, 8)
    ]
    history.append({"run_id": "vrun-8", "failure_taxonomy": {"collision": 50}})

    assert detect_anomalies(history) == []


# ---------------------------------------------------------------------------
# 5. The other uncovered _total-region branch: a non-dict taxonomy
# ---------------------------------------------------------------------------


def test_non_dict_taxonomy_degrades_to_no_modes() -> None:
    """A taxonomy that is not a mapping must not raise mid-report.

    ``_taxonomy`` coerces a non-dict to ``{}``, which makes every failure mode
    invisible and the report empty. That is the documented tolerant contract
    shared by the rest of the engine, and it is a separate branch from the
    denominator fallback above.
    """
    for bad in (["collision"], "collision", 7, None):
        history = [
            _summary_record(f"vrun-{i}", count=5) for i in range(1, 8)
        ]
        history.append(_summary_record("vrun-8", count=50))
        for record in history:
            record["failure_taxonomy"] = bad  # type: ignore[assignment]

        assert detect_anomalies(history) == [], f"non-dict taxonomy {bad!r} misbehaved"


def test_non_dict_taxonomy_counts_still_degrade_to_zero_not_raised() -> None:
    """A dict carrying unparseable counts yields zeros rather than an exception.

    Counts route through ``as_int(default=0)``, so ``"abc"`` becomes 0 instead
    of raising. A zero count is a zero rate, which is well-formed input to the
    detector -- so the spike in the current run is still reported, with a
    baseline expectation of 0.
    """
    history = [_summary_record(f"vrun-{i}", count=5) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=50))
    for record in history[:-1]:
        record["failure_taxonomy"] = {"collision": "abc"}

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].failure_mode == "collision"
    assert anomalies[0].expected == 0.0
    assert anomalies[0].observed == 0.5
    assert anomalies[0].severity == "critical"


# ---------------------------------------------------------------------------
# 6. run_id fallback, also unreached by the total_episodes tests
# ---------------------------------------------------------------------------


def test_missing_run_id_falls_back_to_a_positional_placeholder() -> None:
    """A record with no usable ``run_id`` is still attributable to a position.

    ``run-7`` is the last element's index in an 8-record history. The
    placeholder keeps the finding reportable rather than dropping it, which is
    what lets an operator correlate it back to the history slice.
    """
    history = [_summary_record(f"vrun-{i}", count=5) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=50))
    for record in history:
        record["run_id"] = None

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].run_id == "run-7"


def test_non_string_run_id_is_coerced_to_str() -> None:
    """``str()`` coercion keeps the report JSON-serialisable.

    A store row keyed by an integer id is ordinary enough (Postgres sequence
    columns, hand-rolled imports) and must not put a bare ``int`` into a field
    every consumer treats as a string.
    """
    history = [_summary_record(str(i), count=5) for i in range(1, 8)]
    history.append(_summary_record("vrun-8", count=50))
    history[-1]["run_id"] = 999

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].run_id == "999"
    assert isinstance(anomalies[0].run_id, str)
    # And the whole finding still serialises.
    json.dumps([a.to_dict() for a in anomalies])


# ---------------------------------------------------------------------------
# 7. End-to-end: real StoredRuns, real summaries, real detector
# ---------------------------------------------------------------------------


def test_detector_consumes_real_stored_run_summaries() -> None:
    """Full path: build real runs, take real ``summary()``, detect a real spike.

    Nothing here is hand-shaped. The runs are persisted through a real
    ``ValidationStore``, read back through ``history()``, and their ``summary()``
    dicts are handed to the detector with only ``failure_taxonomy`` grafted on
    (``summary()`` does not carry it -- that is a separate observation, not a
    shape this file is entitled to assume).

    This is the test that would notice if the two representations drifted apart:
    it uses the real producer of the shape, not a copy of it.
    """
    store = ValidationStore()

    def _persist(run_id: str, created_at: str, collisions: int) -> None:
        scorecard = _scorecard()
        store.save(
            StoredRun(
                run_id=run_id,
                checkpoint_id=scorecard.checkpoint_id,
                task_id=scorecard.task_id,
                created_at=created_at,
                scorecard=scorecard,
                evaluation=EvaluationResult(
                    total_episodes=100,
                    success_count=100 - collisions,
                    success_rate=(100 - collisions) / 100,
                    failure_taxonomy={"collision": collisions},
                ),
                safety=SafetyResult(
                    float(collisions), 0.0, None, 0.0, scorecard.safety_score
                ),
            )
        )

    for i in range(1, 8):
        _persist(f"vrun-{i}", f"2026-01-{i:02d}T00:00:00+00:00", collisions=5)
    _persist("vrun-8", "2026-01-08T00:00:00+00:00", collisions=50)

    history = []
    for run in store.history():
        record = run.summary()
        # summary() carries no taxonomy; graft the evaluation's on so the
        # detector has failure modes to work with.
        record["failure_taxonomy"] = dict(run.evaluation.failure_taxonomy)
        history.append(record)

    # The premise, restated against live objects on the live path.
    assert all("total_episodes" not in record for record in history)
    assert all(record["episode_count"] == 100 for record in history)

    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1, (
        f"a real 10x collision spike was missed on real store summaries: {anomalies!r}"
    )
    assert anomalies[0].run_id == "vrun-8"
    assert anomalies[0].failure_mode == "collision"
    assert anomalies[0].observed == 0.5
    assert anomalies[0].expected == 0.05
    assert anomalies[0].severity == "critical"
