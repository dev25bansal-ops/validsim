"""The store must detach a run from every mutable container its caller holds.

The defect
----------
``ValidationStore.save``/``save_exists`` return **the caller's own object** (a
contract ``tests/test_store_memory.py:46`` pins with ``is``), so the caller keeps
a live handle on stored state. ``_snapshot`` therefore has to copy deeply enough
that no mutation reachable through that handle can rewrite the record.

It did not. It enumerated only **2 of the 5** caller-reachable mutable
containers, and left the other three shared by identity:

* ``regression.items``
* ``evaluation.per_task_success``
* ``evaluation.failure_taxonomy``
* each ``episode.joint_states_summary``

The consequence is a *self-contradicting* record. Save a genuine BLOCK verdict
and then mutate only the returned handle: ``regression.items.clear()`` flips the
stored report's ``has_regressions`` to ``False`` and ``worst_severity`` from
``critical`` to ``info``, while the separately-stored ``scorecard`` blob still
says BLOCK with ``regression_delta -0.6667``. A deploy gate reading that record
sees "BLOCK, and nothing regressed" at once.

It also made ``VALIDSIM_STORE=memory`` disagree with sqlite/postgres, which
serialise on write and were never affected. That parity gap is what
``tests/test_store_parity.py`` asserts, and it is why this shipped green: the
existing snapshot coverage pins only ``episodes`` and
``scorecard.failure_taxonomy`` -- precisely the two that were already copied.

The fix is structural (``_copy_value`` / ``_copy_dataclass``): every mutable
container reachable from the record is rebuilt, so a field added later is covered
by construction rather than by remembering to extend a hand-written list.

Every expected value here was measured by running the code, not inferred.

Reproduce::

    python -m pytest tests/test_store_snapshot_agent.py -q
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore

_RUN_ID = "vrun-snapshot01"


def _episode(seed: int) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-snap{seed:04d}",
        task_id="pick-place",
        seed=seed,
        success=seed % 2 == 0,
        collision_count=seed % 3,
        max_contact_force_n=10.0 + seed,
        min_human_distance_m=1.0 + seed,
        failure_mode=None if seed % 2 == 0 else "collision",
        duration_s=5.0 + seed,
        joint_states_summary={"position_rms": 0.1 * seed, "dof": float(seed)},
        randomization_level="full",
    )


def _block_run() -> StoredRun:
    """A run whose every caller-reachable container is populated and mutable.

    Deliberately a *BLOCK* verdict with real regressions, so a leak shows up as a
    contradiction (a BLOCK scorecard beside a regression report claiming nothing
    regressed) rather than as an obviously-empty record.
    """
    from conftest import make_scorecard

    episodes = [_episode(i) for i in range(4)]
    scorecard = make_scorecard(
        run_id=_RUN_ID,
        composite_score=60.42,
        success_rate=0.606,
        safety_score=70.0,
        deploy_decision="BLOCK",
        threshold=85.0,
        regression_delta=-0.6667,
        confidence_interval=(0.42, 0.79),
        failure_taxonomy={"collision": 2, "timeout": 1},
    )
    return StoredRun(
        run_id=scorecard.run_id,
        checkpoint_id=scorecard.checkpoint_id,
        task_id=scorecard.task_id,
        created_at=scorecard.created_at,
        scorecard=scorecard,
        evaluation=EvaluationResult(
            total_episodes=4,
            success_count=2,
            success_rate=0.5,
            per_task_success={"t1": 0.333, "t2": 0.9},
            failure_taxonomy={"collision": 2, "timeout": 1},
            mean_duration_s=6.5,
        ),
        safety=SafetyResult(
            collisions_per_episode=0.5,
            max_force_exceeded_rate=0.0,
            min_human_proximity_m=1.0,
            proximity_violation_rate=0.0,
            safety_score=70.0,
        ),
        episodes=episodes,
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem("success_rate", 0.9, 0.2333, -0.6667, 0.001, True, "critical"),
                RegressionItem("mean_duration_s", 5.0, 6.5, 1.5, 0.4, False, "info"),
            ]
        ),
    )


def _mutable_nodes(value: Any, _seen: set[int] | None = None) -> list[tuple[str, int]]:
    """Every ``id()`` of a **mutable** node reachable from *value*.

    Walks dataclasses, dicts, lists, tuples and sets, with an identity-based
    ``seen`` set so a self-referential structure terminates instead of hanging.

    Only genuinely mutable containers are reported. ``tuple`` is traversed (its
    elements can be mutable) but never *reported*: a tuple cannot be reassigned
    in place, so sharing one is safe. Reporting it would produce a false
    positive for CPython's empty-tuple singleton, which is the one object every
    ``block_reasons = ()`` in the process shares by definition -- and a guard
    that always fails is a guard that gets deleted.

    Returns ``(label, id)`` pairs so a failure can name the offending field.
    """
    if _seen is None:
        _seen = set()
    if id(value) in _seen:
        return []
    _seen.add(id(value))

    out: list[tuple[str, int]] = []
    children: list[Any] = []

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            if field.init:
                children.append(getattr(value, field.name))
    elif isinstance(value, dict):
        children.extend(value.keys())
        children.extend(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        children.extend(value)

    for child in children:
        if dataclasses.is_dataclass(child) and not isinstance(child, type):
            out.extend(_mutable_nodes(child, _seen))
        elif isinstance(child, (dict, list, set)):
            out.append((type(child).__name__, id(child)))
            out.extend(_mutable_nodes(child, _seen))
        elif isinstance(child, tuple):
            # Immutable container: traverse into it, but sharing it is harmless.
            out.extend(_mutable_nodes(child, _seen))

    return out


class TestTheThreePreviouslySharedContainersAreNowDetached:
    """The three containers the old hand-enumerated ``_snapshot`` missed.

    Existing coverage pins only ``episodes`` and ``scorecard.failure_taxonomy``,
    so these three had no test at all -- which is precisely why they shipped
    shared. Each test mutates exactly one container through the handle the caller
    kept and requires the stored record to be untouched.
    """

    def test_clearing_regression_items_does_not_rewrite_the_record(self) -> None:
        """The self-contradiction case, and the one that matters most.

        Pre-fix, clearing the list left the stored report claiming zero
        regressions at ``info`` severity while the separately-stored scorecard
        still said BLOCK with ``regression_delta -0.6667``. A gate reading that
        record sees "BLOCK, and nothing regressed" simultaneously.
        """
        run = _block_run()
        assert run.regression is not None and run.regression.has_regressions
        store = ValidationStore()
        store.save(run)

        run.regression.items.clear()

        stored = store.get(run.run_id)
        assert stored is not None and stored.regression is not None
        assert stored.regression.has_regressions is True, (
            "clearing the caller's regression list silently un-broke the stored "
            "regression report while the scorecard still said BLOCK"
        )
        assert stored.regression.worst_severity == "critical"
        assert len(stored.regression.items) == 2

    def test_mutating_a_regression_item_does_not_rewrite_the_record(self) -> None:
        """Clearing the list is not the only leak: the elements are mutable too.

        ``RegressionItem`` is a **frozen** dataclass, so a caller's handle on an
        *element* cannot reassign its fields -- but the *list* holding them is
        ordinary, and that is the reachable mutation. So the leak this pins is
        ``items`` itself: replacing, reordering or clearing entries through the
        caller's handle must not reach the record.
        """
        run = _block_run()
        assert run.regression is not None
        store = ValidationStore()
        store.save(run)

        items = run.regression.items
        keep = items[0]
        items.clear()
        items.append(keep)  # same element, different position and length
        items.reverse()

        stored = store.get(run.run_id)
        assert stored is not None and stored.regression is not None
        assert len(stored.regression.items) == 2
        assert stored.regression.items[0].significant is True
        assert stored.regression.items[0].severity == "critical"
        assert stored.regression.items[0].delta == -0.6667
        assert stored.regression.worst_severity == "critical"

    def test_per_task_success_is_detached(self) -> None:
        """A caller rewriting its own per-task rates must not alter the record."""
        run = _block_run()
        assert run.evaluation.per_task_success["t1"] == 0.333
        store = ValidationStore()
        store.save(run)

        run.evaluation.per_task_success["t1"] = 1.0
        run.evaluation.per_task_success["injected"] = 0.5

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.evaluation.per_task_success == {"t1": 0.333, "t2": 0.9}, (
            "the stored per-task success rates were rewritten through the "
            "caller's handle"
        )

    def test_evaluation_failure_taxonomy_is_detached(self) -> None:
        """A fabricated failure mode must not appear in the stored record.

        This is the evidence-integrity case: a taxonomy the caller invents after
        the fact would otherwise be read back as though the run had produced it.
        """
        run = _block_run()
        store = ValidationStore()
        store.save(run)

        run.evaluation.failure_taxonomy["injected"] = 999
        run.evaluation.failure_taxonomy.clear()

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.evaluation.failure_taxonomy == {"collision": 2, "timeout": 1}, (
            "the stored evaluation taxonomy accepted a post-save fabrication"
        )

    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_each_episode_joint_states_summary_is_detached(self, index: int) -> None:
        """Every episode's summary, not just the first.

        Parametrised because a walk that only rebuilt ``episodes[0]`` would pass a
        single-index test while leaving the rest shared.
        """
        run = _block_run()
        before = dict(run.episodes[index].joint_states_summary)
        assert before, "fixture must carry a non-empty joint states summary"
        store = ValidationStore()
        store.save(run)

        run.episodes[index].joint_states_summary["position_rms"] = 999.0
        run.episodes[index].joint_states_summary.clear()

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.episodes[index].joint_states_summary == before, (
            f"episode {index}'s joint_states_summary was shared with the caller"
        )


class TestNoSharedMutableNodesRemain:
    """Requirement 2: a structural identity walk, not a field-by-field list."""

    def test_no_mutable_node_is_shared_with_the_caller(self) -> None:
        """Zero shared identities between the caller's object and the snapshot.

        The five field-level tests above each cover one known container. This
        covers *whatever the walk reaches*, so a mutable field added to
        ``StoredRun`` or to a nested artifact in future is covered by
        construction -- the property the structural fix actually provides, and the
        one a hand-enumerated list cannot.
        """
        run = _block_run()
        store = ValidationStore()
        store.save(run)

        stored = store.get(run.run_id)
        assert stored is not None

        caller_ids = {node_id for _label, node_id in _mutable_nodes(run)}
        stored_ids = {node_id for _label, node_id in _mutable_nodes(stored)}

        shared = caller_ids & stored_ids
        assert not shared, (
            f"{len(shared)} mutable node(s) are shared by identity between the "
            "caller's run and the stored snapshot -- every mutable container "
            "reachable from the record must be rebuilt"
        )

    def test_the_walk_actually_reaches_the_containers(self) -> None:
        """The detector is self-tested, so the test above cannot pass vacuously.

        Same pattern as the mixed-case run-id guard: an identity walk that
        silently visited nothing would report "no shared nodes" for a store that
        shares all of them. So the walk's own reach is asserted against the
        fixture's known contents.
        """
        run = _block_run()
        found = {label for label, _ in _mutable_nodes(run)}
        assert "dict" in found and "list" in found, (
            f"the identity walk reached only {sorted(found)}; it is not "
            "traversing the containers it is supposed to check"
        )
        assert len(_mutable_nodes(run)) >= 8, (
            "the fixture is too small for the walk to mean anything"
        )


class TestTheSaveContractSurvivesTheDeepCopy:
    """Requirement 3: a deep copy must not quietly break the ``is`` contract."""

    def test_save_still_returns_its_argument(self) -> None:
        """``save`` hands back the caller's object, not a detached copy.

        A fix that returned the snapshot would satisfy every isolation test above
        while breaking this documented contract -- and would break callers that
        legitimately use the return value as "the run I just saved".
        """
        run = _block_run()
        store = ValidationStore()
        assert store.save(run) is run

    def test_save_exists_still_returns_a_bool_and_leaves_the_argument_intact(
        self,
    ) -> None:
        run = _block_run()
        store = ValidationStore()
        assert store.save_exists(run) is True
        assert run.regression is not None and run.regression.has_regressions

    def test_get_returns_an_equal_but_detached_record(self) -> None:
        """Requirement 4: equal by value, distinct by identity.

        Equality must survive the structural copy (``==`` on the dataclasses), or
        every store-contract test in the suite would break -- so this pins both
        halves at once.
        """
        run = _block_run()
        store = ValidationStore()
        store.save(run)

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored == run, "the snapshot is not equal to the original"
        assert stored is not run
        assert stored.scorecard is not run.scorecard
        assert stored.evaluation is not run.evaluation
        assert stored.regression is not run.regression
        assert all(a is not b for a, b in zip(stored.episodes, run.episodes))


class TestImmutableFieldsKeepTheirTypes:
    """Requirement 7: a tuple must not come back as a list.

    ``_copy_value`` rebuilds tuples via ``tuple(...)`` rather than ``[...]``, so
    the scorecard's tuple-annotated fields stay tuples. A generic
    "copy every container" implementation that rebuilt them as lists would break
    ``==`` against a freshly built scorecard and silently change the JSON shape
    of every export, because ``json.dumps`` renders a tuple as a list but a list
    round-tripped through the store comes back as a list on the SQL backends.
    """

    def test_confidence_interval_survives_as_a_tuple(self) -> None:
        run = _block_run()
        assert isinstance(run.scorecard.confidence_interval, tuple)
        store = ValidationStore()
        store.save(run)

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.scorecard.confidence_interval == (0.42, 0.79)
        assert isinstance(stored.scorecard.confidence_interval, tuple), (
            "confidence_interval came back as "
            f"{type(stored.scorecard.confidence_interval).__name__}, not tuple"
        )

    def test_block_reasons_survives_as_a_tuple(self) -> None:
        run = _block_run()
        reasons = ("adversarial success rate below floor", "composite below threshold")
        object.__setattr__(run.scorecard, "block_reasons", reasons)
        store = ValidationStore()
        store.save(run)

        stored = store.get(run.run_id)
        assert stored is not None
        assert stored.scorecard.block_reasons == reasons
        assert isinstance(stored.scorecard.block_reasons, tuple)

    def test_mutating_a_list_on_the_snapshot_does_not_touch_the_caller(self) -> None:
        """The reverse direction: the stored record is not a window onto the caller.

        Guards the isolation symmetrically -- reading a mutable field out of the
        stored record and mutating it must not reach back into the caller's object.
        """
        run = _block_run()
        store = ValidationStore()
        store.save(run)
        stored = store.get(run.run_id)
        assert stored is not None and stored.evaluation is not None

        stored.evaluation.per_task_success["t1"] = 1.0
        stored.evaluation.failure_taxonomy["injected"] = 1

        assert run.evaluation.per_task_success["t1"] == 0.333
        assert "injected" not in run.evaluation.failure_taxonomy


class TestSelfReferentialStructuresTerminate:
    """Requirement 6: the depth cap must stop a cycle, not hang the suite."""

    def test_a_self_referential_dict_terminates(self) -> None:
        """A dict that contains itself must not hang the copy.

        ``_copy_value`` recurses with a depth cap, so a cyclic structure bottoms
        out and returns rather than exhausting the stack. The assertion is on
        *termination*, not on the shape of the result -- the cap deliberately
        stops copying below its limit, so the deep node is shared rather than
        rebuilt. What matters is that the call returns.

        No timeout guard is used: a recursion cap is a hard bound, so a regression
        would raise ``RecursionError`` here rather than hang. A timeout would add
        a dependency and a flake surface for a failure mode that cannot hang.
        """
        cyclic: dict[str, Any] = {"level": 0}
        cyclic["self"] = cyclic

        from validsim.store.memory import _copy_value

        copied = _copy_value(cyclic)
        assert copied is not None
        assert copied["level"] == 0
        # The cap stopped the walk, so the cycle is broken somewhere below the
        # limit; either a rebuilt node or the shared one is acceptable.
        assert isinstance(copied, dict)

    def test_a_deeply_nested_structure_terminates(self) -> None:
        """Depth far beyond the cap must still return.

        ``_MAX_SNAPSHOT_DEPTH`` is 12. A 200-level nested dict is well past it,
        and the point is that the copy bottoms out instead of recursing 200 deep
        or raising.
        """
        from validsim.store.memory import _copy_value

        deep: dict[str, Any] = {"leaf": 1}
        for _ in range(200):
            deep = {"child": deep}

        copied = _copy_value(deep)
        assert isinstance(copied, dict)


class TestMemoryAgreesWithSqliteAfterTheDeepCopy:
    """Requirement 5: the deep copy must not introduce a backend divergence.

    The point of the fix is that ``VALIDSIM_STORE=memory`` regains parity with the
    persistent backends. Those serialise on write, so they were never exposed to
    the shared-mutable hole; before the fix, memory was the *only* backend whose
    record could be rewritten after the fact. If the copy were done wrong -- e.g.
    dropping a container, or turning a tuple into a list -- this is the assertion
    that would notice, because the two backends would then reconstruct different
    objects from identical writes.
    """

    def test_memory_and_sqlite_reconstruct_identically(self, make_store) -> None:
        run = _block_run()
        mem = ValidationStore()
        lite = make_store("snapshot-parity.db")

        mem.save(run)
        lite.save(run)

        from_mem = mem.get(run.run_id)
        from_lite = lite.get(run.run_id)
        assert from_mem is not None and from_lite is not None

        assert from_mem.scorecard == from_lite.scorecard
        assert from_mem.evaluation == from_lite.evaluation
        assert from_mem.safety == from_lite.safety
        assert from_mem.regression == from_lite.regression
        assert from_mem.episodes == from_lite.episodes
        assert from_mem == from_lite, (
            "memory and sqlite reconstructed different records from one write; "
            "the deep copy changed what the in-memory store stores"
        )

    def test_a_post_save_mutation_leaves_both_backends_agreeing(
        self, make_store
    ) -> None:
        """The defect made memory diverge from sqlite; both must now be immune.

        Pre-fix this drove memory's ``has_regressions`` to ``False`` while
        sqlite -- which JSON-serialised at save time -- kept it ``True``. That
        disagreement between backends holding the *same write* is the parity
        break, and it is what made the bug invisible to any single-backend test.
        """
        run = _block_run()
        mem = ValidationStore()
        lite = make_store("snapshot-parity2.db")
        mem.save(run)
        lite.save(run)

        assert run.regression is not None
        run.regression.items.clear()
        run.evaluation.failure_taxonomy["injected"] = 999
        run.evaluation.per_task_success["t1"] = 1.0

        from_mem = mem.get(run.run_id)
        from_lite = lite.get(run.run_id)
        assert from_mem is not None and from_lite is not None
        assert from_mem.regression == from_lite.regression
        assert from_mem.evaluation == from_lite.evaluation
        assert from_mem.regression is not None
        assert from_mem.regression.has_regressions is True
        assert "injected" not in from_mem.evaluation.failure_taxonomy
        assert from_mem.evaluation.per_task_success["t1"] == 0.333
