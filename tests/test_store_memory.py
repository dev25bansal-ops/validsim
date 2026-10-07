"""Tests for the in-memory, thread-safe validation store."""

from __future__ import annotations

import re
import threading

import pytest

from conftest import (
    make_detailed_stored_run,
    make_persisted_scorecard,
    make_scorecard,
    make_stored_run,
)
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.store.memory import StoredRun, ValidationStore


class TestRunIdFormat:
    def test_new_run_id_has_prefix_and_8_chars(self) -> None:
        run_id = ValidationStore.new_run_id()
        assert run_id.startswith("vrun-")
        suffix = run_id.removeprefix("vrun-")
        assert len(suffix) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", suffix)

    def test_new_run_ids_are_unique(self) -> None:
        ids = {ValidationStore.new_run_id() for _ in range(200)}
        assert len(ids) == 200


class TestSaveGetRoundTrip:
    def test_save_returns_the_argument_and_get_returns_an_equal_record(self) -> None:
        """``save`` hands back what it was given; ``get`` an equal, detached copy.

        The store snapshots on write so a caller cannot mutate the record
        afterwards, which means the two are equal but no longer the same object.
        Returning a detached copy is the point: the alternative is handing back
        a live reference the caller can still rewrite.
        """
        store = ValidationStore()
        run = make_stored_run()
        saved = store.save(run)
        assert saved is run, "save must return the argument unchanged"

        fetched = store.get(run.run_id)
        assert fetched is not None
        assert fetched == run
        assert fetched is not run

    def test_get_unknown_id_returns_none(self) -> None:
        store = ValidationStore()
        assert store.get("vrun-doesnot1") is None

    def test_resave_keeps_the_first_recorded_verdict(self) -> None:
        """A repeated ``save`` of one id must not change a recorded verdict.

        The store is the evidence record for a deploy gate. Re-saving the same
        ``run_id`` (the job worker reuses ``spec.run_id`` on every retry) used
        to overwrite the row, so a crash-and-retry silently rewrote a
        95.0/APPROVE verdict to 20.0/BLOCK with no error. First write wins.
        """
        store = ValidationStore()
        first = make_stored_run(
            run_id="vrun-00000001",
            scorecard=make_scorecard(
                run_id="vrun-00000001", composite_score=95.0, deploy_decision="APPROVE"
            ),
        )
        retry = make_stored_run(
            run_id="vrun-00000001",
            scorecard=make_scorecard(
                run_id="vrun-00000001", composite_score=20.0, deploy_decision="BLOCK"
            ),
        )
        store.save(first)
        store.save(retry)

        assert len(store) == 1  # a duplicate, not a second row
        kept = store.get("vrun-00000001")
        assert kept is not None
        assert kept.scorecard.composite_score == 95.0
        assert kept.scorecard.deploy_decision == "APPROVE"
        # The first-recorded verdict survives byte for byte. It is a detached
        # copy now (see test_save_returns_the_argument...), so equality rather
        # than identity is what proves the first write was the one kept.
        assert kept == first
        assert kept.scorecard.composite_score == 95.0

    def test_resave_does_not_reorder_created_at(self) -> None:
        """A duplicate write must not re-date an earlier row in the ordering.

        ``created_at`` is the sort key for ``history()`` and
        ``list_for_checkpoint()``. If a retry overwrote the stamp, the record
        would jump to the end of its own checkpoint's history and its position
        relative to genuinely newer runs would change.
        """
        store = ValidationStore()
        store.save(
            make_stored_run(
                run_id="vrun-00000001", created_at="2026-01-01T00:00:00+00:00"
            )
        )
        store.save(
            make_stored_run(
                run_id="vrun-00000002", created_at="2026-01-02T00:00:00+00:00"
            )
        )
        store.save(
            make_stored_run(
                run_id="vrun-00000001", created_at="2026-06-06T00:00:00+00:00"
            )
        )
        assert [r.run_id for r in store.history()] == [
            "vrun-00000001",
            "vrun-00000002",
        ]

    def test_delete_then_save_reuses_the_id(self) -> None:
        """An operator deleting a bad run frees the id for a clean re-record.

        This is the sanctioned way to change what is on the record for a given
        ``run_id``: ``delete`` explicitly, then save the corrected run. Nothing
        is overwritten implicitly.
        """
        store = ValidationStore()
        store.save(
            make_stored_run(
                run_id="vrun-00000001",
                scorecard=make_scorecard(
                    run_id="vrun-00000001", composite_score=20.0, deploy_decision="BLOCK"
                ),
            )
        )
        corrected = make_stored_run(
            run_id="vrun-00000001",
            scorecard=make_scorecard(
                run_id="vrun-00000001", composite_score=95.0, deploy_decision="APPROVE"
            ),
        )
        assert store.delete("vrun-00000001") is True
        store.save(corrected)
        assert len(store) == 1
        kept = store.get("vrun-00000001")
        assert kept is not None and kept.scorecard.composite_score == 95.0


