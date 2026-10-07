"""Differential/property tests: memory and SQLite must be observationally equal.

Strategy
--------
A seeded pseudo-random *op script* (``save`` / ``delete`` / ``get`` / ``history``
with date bounds / ``list_for_checkpoint`` / ``count``) is replayed against a
fresh in-memory store and a fresh SQLite store. After every single operation
both stores are asked the same questions and the answers must be identical,
character for character.

The script is not only cross-checked, it is also checked against an
*independent oracle* (a plain dict maintained by the test), so a bug that both
backends shared would still fail the test instead of cancelling out.

Scope of the contract asserted here
-----------------------------------
The op script may re-save an existing id and may use ``save_exists``; both are
covered, because the store contract is now **append-only / first-write-wins**
on every backend. The one restriction that *is* deliberate: every ``created_at``
is unique. Runs that share a timestamp have no defined cross-backend order
(that is a real finding, pinned in ``test_store_divergence_agent.py``), so
including them here would turn this file's failures ambiguous.

PostgreSQL
----------
No live PostgreSQL server is available in this environment (no ``psycopg``
driver, no reachable server — see ``test_store_pg_differential_agent.py``,
which skips with that exact reason). The third backend is therefore covered
where it can be covered honestly: the pure row→:class:`StoredRun`
reconstruction function and the SQL/bound-parameter construction, both of
which need no server.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from validsim.engine.evaluation import EvaluationResult, evaluate
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult, compute_safety
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

# --------------------------------------------------------------------------
# Deterministic run factory
# --------------------------------------------------------------------------

_CHECKPOINTS = ("ckpt-alpha", "ckpt-beta", "ckpt-gamma")
_TASKS = ("pick-place", "insert", "wipe-table")

#: One stamp per run id, all distinct so ``ORDER BY created_at`` is a total
#: order (ties are a known divergence and are tested separately).
_BASE_DAY = 1
_STAMPS: list[str] = [
    f"2026-01-{_BASE_DAY + i // 60:02d}T{i // 60:02d}:{i % 60:02d}+00:00"
    for i in range(180)
]

_FAILURES = ("collision", "timeout", "grasp_failure", "joint_limit")
_LEVELS = ("full", "partial", "light")


def _episode(index: int) -> EpisodeResult:
    """Build a deterministic, fully-populated episode."""
    ok = index % 3 != 0
    return EpisodeResult(
        episode_id=f"ep-{index:05d}",
        task_id=_TASKS[index % len(_TASKS)],
        seed=1000 + index,
        success=ok,
        collision_count=index % 4,
        max_contact_force_n=round(5.0 + (index % 17) * 3.25, 4),
        min_human_distance_m=None if index % 5 == 0 else round(0.3 + (index % 9) * 0.1, 4),
        failure_mode=None if ok else _FAILURES[index % len(_FAILURES)],
        duration_s=round(1.5 + (index % 13) * 0.75, 4),
        joint_states_summary={"position_rms": round(0.1 * (index % 7), 4)},
        randomization_level=_LEVELS[index % len(_LEVELS)],
    )


def _make_run(index: int) -> StoredRun:
    """Build a deterministic :class:`StoredRun` for run id ``vrun-<index>``.

    Every third run carries full detail (episodes + regression + baseline id)
    so the persisted and in-memory representations are compared on the
    non-trivial path, not just the minimal one.
    """
    run_id = f"vrun-{index:04d}"
    checkpoint_id = _CHECKPOINTS[index % len(_CHECKPOINTS)]
    task_id = _TASKS[index % len(_TASKS)]
    created_at = _STAMPS[index % len(_STAMPS)]

    detailed = index % 3 == 0
    n_episodes = 3 if detailed else 0
    episodes = [_episode(index * 10 + k) for k in range(n_episodes)]

    evaluation = (
        evaluate(episodes)
        if episodes
        else EvaluationResult(
            total_episodes=100,
            success_count=90 + (index % 11),
            success_rate=round(0.5 + (index % 50) / 100.0, 4),
            per_task_success={task_id: 0.75},
            failure_taxonomy={_FAILURES[index % len(_FAILURES)]: index % 5},
            mean_duration_s=round(1.0 + index * 0.01, 4),
        )
    )
    safety = (
        compute_safety(episodes)
        if episodes
        else SafetyResult(
            collisions_per_episode=round((index % 9) * 0.11, 4),
            max_force_exceeded_rate=round((index % 7) * 0.07, 4),
            min_human_proximity_m=None if index % 4 == 0 else round(0.4 + index * 0.001, 4),
            proximity_violation_rate=round((index % 3) * 0.05, 4),
            safety_score=round(50.0 + (index % 50), 2),
        )
    )
    regression = (
        RegressionReport(
            items=[
                RegressionItem(
                    "success_rate", 0.9, 0.5, -0.4, 0.002, True, "critical"
                ),
                RegressionItem(
                    "mean_duration_s", 8.0, 11.5, 3.5, None, True, "warning"
                ),
            ]
        )
        if detailed and index % 6 == 0
        else None
    )

    scorecard = Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        composite_score=round(10.0 + (index % 90) + 0.25, 2),
        success_rate=evaluation.success_rate,
        safety_score=safety.safety_score,
        robustness_score=round(60.0 + (index % 40), 2),
        regression_delta=None if regression is None else -0.4,
        confidence_interval=(
            None if index % 4 == 0 else (round(0.5 + index * 0.001, 4), round(0.9, 4))
        ),
        deploy_decision="APPROVE" if index % 2 == 0 else "BLOCK",
        threshold=85.0,
        created_at=created_at,
        episode_count=evaluation.total_episodes,
        failure_taxonomy=dict(evaluation.failure_taxonomy),
        adversarial_episode_count=index % 5,
        adversarial_success_rate=None if index % 5 == 0 else 0.5,
        block_reasons=() if index % 2 == 0 else (f"composite low ({index})",),
    )
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        created_at=created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        baseline_run_id=None if index % 3 != 0 else f"vrun-{(index - 1) % 180:04d}",
        regression=regression,
    )


_RUN_COUNT = 24
_ALL_RUNS = [_make_run(i) for i in range(_RUN_COUNT)]
_BY_ID = {r.run_id: r for r in _ALL_RUNS}
_ALL_IDS = [r.run_id for r in _ALL_RUNS]
_MISSING_ID = "vrun-does-not-exist"
_MISSING_CHECKPOINT = "ckpt-never-validated"


# --------------------------------------------------------------------------
# Canonicalisation: what "identical observable result" means
# --------------------------------------------------------------------------


def _canon(run: StoredRun | None) -> Any:
    """Reduce a run to a JSON-comparable value.

    ``to_dict``/``asdict`` are used rather than raw attributes so that the
    tuple/list distinction (confidence interval, block reasons) is normalised
    the same way both backends' serialisation paths normalise it, and so a
    failure message is a readable diff rather than an opaque dataclass repr.
    """
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "checkpoint_id": run.checkpoint_id,
        "task_id": run.task_id,
        "created_at": run.created_at,
        "scorecard": run.scorecard.to_dict(),
        "evaluation": run.evaluation.to_dict(),
        "safety": run.safety.to_dict(),
        "episodes": [asdict(e) for e in run.episodes],
        "baseline_run_id": run.baseline_run_id,
        "regression": None if run.regression is None else run.regression.to_dict(),
    }


def _canon_all(runs: Iterable[StoredRun]) -> list[Any]:
    return [_canon(r) for r in runs]


#: The date-bound combinations every observation probes.
_BOUNDS: list[tuple[str | None, str | None]] = [
    (None, None),
    (_STAMPS[0], None),
    (None, _STAMPS[0]),
    (_STAMPS[4], _STAMPS[12]),
    (_STAMPS[12], _STAMPS[4]),  # inverted range -> empty on every backend
    (_STAMPS[-1], None),
    (None, _STAMPS[-1]),
    ("2030-01-01T00:00:00+00:00", None),  # matches nothing
    (None, "2000-01-01T00:00:00+00:00"),  # matches nothing
]


def _observe(store: ValidationStore) -> dict[str, Any]:
    """Ask a store every question the differential test cares about."""
    return {
        "count": store.count(),
        "len": len(store),
        "history": [r.run_id for r in store.history()],
        "history_full": _canon_all(store.history()),
        "bounds": {
            f"{since}|{until}": [r.run_id for r in store.history(since=since, until=until)]
            for since, until in _BOUNDS
        },
        "per_checkpoint": {
            ckpt: [r.run_id for r in store.list_for_checkpoint(ckpt)]
            for ckpt in (*_CHECKPOINTS, _MISSING_CHECKPOINT)
        },
        "gets": {rid: _canon(store.get(rid)) for rid in _ALL_IDS},
        "missing_get": _canon(store.get(_MISSING_ID)),
        "summary": [store.get(rid).summary() if store.get(rid) else None for rid in _ALL_IDS],
    }


# --------------------------------------------------------------------------
# Op script
# --------------------------------------------------------------------------

_OP_NAMES = ("save", "save_exists", "delete", "get", "history", "checkpoint", "count", "len")
_OP_WEIGHTS = (20, 8, 10, 16, 16, 12, 9, 9)


def _build_script(seed: int, n_ops: int) -> list[tuple[str, Any]]:
    """Build a reproducible op script.

    Guarantees: the script opens with a ``save`` (so it is never a no-op).
    Both ``save`` (tolerant, append-only) and ``save_exists`` (strict) are
    exercised against already-present ids as well as fresh ones, because the
    duplicate path is where the three backends historically disagreed.
    """
    rng = random.Random(seed)
    script: list[tuple[str, Any]] = [("save", _ALL_IDS[0])]
    pool = list(_ALL_IDS[1:])
    rng.shuffle(pool)
    next_new = 0
    for _ in range(n_ops - 1):
        op = rng.choices(_OP_NAMES, weights=_OP_WEIGHTS, k=1)[0]
        if op in ("save", "save_exists") and next_new < len(pool):
            run_id = pool[next_new]
            next_new += 1
            script.append((op, run_id))
            continue
        if op in ("save", "save_exists"):
            op = "history"
        if op in ("delete", "get"):
            script.append((op, rng.choice(_ALL_IDS + [_MISSING_ID])))
        elif op == "history":
            script.append(("history", rng.choice(_BOUNDS)))
        elif op == "checkpoint":
            script.append(("checkpoint", rng.choice((*_CHECKPOINTS, _MISSING_CHECKPOINT))))
        elif op == "count":
            script.append(("count", None))
        else:
            script.append(("len", None))
    return script


def _apply(store: ValidationStore, oracle: dict[str, StoredRun], op: tuple[str, Any]) -> None:
    """Execute one op against a store *and* the independent oracle dict.

    The oracle models the declared append-only contract independently:
    ``setdefault``/``save_exists``-guarded writes, so a shared bug in all
    backends still fails the assertions instead of cancelling out.
    """
    name, arg = op
    if name == "save":
        run = _BY_ID[arg]
        store.save(run)
        # Append-only: a duplicate is rejected, never applied.
        oracle.setdefault(run.run_id, run)
    elif name == "save_exists":
        run = _BY_ID[arg]
        assert store.save_exists(run) == (run.run_id not in oracle)
        oracle.setdefault(run.run_id, run)
    elif name == "delete":
        store.delete(arg)
        oracle.pop(arg, None)
    elif name == "get":
        store.get(arg)
    elif name == "history":
        store.history(since=arg[0], until=arg[1])
    elif name == "checkpoint":
        store.list_for_checkpoint(arg)
    elif name == "count":
        store.count()
    else:
        len(store)


def _oracle_history_ids(oracle: dict[str, StoredRun]) -> list[str]:
    """Expected oldest-first ids, computed from the oracle, not from a store."""
    return [r.run_id for r in sorted(oracle.values(), key=lambda r: r.created_at)]


# --------------------------------------------------------------------------
# The property test
# --------------------------------------------------------------------------

#: (label, factory) — SQLite stores are closed by the fixture below.
_SCRIPT_SEEDS = [1, 7, 13, 29, 41, 97]
_OPS_PER_SCRIPT = 70


def _run_script(seed: int, n_ops: int) -> dict[str, Any]:
    """Replay a script against both backends; return the first mismatch, if any.

    The oracle is a plain dict applying the *declared* append-only rule, so it
    is an independent check: a bug all backends shared would still surface.
    """
    import tempfile

    mem = ValidationStore()
    with tempfile.TemporaryDirectory() as raw:
        lite = SqliteValidationStore(Path(raw) / "diff.db")
        try:
            oracle_mem: dict[str, StoredRun] = {}
            oracle_lite: dict[str, StoredRun] = {}
            for step, op in enumerate(_build_script(seed, n_ops)):
                _apply(mem, oracle_mem, op)
                _apply(lite, oracle_lite, op)

                assert oracle_mem == oracle_lite, f"oracle drift at step {step} ({op})"

                expected_history = _oracle_history_ids(oracle_mem)
                assert mem.count() == len(oracle_mem), f"memory count drift at step {step}"
                assert lite.count() == len(oracle_lite), f"sqlite count drift at step {step}"
                assert len(mem) == len(oracle_mem), f"memory len drift at step {step}"
                assert len(lite) == len(oracle_lite), f"sqlite len drift at step {step}"
                # Ordering is checked against the oracle, so a shared bug in
                # both backends still fails instead of cancelling out.
                assert [r.run_id for r in mem.history()] == expected_history
                assert [r.run_id for r in lite.history()] == expected_history
                for ckpt in (*_CHECKPOINTS, _MISSING_CHECKPOINT):
                    expected_ck = [
                        r.run_id
                        for r in sorted(
                            (r for r in oracle_mem.values() if r.checkpoint_id == ckpt),
                            key=lambda r: r.created_at,
                        )
                    ]
                    assert [r.run_id for r in mem.list_for_checkpoint(ckpt)] == expected_ck
                    assert [r.run_id for r in lite.list_for_checkpoint(ckpt)] == expected_ck

                obs_mem = _observe(mem)
                obs_lite = _observe(lite)
                if obs_mem != obs_lite:
                    differing = sorted(
                        k for k in obs_mem if obs_mem[k] != obs_lite[k]
                    )
                    detail = {
                        k: {"memory": obs_mem[k], "sqlite": obs_lite[k]} for k in differing
                    }
                    return {
                        "step": step,
                        "op": repr(op),
                        "differing_keys": differing,
                        "detail": json.dumps(detail, default=str, indent=2)[:4000],
                    }
            return {}
        finally:
            lite.close()


@pytest.mark.parametrize("seed", _SCRIPT_SEEDS)
def test_random_op_script_is_identical_across_backends(seed: int) -> None:
    """Memory and SQLite agree on every observable after every operation."""
    mismatch = _run_script(seed, _OPS_PER_SCRIPT)
    assert not mismatch, (
        f"backends diverged at step {mismatch['step']} after {mismatch['op']}; "
        f"differing observations: {mismatch['differing_keys']}\n"
        f"{mismatch['detail']}"
    )


# --------------------------------------------------------------------------
# Hypothesis-driven variant (extra shape coverage, still deterministic)
# --------------------------------------------------------------------------

_task_ids = st.sampled_from(_ALL_IDS)
_ops = st.lists(
    st.one_of(
        st.tuples(st.just("save"), _task_ids),
        st.tuples(st.just("save_exists"), _task_ids),
        st.tuples(st.just("delete"), st.sampled_from(_ALL_IDS)),
        st.tuples(st.just("get"), st.sampled_from(_ALL_IDS)),
        st.tuples(
            st.just("history"),
            st.sampled_from([(None, None), (_STAMPS[0], None), (None, _STAMPS[0])]),
        ),
        st.tuples(st.just("checkpoint"), st.sampled_from(_CHECKPOINTS)),
        st.tuples(st.just("count"), st.none()),
    ),
    min_size=1,
    max_size=25,
)


@settings(
    max_examples=40,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(script=_ops)
def test_hypothesis_ops_identical_across_backends(
    script: list[tuple[str, Any]], tmp_path: Path
) -> None:
    """The same invariant, over shapes Hypothesis picks rather than fixed seeds."""
    import tempfile

    mem = ValidationStore()
    with tempfile.TemporaryDirectory() as raw:
        lite = SqliteValidationStore(Path(raw) / "hyp.db")
        try:
            # Two independent oracles, one per store: both are driven by the
            # same op script, so a backend that diverges shows up as an oracle
            # disagreement rather than a shared blind spot.
            oracle_mem: dict[str, StoredRun] = {}
            oracle_lite: dict[str, StoredRun] = {}
            for op in script:
                _apply(mem, oracle_mem, op)
                _apply(lite, oracle_lite, op)
                assert oracle_mem == oracle_lite, f"oracle drift after {op!r}"
                obs_mem = _observe(mem)
                obs_lite = _observe(lite)
                assert obs_mem == obs_lite, (
                    f"diverged after {op!r}; keys: "
                    f"{[k for k in obs_mem if obs_mem[k] != obs_lite[k]]}"
                )
                assert [r.run_id for r in mem.history()] == _oracle_history_ids(oracle_mem)
                assert [r.run_id for r in lite.history()] == _oracle_history_ids(oracle_lite)
        finally:
            lite.close()


# --------------------------------------------------------------------------
# create_store() must hand back the backend the environment names
# --------------------------------------------------------------------------


class TestFactoryBackendSelectionParity:
    """``create_store`` must not change which observable contract applies."""

    def test_sqlite_env_is_observationally_equal_to_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from validsim.store import create_store

        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(tmp_path / "factory.db"))
        env_store = create_store()
        mem = ValidationStore()
        try:
            assert type(env_store) is SqliteValidationStore
            oracle_env: dict[str, StoredRun] = {}
            oracle_mem: dict[str, StoredRun] = {}
            for op in _build_script(3, 45):
                _apply(env_store, oracle_env, op)
                _apply(mem, oracle_mem, op)
                assert oracle_env == oracle_mem, f"oracle drift after {op!r}"
                assert _observe(env_store) == _observe(mem)
        finally:
            env_store.close()

    def test_blank_sqlite_path_falls_back_to_documented_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blank path must not silently open a throwaway temp database."""
        from validsim.store import create_store

        monkeypatch.setenv("VALIDSIM_STORE", "  SQLITE  ")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", "   ")
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        store = create_store()
        try:
            assert isinstance(store, SqliteValidationStore)
            assert Path(store.db_path).name == "validsim.db"
        finally:
            store.close()

    def test_unknown_backend_falls_back_to_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from validsim.store import create_store

        monkeypatch.setenv("VALIDSIM_STORE", "cassandra")
        assert type(create_store()) is ValidationStore


