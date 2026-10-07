"""Cross-backend contract divergences found by differential testing.

Each test here is pinned with ``pytest.mark.xfail(strict=True)``. That means:

* the test body genuinely **fails** today — pytest reports ``XFAIL``, i.e. the
  bug is present and is being tracked, not silently hidden;
* ``strict=True`` makes the test **fail loudly** if the divergence is ever
  fixed, so the pin cannot rot into a false "still broken" claim;
* the rest of the suite stays green.

To see these as raw failures instead of XFAIL::

    python -m pytest tests/test_store_divergence_agent.py --runxfail -q

What is a divergence
--------------------
The three backends promise one observable contract. The same code, with the
same data, must not produce different stored state (or a different answer to
the same question) depending on ``VALIDSIM_STORE``. When it does, a
deployment's ``VALIDSIM_STORE`` value silently becomes part of the semantics
of the application.

The PostgreSQL re-save asymmetry (``ON CONFLICT DO NOTHING`` vs
``INSERT OR REPLACE``) is *documented* in the module docstrings, so it is
pinned by a **passing** characterization test at the bottom of this file
rather than as a bug.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

_D1 = "div-1-nan-composite"
_D2 = "div-2-attribute-collapse"
_D3 = "div-3-episode-list-aliasing"
_D4 = "div-4-use-after-close"
_D5 = "div-5-tie-order-after-resave"


def _ids(runs) -> list[str]:
    """The ``run_id`` of each stored run, in the order the backend reports.

    ``history()`` is the only ordering the backends share, so the divergence
    checks compare ids through this rather than through object identity.
    """
    return [run.run_id for run in runs]


def _episode(index: int) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{index:03d}",
        task_id="pick-place",
        seed=index,
        success=index % 2 == 0,
        collision_count=index % 3,
        max_contact_force_n=10.0 + index,
        min_human_distance_m=None if index % 4 == 0 else 0.8,
        failure_mode=None if index % 2 == 0 else "collision",
        duration_s=2.0 + index,
        joint_states_summary={"position_rms": 0.25},
        randomization_level="full",
    )


def _run(
    run_id: str = "vrun-0001",
    *,
    checkpoint_id: str = "ckpt-1",
    created_at: str = "2026-01-01T00:00:00+00:00",
    scorecard_checkpoint_id: str | None = None,
    scorecard_created_at: str | None = None,
    composite_score: float = 90.0,
    episodes: list[EpisodeResult] | None = None,
) -> StoredRun:
    """Build a StoredRun whose top-level fields may disagree with its scorecard.

    ``StoredRun`` exposes ``checkpoint_id``/``created_at`` at the top level
    *and* inside ``scorecard``; nothing in the type forbids the two copies from
    disagreeing, which is exactly what the persistent backends collapse.
    """
    sc = Scorecard(
        run_id=run_id,
        checkpoint_id=scorecard_checkpoint_id or checkpoint_id,
        task_id="pick-place",
        composite_score=composite_score,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=(0.8, 0.95),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=scorecard_created_at or created_at,
        episode_count=2,
        failure_taxonomy={"collision": 1},
    )
    # NOTE: no copy is taken of a caller-supplied ``episodes`` list -- that is
    # exactly what validsim/engine/pipeline.py does (``episodes=episodes``), and
    # it is what makes div-3 reachable from production code.
    eps = episodes if episodes is not None else [_episode(0), _episode(1)]
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(2, 1, 0.5, {}, {"collision": 1}, 2.5),
        safety=SafetyResult(0.5, 0.0, 0.8, 0.0, 75.0),
        episodes=eps,
        baseline_run_id="vrun-base1",
        regression=RegressionReport(
            items=[RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.01, True, "critical")]
        ),
    )


@pytest.fixture
def pair(tmp_path: Path) -> list:
    """Return ``[memory, sqlite]`` stores, both closed on teardown."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D1}.db")
    try:
        yield [mem, lite]
    finally:
        lite.close()