class TestOrdering:
    def test_list_for_checkpoint_filters_and_orders_oldest_first(self) -> None:
        store = ValidationStore()
        store.save(make_stored_run(run_id="vrun-a0000001", checkpoint_id="ckpt-1",
                        created_at="2026-01-03T00:00:00+00:00"))
        store.save(make_stored_run(run_id="vrun-b0000002", checkpoint_id="ckpt-2",
                        created_at="2026-01-01T00:00:00+00:00"))
        store.save(make_stored_run(run_id="vrun-c0000003", checkpoint_id="ckpt-1",
                        created_at="2026-01-02T00:00:00+00:00"))
        runs = store.list_for_checkpoint("ckpt-1")
        assert [r.run_id for r in runs] == ["vrun-c0000003", "vrun-a0000001"]
        assert all(r.checkpoint_id == "ckpt-1" for r in runs)

    def test_list_for_checkpoint_empty_result(self) -> None:
        store = ValidationStore()
        assert store.list_for_checkpoint("ckpt-nope") == []

    def test_history_orders_oldest_first_across_checkpoints(self) -> None:
        store = ValidationStore()
        stamps = ["2026-03-01", "2026-01-01", "2026-02-01"]
        for i, ts in enumerate(stamps):
            store.save(make_stored_run(run_id=f"vrun-{i:08x}", checkpoint_id=f"ckpt-{i}",
                            created_at=ts))
        runs = store.history()
        assert [r.created_at for r in runs] == sorted(stamps)


class TestThreadSafety:
    def test_concurrent_saves_from_8_threads(self) -> None:
        store = ValidationStore()
        n_threads, per_thread = 8, 25
        errors: list[Exception] = []

        def worker(offset: int) -> None:
            try:
                for i in range(per_thread):
                    run = make_stored_run(run_id=f"vrun-{offset * per_thread + i:08x}")
                    store.save(run)
                    assert store.get(run.run_id) is not None
            except Exception as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        assert len(store) == n_threads * per_thread
        assert len(store.history()) == n_threads * per_thread


class TestLenAndClose:
    def test_len_reflects_store_contents(self) -> None:
        store = ValidationStore()
        assert len(store) == 0
        store.save(make_stored_run(run_id="vrun-00000001"))
        store.save(make_stored_run(run_id="vrun-00000002"))
        assert len(store) == 2

    def test_close_is_noop_and_store_remains_usable(self) -> None:
        store = ValidationStore()
        run = make_stored_run()
        store.save(run)
        store.close()  # must not raise
        assert store.get(run.run_id) == run
        store.close()
        assert len(store) == 1


