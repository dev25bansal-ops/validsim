"""Cross-backend store contract: identical semantics and identical reconstruction.

Two defects this file exists to prevent, both of which a single-backend test
cannot see:

1. **Divergent write semantics.** ``sqlite.save()`` used ``INSERT OR REPLACE``
   (overwrite) while ``postgres.save()`` used ``ON CONFLICT DO NOTHING``
   (first-write-wins). SQLite is the backend ``actions/validate/action.yml``
   selects by default, and ``jobs/worker.py`` reuses ``spec.run_id`` on every
   retry — so on the default path a crash-and-retry silently rewrote a recorded
   95.0/APPROVE verdict to 20.0/BLOCK, with no error and no log line. A store
   whose divergence is invisible from one backend is not a contract, it is three
   unrelated implementations. The declared contract is **append-only:
   first-write-wins, and a duplicate write never mutates the stored record**,
   because for a product whose premise is a trustworthy evidence record,
   overwriting a signed-off verdict is strictly worse than recording a duplicate.

2. **Partial reconstruction.** The scorecard is persisted as a JSON blob, so a
   newly added :class:`~validsim.engine.scorecard.Scorecard` field *is* written
   — but the reconstruct functions enumerate fields explicitly, so a field
   nobody remembered there silently reset to its default on reload. The
   adversarial-segment fields were already caught that way. Rather than trust
   whoever added the next field, :class:`TestCrossBackendScorecardParity`
   derives the field list from :func:`dataclasses.fields` at test time, so a
   future field is covered automatically and a backend that forgets one fails.

Both suites are parametrised over :data:`conftest.store_backend_ids` (memory,
sqlite) and, for the append-only contract, over *every* read path — ``get``,
``list_for_checkpoint`` and ``history`` — because "first write wins" is only
true if no query can surface the losing write. PostgreSQL is covered
separately in ``test_store_postgres.py`` against an injected fake connection,
since asserting its conflict clause needs no live server but running it does.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

import pytest
from conftest import (
    make_adversarial_scorecard,
    make_detailed_stored_run,
    make_persisted_scorecard,
    store_backend_ids,
)

from validsim.store.memory import ValidationStore

#: A high, obviously-passing verdict: the thing a retry must not be able to
#: erase.
_APPROVE_SCORE = 95.0
#: A low, obviously-failing verdict: what a crashed-then-retried re-execution
#: produced, and what used to overwrite the record.
_BLOCK_SCORE = 20.0
#: A run id the job worker genuinely reuses across retries (``spec.run_id``).
_RETRY_RUN_ID = "vrun-retry001"


def _verdict(run) -> tuple[float, str]:
    """The gate-facing pair: ``(composite_score, deploy_decision)``."""
    return (run.scorecard.composite_score, run.scorecard.deploy_decision)


def _appending_run(run_id: str, score: float, decision: str, created_at: str):
    """A minimal run whose only distinguishing features are id, score and stamp."""
    from conftest import make_stored_run

    return make_stored_run(
        make_persisted_scorecard(
            run_id,
            composite_score=score,
            deploy_decision=decision,
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# Contract 1: append-only (first-write-wins), identical on every backend
# ---------------------------------------------------------------------------


class TestAppendOnlyWriteContract:
    """A repeated ``save`` of one ``run_id`` must never change what is stored.

    Parametrised over every backend *and* every read path. SQLite and the
    in-memory store used to disagree here with PostgreSQL; the divergence was
    invisible from PostgreSQL's own tests and from SQLite's own tests, because
    each asserted only its own behaviour.
    """

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_second_save_cannot_overwrite_a_recorded_verdict(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        store = store_backends(backend_id)
        store.save(
            _appending_run(
                _RETRY_RUN_ID, _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
            )
        )
        # Exactly what a worker retry does: the same run id, freshly executed,
        # and on a partially-failed run a *different* (blocking) verdict.
        store.save(
            _appending_run(
                _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
            )
        )

        assert store.count() == 1, f"{backend_id}: a duplicate must not add a row"
        got = store.get(_RETRY_RUN_ID)
        assert got is not None
        assert _verdict(got) == (_APPROVE_SCORE, "APPROVE"), (
            f"{backend_id}: a re-save silently rewrote the recorded verdict "
            f"to {_verdict(got)}"
        )

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_every_read_path_returns_the_first_recorded_run(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """``get``, ``list_for_checkpoint`` and ``history`` must all agree.

        Checking only ``get`` would pass on a backend that stored two rows and
        happened to return the first from ``get`` — so the losing write would
        still be visible to ``/api/v1/validations``.
        """
        store = store_backends(backend_id)
        keep = _appending_run(
            _RETRY_RUN_ID, _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
        )
        store.save(keep)
        store.save(
            _appending_run(
                _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
            )
        )

        from_get = store.get(_RETRY_RUN_ID)
        from_list = store.list_for_checkpoint(keep.checkpoint_id)
        from_history = store.history()
        assert from_get is not None
        assert [ _verdict(r) for r in from_list ] == [(_APPROVE_SCORE, "APPROVE")]
        assert [_verdict(r) for r in from_history] == [(_APPROVE_SCORE, "APPROVE")]
        assert from_get == from_list[0] == from_history[0] == keep

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_duplicate_does_not_disturb_neighbouring_rows(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """A rejected duplicate leaves the rest of the log untouched."""
        store = store_backends(backend_id)
        first = _appending_run(
            "vrun-a0000001", _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
        )
        second = _appending_run(
            "vrun-b0000002", 88.0, "APPROVE", "2026-01-02T00:00:00+00:00"
        )
        store.save(first)
        store.save(second)
        store.save(
            _appending_run(
                "vrun-a0000001", _BLOCK_SCORE, "BLOCK", "2026-03-03T00:00:00+00:00"
            )
        )
        assert store.count() == 2
        assert [r.run_id for r in store.history()] == [
            "vrun-a0000001",
            "vrun-b0000002",
        ]
        assert store.get("vrun-a0000001") == first  # type: ignore[arg-type]
        assert store.get("vrun-b0000002") == second  # type: ignore[arg-type]

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_delete_then_save_is_the_sanctioned_correction_path(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """An explicit ``delete`` still frees the id for a corrected record.

        Append-only must not mean "frozen": an operator who needs to correct a
        run on the record deletes it deliberately and re-records it. The
        correction is then visible, which is the point.
        """
        store = store_backends(backend_id)
        store.save(
            _appending_run(
                _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
            )
        )
        corrected = _appending_run(
            _RETRY_RUN_ID, _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
        )
        assert store.delete(_RETRY_RUN_ID) is True
        store.save(corrected)
        got = store.get(_RETRY_RUN_ID)
        assert got is not None and _verdict(got) == (_APPROVE_SCORE, "APPROVE")
        assert store.count() == 1


class TestStrictWriteContract:
    """``save_exists`` is the opt-in strict variant of :meth:`save`.

    ``save`` deliberately swallows a duplicate (a retry is normal traffic), but
    a caller that reuses a run id it does not own -- the job worker does, via
    ``spec.run_id`` -- must be able to find out whether its verdict is actually
    the one on the record. ``engine/pipeline.py`` uses this, and it is the only
    way a caller can avoid returning a scorecard the store never accepted.
    """

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_first_write_reports_true_then_false(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        store = store_backends(backend_id)
        first = _appending_run(
            _RETRY_RUN_ID, _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
        )
        duplicate = _appending_run(
            _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
        )
        assert store.save_exists(first) is True
        assert store.save_exists(duplicate) is False
        got = store.get(_RETRY_RUN_ID)
        assert got is not None and _verdict(got) == (_APPROVE_SCORE, "APPROVE")
        assert store.count() == 1

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_rejected_duplicate_is_identical_to_save(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """``save_exists`` must be ``save`` plus a report, not a third semantic."""
        store = store_backends(backend_id)
        store.save_exists(
            _appending_run(
                _RETRY_RUN_ID, _APPROVE_SCORE, "APPROVE", "2026-01-01T00:00:00+00:00"
            )
        )
        store.save_exists(
            _appending_run(
                _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
            )
        )
        got = store.get(_RETRY_RUN_ID)
        assert got is not None and _verdict(got) == (_APPROVE_SCORE, "APPROVE")
        assert store.count() == 1

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_delete_then_save_exists_reports_true_again(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        store = store_backends(backend_id)
        store.save_exists(
            _appending_run(
                _RETRY_RUN_ID, _BLOCK_SCORE, "BLOCK", "2026-01-01T00:00:00+00:00"
            )
        )
        assert store.delete(_RETRY_RUN_ID) is True
        assert (
            store.save_exists(
                _appending_run(
                    _RETRY_RUN_ID,
                    _APPROVE_SCORE,
                    "APPROVE",
                    "2026-01-01T00:00:00+00:00",
                )
            )
            is True
        )


# ---------------------------------------------------------------------------
# Contract 2: every Scorecard field is reconstructed, on every backend
# ---------------------------------------------------------------------------


class TestCrossBackendScorecardParity:
    """Field-for-field reconstruction equality across all backends.

    The assertion is derived from :func:`dataclasses.fields` rather than written
    out, so a field added to :class:`Scorecard` tomorrow is compared tomorrow.
    A hand-written list would go stale at exactly the moment it matters — the
    moment someone adds a field and forgets one of the two backends.
    """

    @staticmethod
    def _field_names() -> list[str]:
        return [f.name for f in dataclasses.fields(_scorecard_type())]

    def test_fixture_covers_every_scorecard_field(self) -> None:
        """Guard the guard: the fixture must be non-default on every field.

        If ``make_adversarial_scorecard`` ever leaves a field at its default,
        this suite would silently stop covering it — the failure mode it exists
        to prevent, reproduced one level up. Compared field-by-field against a
        bare :class:`Scorecard` (i.e. the dataclass defaults themselves) rather
        than against another fixture, so the check stays correct as fields land.
        """
        from validsim.engine.scorecard import Scorecard

        rich = make_adversarial_scorecard("vrun-fixt0001")
        blank = Scorecard(
            run_id="vrun-fixt0002",
            checkpoint_id="",
            task_id="",
            composite_score=0.0,
            success_rate=0.0,
            safety_score=0.0,
            robustness_score=0.0,
            regression_delta=None,
            confidence_interval=None,
            deploy_decision="APPROVE",
        )
        # Identity fields are excluded: they must match across the two objects
        # for the comparison to mean anything, so they cannot "differ".
        identity = {"run_id", "checkpoint_id", "task_id", "deploy_decision"}
        still_default = [
            name
            for name in self._field_names()
            if name not in identity and getattr(rich, name) == getattr(blank, name)
        ]
        assert still_default == [], (
            "make_adversarial_scorecard leaves these Scorecard fields at their "
            f"default, so the parity suite would not cover them: {still_default}"
        )

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_every_field_survives_the_round_trip(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        store = store_backends(backend_id)
        card = make_adversarial_scorecard("vrun-parity01")
        store.save(make_detailed_stored_run(card))

        got = store.get("vrun-parity01")
        assert got is not None
        by_name = {name: getattr(got.scorecard, name) for name in self._field_names()}
        assert by_name == {
            name: getattr(card, name) for name in self._field_names()
        }, f"{backend_id}: scorecard fields did not round-trip field-for-field"
        # The whole record, not just the scorecard, must match exactly.
        assert got == make_detailed_stored_run(card)

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_tuple_typed_fields_come_back_as_tuples(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """JSON has no tuple type, so a naive restore yields lists.

        ``confidence_interval`` and ``block_reasons`` are annotated as tuples
        and compared by tuple identity in every consumer, so a list silently
        breaks equality (and hashing) on the reconstructed record.
        """
        store = store_backends(backend_id)
        store.save(make_detailed_stored_run(make_adversarial_scorecard("vrun-tup0001")))
        got = store.get("vrun-tup0001")
        assert got is not None
        assert isinstance(got.scorecard.confidence_interval, tuple)
        assert got.scorecard.confidence_interval == (0.1111, 0.9999)
        assert isinstance(got.scorecard.block_reasons, tuple)
        assert len(got.scorecard.block_reasons) == 2

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_empty_and_zero_valued_fields_survive(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """Falsy values must round-trip as themselves, not as a missing key.

        A ``.get(key, default)`` restore is correct for a legacy row but wrong
        for a *present* key holding a falsy value: an empty
        ``confidence_interval=None``, an empty ``block_reasons=()`` or a zero
        ``adversarial_episode_count`` must not be confused with "the field did
        not exist yet".
        """
        store = store_backends(backend_id)
        card = make_adversarial_scorecard("vrun-falsy01")
        card = dataclasses.replace(
            card,
            confidence_interval=None,
            block_reasons=(),
            failure_taxonomy={},
            adversarial_episode_count=0,
            adversarial_success_rate=0.0,
            regression_delta=0.0,
        )
        from conftest import make_stored_run

        store.save(make_stored_run(card))
        got = store.get("vrun-falsy01")
        assert got is not None
        assert got.scorecard == card
        assert got.scorecard.confidence_interval is None
        assert got.scorecard.block_reasons == ()
        assert got.scorecard.adversarial_success_rate == 0.0
        assert got.scorecard.regression_delta == 0.0

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_episode_and_detail_fields_survive(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """The non-scorecard half of the record must match across backends too."""
        store = store_backends(backend_id)
        run = make_detailed_stored_run(make_adversarial_scorecard("vrun-detail1"))
        store.save(run)
        got = store.get("vrun-detail1")
        assert got is not None
        assert got.evaluation == run.evaluation
        assert got.safety == run.safety
        assert got.episodes == run.episodes
        assert got.baseline_run_id == run.baseline_run_id
        assert got.regression == run.regression
        assert got.summary() == run.summary()


def _scorecard_type():
    """The ``Scorecard`` class, imported lazily to keep the header short."""
    from validsim.engine.scorecard import Scorecard

    return Scorecard


# ---------------------------------------------------------------------------
# Contract 3: the pipeline must never hand back an unstored verdict
# ---------------------------------------------------------------------------
#
# This is the layer where the divergence was actually reachable.
# ``engine/pipeline.py`` is the single sequence the API, the CLI and the job
# worker all funnel through, it returns ``store.save(...)``'s value straight to
# its caller, and ``jobs/worker.py`` passes its own ``spec.run_id`` -- the one
# case where a run id can be reused. On an append-only store that means a retry
# produces a *newly built* scorecard the store never accepted, which is the
# object the API would then return and the worker would record as its result.
# The pipeline therefore has to re-read the record when a write is rejected.


class TestPipelineReturnsTheStoredVerdict:
    """``run_and_score`` returns the record the store holds, not the one it built."""

    @staticmethod
    def _task() -> "object":
        from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig

        return TaskConfig(
            task_id="pick-place",
            robot=RobotSpec(name="franka"),
            environment=EnvironmentSpec(name="kitchen"),
            episodes=20,
            adversarial_count=4,
        )

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_retry_with_the_same_run_id_returns_the_stored_record(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """The worker-retry path: same ``spec.run_id``, verdict stays put.

        ``run_and_score`` is deterministic for a fixed ``(checkpoint_id, task)``
        seed, so re-running reproduces the scorecard; the interesting part is
        that a *different* backend (a partially-failed retry) would build a
        different one. Either way the store keeps the first record and the
        pipeline must report that record.
        """
        from validsim.engine.pipeline import run_and_score
        from validsim.sim.runner import MockIsaacBackend

        store = store_backends(backend_id)
        first = run_and_score(
            self._task(),
            "ckpt-retry",
            store,
            backend=MockIsaacBackend(base_success_rate=0.95),
            run_id=_RETRY_RUN_ID,
        )
        retry = run_and_score(
            self._task(),
            "ckpt-retry",
            store,
            backend=MockIsaacBackend(base_success_rate=0.05),
            run_id=_RETRY_RUN_ID,
        )
        assert store.count() == 1
        assert retry == first
        assert _verdict(retry) == _verdict(first)
        assert retry == store.get(_RETRY_RUN_ID)

    def test_api_returned_verdict_matches_the_stored_one(self) -> None:
        """The divergence was observable through ``POST /validations``.

        The audit noted that after a re-save the API could return a scorecard
        the system of record never accepted. This asserts the invariant at the
        seam where it was reported: whatever the pipeline hands back is exactly
        what a *fresh* reader of the store sees.
        """
        from validsim.engine.pipeline import run_and_score
        from validsim.sim.runner import MockIsaacBackend

        store = ValidationStore()
        returned = run_and_score(
            self._task(),
            "ckpt-retry",
            store,
            backend=MockIsaacBackend(base_success_rate=0.95),
            run_id=_RETRY_RUN_ID,
        )
        for _ in range(2):  # two retries, as a flaky worker would do
            returned = run_and_score(
                self._task(),
                "ckpt-retry",
                store,
                backend=MockIsaacBackend(base_success_rate=0.05),
                run_id=_RETRY_RUN_ID,
            )
        fresh_reader = store.get(_RETRY_RUN_ID)
        assert fresh_reader is not None
        assert returned.scorecard.composite_score == fresh_reader.scorecard.composite_score
        assert returned.scorecard.deploy_decision == fresh_reader.scorecard.deploy_decision
        assert returned.scorecard == fresh_reader.scorecard


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