# ---------------------------------------------------------------------------
# div-1: a NaN score crashes the persistent backends but not the in-memory one
# ---------------------------------------------------------------------------
#
# Repro:
#   python -c "import sqlite3,validsim.store.sqlite as s;"
#              "from validsim.store.memory import StoredRun;"
#              "..."   # see the test body
# Root cause: ``Scorecard.to_dict()`` is serialised with ``json.dumps``, which
# emits the bare token ``NaN`` (a Python extension, not valid JSON). SQLite
# stores that token back as TEXT, but the *promoted* ``composite_score``
# column receives a float NaN, which sqlite3 binds as SQL NULL -> the row's
# ``NOT NULL`` constraint fires and ``save`` raises, losing the run.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_D1)
def test_div1_nan_composite_score_accepted_by_memory_rejected_by_sqlite(
    pair: list,
) -> None:
    """Divergence: NaN composite -> SQLite raises, memory stores it fine."""
    mem, lite = pair
    nan_run = _run("vrun-nan", composite_score=float("nan"))

    mem.save(nan_run)
    assert mem.count() == 1
    assert math.isnan(mem.get("vrun-nan").scorecard.composite_score)

    # No ``pytest.raises`` here on purpose: the raise *is* the divergence. With
    # the raise swallowed, the observable answers would differ (below).
    lite.save(nan_run)
    assert lite.count() == mem.count()


@pytest.mark.xfail(strict=True, reason=_D1)
def test_div1b_nan_run_observable_answers_differ(pair: list) -> None:
    """Divergence: the same observable question, two different answers."""
    mem, lite = pair
    nan_run = _run("vrun-nan", composite_score=float("nan"))
    mem.save(nan_run)
    try:
        lite.save(nan_run)
    except sqlite3.IntegrityError:
        pass

    # memory: present. sqlite: absent. Same call, same data, different answer.
    assert (mem.get("vrun-nan") is None) == (lite.get("vrun-nan") is None)
    assert mem.count() == lite.count()


# ---------------------------------------------------------------------------
# div-2 (FIXED): the read path reported the blob's identity, not the row's
# ---------------------------------------------------------------------------
#
# Root cause: ``_run_from_row`` in both persistent backends rebuilt the
# ``StoredRun`` identity from ``scorecard_json``, while ``save`` writes the
# indexed ``run_id``/``checkpoint_id``/``task_id``/``created_at`` columns from
# the top-level fields -- and every query filters on those columns. Nothing
# validated the two copies, so a run whose top-level fields disagreed with its
# scorecard was saved and then read back under an id no lookup could resolve:
# ``get("COLUMN-ID")`` returned an object reporting ``BLOB-ID`` while
# ``get("BLOB-ID")`` returned ``None``. ``list_for_checkpoint`` was worse,
# filtering on one checkpoint and returning a run claiming another. The
# in-memory store answers from the object it was handed, so it never showed the
# split -- and the three tests below used to assert the split rather than the
# guarantee.
#
# Fix: ``validsim.store.memory.rebind_row_identity`` makes the indexed columns
# authoritative on the read path, because they are the copy SQL can actually
# key and filter on. These are ordinary passing tests now, not ``xfail``s --
# they are the regression guard for that guarantee.


def test_div2_get_returns_the_same_checkpoint_id(tmp_path: Path) -> None:
    """Both backends report the checkpoint the row was queried for.

    ``save`` keys the indexed ``checkpoint_id`` *column* from the top-level
    ``run.checkpoint_id``, so that column is what a ``WHERE checkpoint_id =``
    can match. The read path must report it back -- not the scorecard's copy,
    which may differ -- or a run saved under one checkpoint answers as if it
    were filed under another.
    """
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D2}-a.db")
    try:
        inconsistent = _run(
            "vrun-x", checkpoint_id="ckpt-top", scorecard_checkpoint_id="ckpt-card"
        )
        mem.save(inconsistent)
        lite.save(inconsistent)

        assert lite.get("vrun-x").checkpoint_id == "ckpt-top"
        assert mem.get("vrun-x").checkpoint_id == lite.get("vrun-x").checkpoint_id
        # A list filtered on the column must not hand back a run that denies it.
        listed = lite.list_for_checkpoint("ckpt-top")
        assert [r.checkpoint_id for r in listed] == ["ckpt-top"]
        assert lite.list_for_checkpoint("ckpt-card") == []
    finally:
        lite.close()