class TestSummary:
    def test_summary_shape(self) -> None:
        run = make_stored_run(run_id="vrun-cafe1234")
        summary = run.summary()
        assert set(summary) == {
            "run_id",
            "checkpoint_id",
            "task_id",
            "created_at",
            "baseline_run_id",
            "composite_score",
            "deploy_decision",
            "episode_count",
        }
        assert summary["run_id"] == "vrun-cafe1234"
        assert summary["checkpoint_id"] == "ckpt-1"
        assert summary["task_id"] == "pick-place"
        assert summary["created_at"] == "2026-01-01T00:00:00+00:00"
        assert summary["baseline_run_id"] is None
        assert summary["composite_score"] == 90.0
        assert summary["deploy_decision"] == "APPROVE"
        assert summary["episode_count"] == 100

    def test_summary_values_follow_scorecard(self) -> None:
        sc = make_scorecard(
            run_id="vrun-deadbeef",
            composite_score=70.0,
            deploy_decision="BLOCK",
            episode_count=42,
        )
        run = StoredRun(
            run_id="vrun-deadbeef",
            checkpoint_id=sc.checkpoint_id,
            task_id=sc.task_id,
            created_at=sc.created_at,
            scorecard=sc,
            evaluation=EvaluationResult(total_episodes=42, success_count=30,
                                        success_rate=0.714),
            safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
            baseline_run_id="vrun-baseline1",
        )
        summary = run.summary()
        assert summary["composite_score"] == 70.0
        assert summary["deploy_decision"] == "BLOCK"
        assert summary["episode_count"] == 42
        assert summary["baseline_run_id"] == "vrun-baseline1"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))


def _run_with_episodes() -> StoredRun:
    """A StoredRun that actually carries episodes.

    ``make_stored_run`` builds one with an empty episode list, which would make
    the aliasing assertions below pass vacuously.
    """
    return make_detailed_stored_run(make_persisted_scorecard())


class TestStoreDoesNotAliasTheCallersObjects:
    """A stored record must not change because its caller kept working.

    ``StoredRun`` is frozen, which reads as immutable but is not: ``episodes``
    is a list of *non-frozen* dataclasses and ``scorecard.failure_taxonomy`` is
    a dict. Storing the caller's object by reference meant anything it did to
    its own list or taxonomy rewrote the record -- clearing the list took a
    stored verdict to zero episodes, and flipping ``episodes[0].success`` changed
    the evidence behind an APPROVE with no write, no lock and no log line. The
    persistent backends never had the hole because they rebuild on read.
    """

    def test_clearing_the_callers_list_leaves_the_record_intact(self) -> None:
        """The sharpest case: an empty record is a verdict with no evidence."""
        run = _run_with_episodes()
        store = ValidationStore()
        store.save(run)

        run.episodes.clear()

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.episodes, "the stored record lost every episode"

    def test_mutating_a_caller_held_episode_does_not_rewrite_the_record(self) -> None:
        """Copying the list is not enough -- the elements are mutable too."""
        run = _run_with_episodes()
        assert run.episodes, "fixture must carry episodes"
        before = [e.success for e in run.episodes]

        store = ValidationStore()
        store.save(run)

        for episode in run.episodes:
            episode.success = not episode.success
            episode.collision_count = 7

        stored = store.get(run.run_id)
        assert stored is not None
        assert [e.success for e in stored.episodes] == before
        assert all(e.collision_count != 7 for e in stored.episodes)

    def test_appending_to_the_callers_list_does_not_grow_the_record(self) -> None:
        run = _run_with_episodes()
        store = ValidationStore()
        store.save(run)
        count = len(run.episodes)
        extra = _run_with_episodes().episodes[0]

        run.episodes.append(extra)

        stored = store.get(run.run_id)
        assert stored is not None
        assert len(stored.episodes) == count

    def test_save_exists_snapshots_too(self) -> None:
        """The strict path has the same guarantee as the tolerant one."""
        run = _run_with_episodes()
        store = ValidationStore()
        assert store.save_exists(run) is True

        run.episodes.clear()

        stored = store.get(run.run_id)
        assert stored is not None and stored.episodes

    def test_the_failure_taxonomy_is_copied_too(self) -> None:
        run = _run_with_episodes()
        taxonomy = dict(run.scorecard.failure_taxonomy)
        assert taxonomy, "fixture must carry taxonomy entries"
        store = ValidationStore()
        store.save(run)

        run.scorecard.failure_taxonomy.clear()

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.scorecard.failure_taxonomy == taxonomy, (
            "clearing the caller's taxonomy emptied the stored record's"
        )
        assert (
            stored.scorecard.failure_taxonomy is not run.scorecard.failure_taxonomy
        ), "the stored record still shares the caller's dict"