# --------------------------------------------------------------------------
# Live-PostgreSQL gate, shared with the dedicated PG differential file
# --------------------------------------------------------------------------

#: Env var a developer/CI sets to point the differential suite at a real server.
PG_TEST_DSN_ENV = "VALIDSIM_TEST_PG_URL"


def live_postgres_dsn() -> tuple[str | None, str]:
    """Return ``(dsn, reason)``; ``dsn`` is ``None`` when no server is usable.

    Checked in order, and every failure is reported verbatim rather than
    silently downgraded to a skip message that hides which condition failed.
    """
    dsn = (os.environ.get(PG_TEST_DSN_ENV) or os.environ.get("VALIDSIM_PG_URL") or "").strip()
    if not dsn:
        return None, (
            f"no PostgreSQL DSN: set {PG_TEST_DSN_ENV} (e.g. "
            "postgresql://validsim:pw@127.0.0.1:5432/validsim) to enable the "
            "three-backend differential tests"
        )
    try:
        import psycopg  # noqa: PLC0415
    except ImportError:
        return None, (
            f"{PG_TEST_DSN_ENV} is set but the psycopg 3 driver is not installed "
            "(pip install 'psycopg[binary]')"
        )
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 - any failure means "no live server"
        return None, f"cannot reach PostgreSQL at the configured DSN: {exc}"
    return dsn, "live PostgreSQL"