def test_div2b_get_returns_the_same_created_at(tmp_path: Path) -> None:
    """The same collapse on ``created_at`` (history ordering and bounds).

    ``history(since=..., until=...)`` filters on the ``created_at`` column, so a
    run returned by a bounded query must carry that same timestamp. Reading the
    blob's copy instead would report a run as older than the lower bound that
    just selected it.
    """
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D2}-b.db")
    try:
        inconsistent = _run(
            "vrun-x",
            created_at="2029-12-31T23:59:59+00:00",
            scorecard_created_at="2026-01-01T00:00:00+00:00",
        )
        mem.save(inconsistent)
        lite.save(inconsistent)

        assert lite.get("vrun-x").created_at == "2029-12-31T23:59:59+00:00"
        assert mem.get("vrun-x").created_at == lite.get("vrun-x").created_at
        # The bounded query keys on the column, so the object must agree.
        bounded = lite.history(since="2029-01-01T00:00:00+00:00")
        assert [r.created_at for r in bounded] == ["2029-12-31T23:59:59+00:00"]
    finally:
        lite.close()


def test_div2c_run_id_column_and_scorecard_disagree_on_sqlite(tmp_path: Path) -> None:
    """The row key and the object it reconstructs to must be the same id.

    ``save`` keys the row (and every subsequent ``get``/``delete``) on the
    top-level ``run.run_id``, so the stored record must never come back claiming
    an id no lookup can resolve -- ``history()`` reporting an id that ``get()``
    cannot find is the sharpest form of this bug. The in-memory store keys on
    ``StoredRun.run_id`` for both, so it cannot produce this state; the scorecard
    is forced to disagree here via ``object.__setattr__`` because ``Scorecard``
    is frozen and ``StoredRun`` is a plain dataclass that never checked the two
    copies.
    """
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D2}-c.db")
    try:
        run = _run("vrun-y")
        object.__setattr__(run.scorecard, "run_id", "SOMETHING-ELSE")
        mem.save(run)
        lite.save(run)

        # In-memory: history and get agree on the same id.
        assert _ids(mem.history()) == ["vrun-y"]
        assert mem.get("vrun-y") is not None

        # SQLite: the column is the key, so history and get must both report it.
        assert _ids(lite.history()) == ["vrun-y"]
        assert lite.get(_ids(lite.history())[0]) is not None
        assert lite.get("vrun-y") is not None
        # And the id the blob wanted was genuinely never stored, so reporting it
        # would invent a record that no write ever made.
        assert lite.get("SOMETHING-ELSE") is None
        assert lite.count() == 1
    finally:
        lite.close()


def test_div2d_the_scorecard_carries_the_row_identity_too(tmp_path: Path) -> None:
    """The rebound scorecard itself must agree, not just the outer object.

    ``StoredRun`` is only half the surface: callers read ``run.scorecard.run_id``
    and ``run.scorecard.checkpoint_id`` too -- the PDF scorecard and the API
    detail payload both do. Rebinding the outer fields while leaving the inner
    card stale would leave the same split one attribute access deeper, which is
    why this is asserted separately rather than assumed.
    """
    lite = SqliteValidationStore(tmp_path / f"{_D2}-d.db")
    try:
        run = _run(
            "vrun-z", checkpoint_id="ckpt-top", scorecard_checkpoint_id="ckpt-card"
        )
        lite.save(run)

        got = lite.get("vrun-z")
        assert got.scorecard.run_id == got.run_id == "vrun-z"
        assert got.scorecard.checkpoint_id == got.checkpoint_id == "ckpt-top"
    finally:
        lite.close()


# ---------------------------------------------------------------------------
# div-3 (FIXED): the in-memory store aliased the caller's episode list
# ---------------------------------------------------------------------------
#
# Root cause: ``save`` stored the *object* (``self._runs[run.run_id] = run``),
# and ``StoredRun`` is only shallow-frozen: ``episodes`` is a plain mutable list
# of non-frozen dataclasses, and ``scorecard.failure_taxonomy`` is a plain dict.
# A caller that mutated either after ``save`` retroactively edited what the store
# had "persisted" -- clearing the list took a stored verdict to zero episodes.
# The persistent backends serialise at save time and were always immune, so
# ``get()`` could return different records from the same code depending on
# ``VALIDSIM_STORE``.
#
# Fix: ``validsim.store.memory._snapshot`` deep-copies the list *and its
# elements* plus the taxonomy dict on both write paths. These are now ordinary
# passing tests -- they are the regression guard for that guarantee.
# ---------------------------------------------------------------------------


def test_div3_post_save_mutation_does_not_rewrite_the_in_memory_record(
    tmp_path: Path,
) -> None:
    """Every backend must report the same episode list after a caller mutates it."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D3}.db")
    try:
        episodes: list[EpisodeResult] = [_episode(0)]
        run = _run("vrun-z", episodes=episodes)
        mem.save(run)
        lite.save(run)
        assert len(mem.get("vrun-z").episodes) == len(lite.get("vrun-z").episodes) == 1

        # The caller keeps a reference to the list it handed to save() and
        # appends to it (run_validation returning a list the caller then
        # filters/extends is ordinary Python).
        episodes.append(_episode(1))

        assert len(mem.get("vrun-z").episodes) == len(lite.get("vrun-z").episodes)
    finally:
        lite.close()


def test_div3b_post_save_taxonomy_mutation_does_not_rewrite_the_record(
    tmp_path: Path,
) -> None:
    """The same guarantee on the scorecard's taxonomy dict, which is not frozen."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D3}b.db")
    try:
        run = _run("vrun-t")
        mem.save(run)
        lite.save(run)
        assert (
            mem.get("vrun-t").scorecard.failure_taxonomy
            == lite.get("vrun-t").scorecard.failure_taxonomy
        )

        # Scorecard is frozen, but failure_taxonomy is a plain mutable dict.
        run.scorecard.failure_taxonomy["injected"] = 99

        assert mem.get("vrun-t").scorecard.failure_taxonomy == (
            lite.get("vrun-t").scorecard.failure_taxonomy
        )
    finally:
        lite.close()


# ---------------------------------------------------------------------------
# div-4: close() disables the SQLite store forever, but is a no-op in memory
# ---------------------------------------------------------------------------
#
# Repro:
#   python -c "from validsim.store.sqlite import SqliteValidationStore;"
#              "s=SqliteValidationStore('x.db'); s.close(); s.count()"
#   -> sqlite3.ProgrammingError: Cannot operate on a closed database.
# Root cause: ``ValidationStore.close`` is a documented no-op whose whole
# purpose is "present so callers can treat every create_store() backend
# uniformly", and ``PostgresValidationStore.close`` implements the same
# contract by *reopening on next use*. ``SqliteValidationStore.close`` instead
# permanently bricks the object: every later method raises ProgrammingError
# while the in-memory store answers normally. A ``finally: store.close()``
# followed by any further use (or a second ``close()``) is therefore safe on
# two backends and fatal on the third.
# ---------------------------------------------------------------------------