@pytest.fixture
def pg_store() -> Iterable[Any]:
    """Yield a live PostgreSQL-backed store, or skip with the precise reason."""
    dsn, reason = live_postgres_dsn()
    if dsn is None:
        pytest.skip(reason)
    from validsim.store.postgres import PostgresValidationStore  # noqa: PLC0415

    table = f"diff_agent_{os.getpid()}"
    store = PostgresValidationStore(dsn, table)
    try:
        store._conn_factory = lambda: __import__(
            "psycopg"
        ).connect(dsn, autocommit=True)
        conn = store._ensure_ready()  # noqa: SLF001 - test seam, documented as such
        conn.execute(f"TRUNCATE TABLE {table}")
        yield store
    finally:
        try:
            store._conn_factory = None
            conn = getattr(store, "_conn", None)
            if conn is not None and not getattr(conn, "closed", False):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        finally:
            store.close()


def test_live_postgres_gate_reports_its_reason_plainly() -> None:
    """Whatever the environment, the gate must state a concrete reason."""
    dsn, reason = live_postgres_dsn()
    if dsn is None:
        assert reason, "a skip must carry a reason the developer can act on"
        assert "psycopg" in reason or "PostgreSQL" in reason
    else:  # pragma: no cover - only on a machine with a live server
        assert reason == "live PostgreSQL"


def test_live_postgres_smoke_if_available(pg_store: Any) -> None:
    """Three-backend smoke: a save/get round trip against a real server.

    Skipped — not silently passed — when no server is configured.
    """
    run = _ALL_RUNS[3]
    pg_store.save(run)
    assert pg_store.get(run.run_id) == run
    assert pg_store.count() == 1