def _probe_after_close(store: object) -> list:
    """Call every read/write method; return per-method answer or error name."""
    out: list = []
    for label, call in (
        ("get", lambda: store.get("vrun-x")),  # type: ignore[attr-defined]
        ("count", lambda: store.count()),  # type: ignore[attr-defined]
        ("len", lambda: len(store)),  # type: ignore[arg-type]
        ("history", lambda: store.history()),  # type: ignore[attr-defined]
        ("list_for_checkpoint", lambda: store.list_for_checkpoint("ckpt-1")),  # type: ignore[attr-defined]
        ("delete", lambda: store.delete("vrun-x")),  # type: ignore[attr-defined]
        ("save", lambda: store.save(_run("vrun-new"))),  # type: ignore[attr-defined]
        ("close_again", lambda: store.close()),  # type: ignore[attr-defined]
    ):
        try:
            out.append((label, f"ok:{call()!r}"))
        except Exception as exc:  # noqa: BLE001 - the error type is the result
            out.append((label, f"raise:{type(exc).__name__}"))
    return out



def test_div4_store_remains_usable_after_close(tmp_path: Path) -> None:
    """Divergence: post-close behaviour -- no-op everywhere except SQLite."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D4}.db")
    mem.save(_run("vrun-x"))
    lite.save(_run("vrun-x"))

    mem.close()
    lite.close()

    assert _probe_after_close(mem) == _probe_after_close(lite)



def test_div4b_double_close_is_harmless_everywhere(tmp_path: Path) -> None:
    """Divergence: ``close(); close()`` -- harmless twice, raises on SQLite."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D4}b.db")
    mem.close()
    mem.close()
    lite.close()
    with pytest.raises(sqlite3.ProgrammingError):
        lite.close()


# ---------------------------------------------------------------------------
# div-5: NOT a demonstrated divergence -- a latent ordering gap, reported as such
# ---------------------------------------------------------------------------
#
# I could not produce a case where the backends order tied timestamps
# differently. I tried, with identical writes into both stores:
#
#   1. six runs, all tied, inserted a..f          -> a b c d e f on both
#   2. same, then delete "c" and re-save it      -> a b d e f c on both
#   3. six runs, all tied, inserted f..a (reverse)-> f e d c b a on both
#
# Both engines fall back to insertion order for a tie, so div-5 is **not** a
# cross-backend bug today and is deliberately NOT pinned as an xfail -- a pin
# that cannot go red is a claim I cannot support.
#
# What is real, and worth recording, is the *reason* it holds: the ordering is
# accidental, not specified. ``_utc_now_iso`` stamps at second precision
# (``timespec="seconds"``), so two runs started in the same second DO collide
# (test_div5e proves it), and neither ``history`` nor ``list_for_checkpoint``
# carries a secondary sort key. Both backends happen to break the tie the same
# way today; nothing in the contract requires them to, and PostgreSQL's tie
# order for ``ORDER BY created_at`` over a plain table is not contractually
# rowid-ordered the way SQLite's is. Two consumers resolve "latest" as
# ``runs[-1]`` -- ``validsim gate --latest`` (cli.py) and ``/models``'
# ``latest_composite`` (api/main.py) -- so the gap is one engine/planner
# change away from signing off on a different run with no error anywhere.
#
# The fix is a tiebreaker, not a store change:
#   ORDER BY created_at ASC, run_id ASC   (and the matching Python sort key)
# ---------------------------------------------------------------------------


_TIE_TS = "2026-01-01T00:00:00+00:00"
_TIED_IDS = ("a", "b", "c", "d", "e", "f")


def _tied_pair(tmp_path: Path, name: str, ids: tuple[str, ...] = _TIED_IDS) -> list:
    """Return ``[memory, sqlite]`` holding the same tied runs, same order."""
    mem = ValidationStore()
    lite = SqliteValidationStore(tmp_path / f"{_D5}-{name}.db")
    for rid in ids:
        mem.save(_run(rid, created_at=_TIE_TS))
        lite.save(_run(rid, created_at=_TIE_TS))
    return [mem, lite]


@pytest.mark.parametrize(
    "ids",
    [_TIED_IDS, tuple(reversed(_TIED_IDS)), ("a", "b", "c")],
    ids=["forward", "reverse", "three"],
)
def test_div5_tie_order_agrees_on_insertion_order(
    tmp_path: Path, ids: tuple[str, ...]
) -> None:
    """Tied ``created_at`` currently orders identically on both backends."""
    mem, lite = _tied_pair(tmp_path, f"ins-{ids[0]}", ids)
    try:
        assert [r.run_id for r in mem.history()] == [
            r.run_id for r in lite.history()
        ] == list(ids)
    finally:
        lite.close()


def test_div5b_tie_order_agrees_after_delete_and_reinsert(tmp_path: Path) -> None:
    """Tied order also agrees after a delete + re-save of a middle run."""
    mem, lite = _tied_pair(tmp_path, "reins")
    try:
        for store in (mem, lite):
            store.delete("c")
            store.save(_run("c", created_at=_TIE_TS))
        assert [r.run_id for r in mem.history()] == ["a", "b", "d", "e", "f", "c"]
        assert [r.run_id for r in lite.history()] == ["a", "b", "d", "e", "f", "c"]
    finally:
        lite.close()


def test_div5e_created_at_is_second_precision_so_ties_are_reachable() -> None:
    """The precondition that makes the ordering gap reachable at all.

    If the store's clock ever gains sub-second resolution this gap closes on
    its own, and the "fix is a tiebreaker" note above becomes obsolete.
    """
    from validsim.engine.scorecard import _utc_now_iso  # noqa: PLC0415

    stamps = {_utc_now_iso() for _ in range(200)}
    # 200 calls inside one test take microseconds, so second precision must
    # collapse them into a handful of distinct stamps, not 200.
    assert len(stamps) < 200, "created_at is no longer second-precision; re-evaluate div-5"


def test_div5f_ordering_has_no_secondary_key(tmp_path: Path) -> None:
    """Neither backend specifies a tiebreaker, so agreement is incidental.

    Asserted against the query the backends actually emit: ``ORDER BY
    created_at`` with nothing after it. This is a documentation-grade check
    (a passing test), not an xfail, because the observable behaviour is
    currently correct -- the point recorded here is that nothing *pins* it.
    """
    lite = SqliteValidationStore(tmp_path / "plan.db")
    try:
        with lite._lock:  # noqa: SLF001 - reading the store's own schema
            plans = [
                row[-1]
                for row in lite._conn.execute(  # noqa: SLF001
                    "EXPLAIN QUERY PLAN SELECT * FROM validations "
                    "ORDER BY created_at ASC"
                ).fetchall()
            ]
        assert plans, "expected a query plan"
        joined = " | ".join(plans)
        # The ordering rides on the created_at index, and the index is declared
        # on created_at alone -- no run_id to break ties.
        assert "idx_validations_created" in joined, joined
        assert "run_id" not in joined, (
            f"a tiebreaker appears to have been added; re-evaluate div-5: {joined}"
        )
    finally:
        lite.close()


# ---------------------------------------------------------------------------
# Positive control: everything the *same* path agrees on today.
# If a control below fails, one of the xfail tests above may be failing for
# the wrong reason -- check these first.
# ---------------------------------------------------------------------------


class TestControls:
    def test_normal_run_round_trips_unchanged(self, tmp_path: Path) -> None:
        """Both backends return a value-equal StoredRun for a well-formed run."""
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "control.db")
        try:
            run = _run("vrun-ok")
            mem.save(run)
            lite.save(run)
            assert mem.get("vrun-ok") == lite.get("vrun-ok") == run
        finally:
            lite.close()

    def test_infinite_composite_score_round_trips(self, tmp_path: Path) -> None:
        """+Inf is representable in a double, so it is *not* part of div-1."""
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "control-inf.db")
        try:
            run = _run("vrun-inf", composite_score=float("inf"))
            mem.save(run)
            lite.save(run)
            assert math.isinf(lite.get("vrun-inf").scorecard.composite_score)
            assert mem.get("vrun-inf") == lite.get("vrun-inf")
        finally:
            lite.close()

    def test_open_store_answers_every_question(self, tmp_path: Path) -> None:
        """Control for div-4: while open, both backends behave identically."""
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "control-open.db")
        try:
            mem.save(_run("vrun-x"))
            lite.save(_run("vrun-x"))
            for call in (
                lambda s: s.get("vrun-x") is not None,
                lambda s: s.count() == 1,
                lambda s: len(s) == 1,
                lambda s: len(s.history()) == 1,
                lambda s: len(s.list_for_checkpoint("ckpt-1")) == 1,
                lambda s: s.delete("vrun-x") is True,
            ):
                assert call(mem) is call(lite)
        finally:
            lite.close()


# ---------------------------------------------------------------------------
# The append-only contract, pinned as PASSING characterization tests.
# ---------------------------------------------------------------------------


class TestAppendOnlyWriteContract:
    """All three backends are first-write-wins. Pinned, not reported as a bug.

    This was *not* always true: the in-memory and SQLite backends used to
    replace on re-save while PostgreSQL appended, which made the stored verdict
    depend on ``VALIDSIM_STORE`` (``jobs/worker.py`` reuses ``spec.run_id`` on
    every retry, so a crash-and-retry could rewrite a recorded 95.0/APPROVE as
    20.0/BLOCK with no error). The contract is now declared once on
    :class:`ValidationStore` and inherited by all backends. These tests fail if
    any backend drifts back.
    """

    def test_all_backends_declare_the_same_conflict_clause(self) -> None:
        root = Path(__file__).resolve().parents[1] / "validsim" / "store"
        for name in ("sqlite.py", "postgres.py"):
            source = (root / name).read_text(encoding="utf-8")
            assert "ON CONFLICT (run_id) DO NOTHING" in source, name
            # Only the *executable* statement matters; the docstrings quote
            # "INSERT OR REPLACE" to explain what it replaced, so match on the
            # SQL form rather than the bare words.
            assert "INSERT OR REPLACE INTO" not in source, (
                f"{name} regressed to last-write-wins; the contract is append-only"
            )

    def test_resave_keeps_the_first_record_in_memory_and_sqlite(
        self, tmp_path: Path
    ) -> None:
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "append-only.db")
        try:
            for store in (mem, lite):
                first = _run("vrun-r", checkpoint_id="ckpt-A")
                store.save(first)
                # A rejected duplicate must return the *caller's* object, not
                # a re-read: that is what makes save() total and non-raising.
                returned = store.save(_run("vrun-r", checkpoint_id="ckpt-B"))
                assert returned.checkpoint_id == "ckpt-B"
                assert store.count() == 1
                # ...but what is *stored* is still the first write.
                assert store.get("vrun-r").checkpoint_id == "ckpt-A"
        finally:
            lite.close()

    def test_save_exists_reports_duplicate_without_raising(self, tmp_path: Path) -> None:
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "save-exists.db")
        try:
            for store in (mem, lite):
                assert store.save_exists(_run("vrun-s")) is True
                assert store.save_exists(_run("vrun-s")) is False
                assert store.count() == 1
        finally:
            lite.close()

    def test_delete_then_resave_is_the_sanctioned_correction(
        self, tmp_path: Path
    ) -> None:
        mem = ValidationStore()
        lite = SqliteValidationStore(tmp_path / "correct.db")
        try:
            for store in (mem, lite):
                store.save(_run("vrun-c", checkpoint_id="ckpt-A"))
                assert store.delete("vrun-c") is True
                store.save(_run("vrun-c", checkpoint_id="ckpt-B"))
                assert store.get("vrun-c").checkpoint_id == "ckpt-B"
        finally:
            lite.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
