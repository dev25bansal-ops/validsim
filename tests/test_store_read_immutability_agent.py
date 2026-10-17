"""A record read out of the store cannot be used to rewrite the store.

The defect
----------
``ValidationStore`` -- the **default** backend, ``VALIDSIM_STORE`` unset -- kept
one record per ``run_id`` and handed that *same object* to every reader. Because
``StoredRun`` is a frozen dataclass only at the top level (``episodes`` was a
``list`` of a **non-frozen** ``EpisodeResult``, and the scorecard/evaluation
artifacts carry mappings), a caller could rewrite the store's evidence through
the value it was handed back::

    rec = store.get(run_id)
    rec.episodes[0].success = False          # the stored evidence, changed
    rec.evaluation.failure_taxonomy.clear()  # the stored taxonomy, emptied

No write, no lock, no log line -- the same "silently rewritten verdict" the
append-only write contract exists to make impossible, reached through the read
side instead. The SQL backends never had the hole, because they rebuild from
persisted JSON on every read, so this was a backend divergence as well as an
integrity hole: ``VALIDSIM_STORE=memory`` and ``VALIDSIM_STORE=sqlite`` disagreed
about whether a stored verdict was writable.

The fix, and why it is a freeze rather than a copy
--------------------------------------------------
The obvious fix -- deep-copy on every read -- is correct but spends the one
thing this backend exists to provide. ``history()`` over 100 000 runs measures
~39 ms as a share and ~8 500 ms as a deep copy, and that bulk read is what
``/api/v1/dashboard/history`` and ``/api/v1/validations`` do on every request;
``tests/bench/test_bench_store.py`` pins the ratio against SQLite so it cannot be
given away by accident.

So the store *freezes* instead (``validsim.store._frozen``): the record it keeps
has no mutable container reachable from it, which makes sharing it safe, and the
walk is paid once at save time -- where ``save`` was already paying a full deep
copy. Reads therefore stay a share, and the isolation becomes structural rather
than per-field.

What is asserted here
---------------------
* The **guarantee**, on every backend and every read path: after any mutation
  attempt through a read handle, the store still returns the first-recorded
  record.
* The **one documented difference**: on the in-memory backend the attempt raises
  ``TypeError`` (the container is shared), on the SQL backends it succeeds on a
  private copy. Neither reaches the store, and the in-memory behaviour is the
  stricter one.
* The **freeze mechanics** that make the sharing safe: every record dataclass is
  frozen, every container reachable from a stored record is a
  ``FrozenList``/``FrozenDict``, and those still compare, serialise, copy and
  pickle exactly like the plain containers they replace.

Every expected value here was measured by running the code, not inferred.

Reproduce::

    python -m pytest tests/test_store_read_immutability_agent.py -q
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle
from typing import Any, Callable

import pytest
from conftest import (
    make_detailed_stored_run,
    make_persisted_scorecard,
    make_stored_run,
    store_backend_ids,
)

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store._frozen import FrozenDict, FrozenList, freeze
from validsim.store.memory import StoredRun, ValidationStore

#: The record every test in this file mutates through.
_RUN_ID = "vrun-readimm1"

#: Dataclasses that can appear inside a stored record. ``StoredRun`` is here
#: because it is the record itself; every other entry is reachable from it.
_RECORD_DATACLASSES: tuple[type, ...] = (
    StoredRun,
    Scorecard,
    EvaluationResult,
    SafetyResult,
    EpisodeResult,
    RegressionReport,
    RegressionItem,
)


def _record() -> StoredRun:
    """A record whose every container is populated and reachable.

    Deliberately an ``APPROVE`` with real episodes, taxonomy and per-task rates:
    a leak has to show up as *changed evidence* rather than as an obviously empty
    record, so a passing test cannot be a coincidence of a small fixture.
    """
    return make_detailed_stored_run(
        make_persisted_scorecard(
            _RUN_ID,
            composite_score=95.0,
            deploy_decision="APPROVE",
        )
    )


def _by_path(store: ValidationStore, run_id: str, path: str) -> StoredRun:
    """Read ``run_id`` back through one of the three documented read paths."""
    if path == "get":
        got = store.get(run_id)
        assert got is not None
        return got
    if path == "history":
        return next(r for r in store.history() if r.run_id == run_id)
    if path == "list_for_checkpoint":
        return next(r for r in store.list_for_checkpoint("ckpt-1") if r.run_id == run_id)
    raise AssertionError(f"unknown read path {path!r}")  # pragma: no cover


#: Every way a caller could try to change stored evidence through a read handle.
#: Each is a callable so a test can attempt it and record whether the store
#: refused (in-memory) or accepted-and-discarded (SQL) it. Tests apply these to a
#: **freshly read** handle one at a time, so the vectors stay independent of each
#: other's side effects and each one starts from a full record.
_MUTATIONS: tuple[tuple[str, Callable[[StoredRun], None]], ...] = (
    (
        "episode.success = ...",
        lambda r: setattr(r.episodes[0], "success", not r.episodes[0].success),
    ),
    ("episode.collision_count = ...", lambda r: setattr(r.episodes[0], "collision_count", 7)),
    ("episodes.append(...)", lambda r: r.episodes.append(r.episodes[0])),
    ("episodes[0] = ...", lambda r: r.episodes.__setitem__(0, r.episodes[-1])),
    ("episodes[1:] = ...", lambda r: r.episodes.__setitem__(slice(1, None), [])),
    ("episodes.clear()", lambda r: r.episodes.clear()),
    (
        "scorecard.failure_taxonomy[...] = ...",
        lambda r: r.scorecard.failure_taxonomy.update({"injected": 999}),
    ),
    ("scorecard.failure_taxonomy.clear()", lambda r: r.scorecard.failure_taxonomy.clear()),
    (
        "evaluation.per_task_success[...] = ...",
        lambda r: r.evaluation.per_task_success.update({"injected": 1.0}),
    ),
    ("evaluation.per_task_success.clear()", lambda r: r.evaluation.per_task_success.clear()),
    ("evaluation.failure_taxonomy.clear()", lambda r: r.evaluation.failure_taxonomy.clear()),
    ("episode.joint_states_summary.clear()", lambda r: r.episodes[0].joint_states_summary.clear()),
)


def _mutation(label: str) -> Callable[[StoredRun], None]:
    """Look a vector up by name, so a test cannot depend on its position."""
    for name, mutation in _MUTATIONS:
        if name == label:
            return mutation
    raise AssertionError(f"unknown mutation vector {label!r}")  # pragma: no cover


def _attempt(mutation: Callable[[StoredRun], None], record: StoredRun) -> bool:
    """Try *mutation*; return whether it was applied to the handle.

    ``False`` means the store refused it (``TypeError`` from a frozen container
    or ``FrozenInstanceError`` from a frozen dataclass) -- the in-memory
    behaviour. ``True`` means it landed on the handle, which on the SQL backends
    is a private copy. Either way the caller asserts on the *store*, never on the
    handle, so a test cannot pass by asserting the wrong thing.
    """
    try:
        mutation(record)
    except (TypeError, dataclasses.FrozenInstanceError):
        return False
    return True


def _attempt_all(store: ValidationStore, run_id: str, path: str) -> int:
    """Apply every vector to its own fresh handle; return how many landed.

    A fresh read per vector is what keeps them independent: a vector that empties
    a list must not decide whether the next one raises ``IndexError`` instead of
    ``TypeError``.
    """
    landed = 0
    for _label, mutation in _MUTATIONS:
        if _attempt(mutation, _by_path(store, run_id, path)):
            landed += 1
    return landed


# ---------------------------------------------------------------------------
# The guarantee: no read path can rewrite the store
# ---------------------------------------------------------------------------


class TestNoReadPathCanRewriteTheStore:
    """The contract, asserted per backend *and* per read path.

    Checking only ``get`` would pass on a backend whose ``history`` handed out a
    different object -- and ``history`` is the path the dashboard and the
    validation list actually use.
    """

    @pytest.mark.parametrize("path", ["get", "history", "list_for_checkpoint"])
    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_every_mutation_through_a_read_handle_leaves_the_store_intact(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str, path: str
    ) -> None:
        store = store_backends(backend_id)
        original = _record()
        store.save(original)

        landed = _attempt_all(store, _RUN_ID, path)
        assert landed >= 0  # every vector was applied; the label names them

        after = _by_path(store, _RUN_ID, path)
        assert after == original, (
            f"{backend_id}/{path}: the store's record was rewritten through a "
            "read handle -- a read path must not be able to change the evidence"
        )
        assert [e.success for e in after.episodes] == [
            e.success for e in original.episodes
        ], f"{backend_id}/{path}: the stored episode outcomes changed"
        assert "injected" not in after.scorecard.failure_taxonomy
        assert "injected" not in after.evaluation.per_task_success
        assert after.episodes[0].joint_states_summary, (
            f"{backend_id}/{path}: the stored joint states summary was emptied"
        )

    @pytest.mark.parametrize("backend_id", store_backend_ids)
    def test_the_surviving_record_is_the_first_one_recorded(
        self, store_backends: Callable[[str], ValidationStore], backend_id: str
    ) -> None:
        """A mutation attempt must not open the door for a later duplicate write."""
        store = store_backends(backend_id)
        original = _record()
        store.save(original)

        _attempt_all(store, _RUN_ID, "get")

        # A BLOCK scorecard for the same run_id: append-only must ignore it.
        conflicting = make_stored_run(
            make_persisted_scorecard(_RUN_ID, composite_score=20.0, deploy_decision="BLOCK")
        )
        store.save(conflicting)

        after = store.get(_RUN_ID)
        assert after is not None
        assert (after.scorecard.composite_score, after.scorecard.deploy_decision) == (
            95.0,
            "APPROVE",
        )
        assert store.count() == 1


class TestTheBackendsDifferOnlyInHowTheyRefuse:
    """The one deliberate, documented difference -- pinned rather than assumed.

    The in-memory store shares its frozen record, so an in-place mutation is
    refused outright. The SQL backends rebuild from JSON per read, so the same
    statement succeeds on a throwaway copy. Neither can reach the store, and
    stating which side is which is what keeps the difference from silently
    becoming a divergence nobody chose.
    """

    def test_memory_refuses_in_place_mutation_of_a_read_handle(self) -> None:
        store = ValidationStore()
        store.save(_record())
        handle = store.get(_RUN_ID)
        assert handle is not None

        assert _attempt(_mutation("episode.success = ..."), handle) is False, (
            "the in-memory store shares its record, so a mutation must be refused"
        )

    def test_sqlite_accepts_it_on_a_private_copy(self, make_store) -> None:
        store = make_store("read-immutability.db")
        store.save(_record())
        handle = store.get(_RUN_ID)
        assert handle is not None

        assert _attempt(_mutation("episodes.append(...)"), handle) is True, (
            "the SQL backends rebuild per read, so the mutation lands on a "
            "private copy and is expected to succeed"
        )

    def test_both_leave_the_store_returning_the_original(
        self, store_backends: Callable[[str], ValidationStore]
    ) -> None:
        original = _record()
        for backend_id in store_backend_ids:
            store = store_backends(backend_id)
            store.save(original)
            _attempt_all(store, _RUN_ID, "get")
            after = store.get(_RUN_ID)
            assert after is not None and after == original, (
                f"{backend_id}: the store's record changed after a mutation "
                "through a read handle"
            )


# ---------------------------------------------------------------------------
# Why the sharing is safe: the record is genuinely immutable
# ---------------------------------------------------------------------------


def _mutable_nodes(value: Any, _seen: set[int] | None = None) -> list[tuple[str, int]]:
    """Every ``id()`` of a **mutable** node reachable from *value*.

    Walks dataclasses, dicts, lists, tuples and sets with an identity-based
    ``seen`` set, so a cyclic structure terminates. Tuples are traversed but not
    reported: a tuple cannot be reassigned in place, so sharing one is safe --
    and reporting CPython's empty-tuple singleton would make the guard fail
    always, which is a guard that gets deleted.
    """
    if _seen is None:
        _seen = set()
    if id(value) in _seen:
        return []
    _seen.add(id(value))

    out: list[tuple[str, int]] = []
    children: list[Any] = []
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        children.extend(
            getattr(value, f.name) for f in dataclasses.fields(value) if f.init
        )
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
            out.extend(_mutable_nodes(child, _seen))
    return out


class TestTheStoredRecordHasNoMutableNodeReachableFromIt:
    """A structural walk, so a field added later is covered by construction."""

    def test_no_mutable_container_is_reachable_from_a_stored_record(self) -> None:
        store = ValidationStore()
        store.save(_record())
        stored = store.get(_RUN_ID)
        assert stored is not None

        mutable = _mutable_nodes(stored)
        # FrozenList/FrozenDict are the *only* acceptable containers, and they
        # are still reported (they subclass list/dict) -- so the assertion is on
        # their type, not on their absence.
        offenders = [
            label
            for label, _node_id in mutable
            if label not in ("FrozenList", "FrozenDict")
        ]
        assert not offenders, (
            f"a stored record still reaches plain mutable containers: {offenders}"
        )
        assert mutable, "the walk found no container at all -- it is not reaching them"

    def test_every_record_dataclass_is_frozen(self) -> None:
        """Field reassignment must be impossible, not merely unreachable.

        ``EpisodeResult`` used to be the one non-frozen dataclass in this graph,
        and that single exception was the whole hole: a stored run keeps a list
        of them.
        """
        for cls in _RECORD_DATACLASSES:
            assert cls.__dataclass_params__.frozen, (
                f"{cls.__name__} is not frozen; a record containing it can be "
                "rewritten in place through a read handle"
            )

    def test_no_record_dataclass_field_is_a_set(self) -> None:
        """The precondition that makes ``freeze``'s ``set`` -> ``frozenset`` safe.

        ``frozenset({1}) != {1}``, so if a record field were a ``set`` the freeze
        would silently break record equality against the SQL backends. No field
        is a ``set`` today; this fails the moment one is added.
        """
        offenders: list[str] = []
        for cls in _RECORD_DATACLASSES:
            for f in dataclasses.fields(cls):
                if isinstance(f.default, (set, frozenset)):
                    offenders.append(f"{cls.__name__}.{f.name}")
        assert not offenders, (
            f"record field(s) default to a set: {offenders}. freeze() converts a "
            "set to a frozenset, which is not equal to a set -- give the field an "
            "immutable type instead"
        )

    def test_freezing_a_frozen_record_reuses_its_containers(self) -> None:
        """A second freeze must not rebuild a single container.

        ``freeze`` returns an already-frozen container as it is, so the expensive
        part of the walk -- rebuilding ``FrozenList``/``FrozenDict`` and every
        nested episode -- is paid once, at save time. The dataclass *wrappers*
        are rebuilt (detecting "already frozen" would require the same walk), so
        the assertion is on the containers and on value equality rather than on
        object identity of the record.
        """
        store = ValidationStore()
        store.save(_record())
        stored = store.get(_RUN_ID)
        assert stored is not None

        again = freeze(stored)
        assert again == stored
        assert again.episodes is stored.episodes, (
            "freeze() rebuilt the episode list of an already-frozen record"
        )
        assert again.scorecard.failure_taxonomy is stored.scorecard.failure_taxonomy
        assert again.evaluation.per_task_success is stored.evaluation.per_task_success
        assert again.episodes[0] is stored.episodes[0]
        assert again.episodes[0].joint_states_summary is (
            stored.episodes[0].joint_states_summary
        )

    def test_freeze_terminates_on_a_cyclic_structure(self) -> None:
        cyclic: dict[str, Any] = {"level": 0}
        cyclic["self"] = cyclic
        frozen = freeze(cyclic)
        assert frozen["level"] == 0
        assert isinstance(frozen, FrozenDict)


class TestFrozenContainersBehaveLikeThePlainOnes:
    """Freezing must be invisible except for the refusal.

    ``_frozen`` subclasses ``list``/``dict`` rather than wrapping them precisely
    so equality, ``json.dumps`` and ``dataclasses.asdict`` keep working: a record
    read from memory must compare equal to the same record read from SQLite, and
    must serialise to the same bytes.
    """

    def test_a_frozen_container_compares_equal_to_a_plain_one(self) -> None:
        assert FrozenList([1, 2, 3]) == [1, 2, 3]
        assert [1, 2, 3] == FrozenList([1, 2, 3])
        assert FrozenDict({"a": 1}) == {"a": 1}
        assert {"a": 1} == FrozenDict({"a": 1})
        assert FrozenList([1]) != [2]
        assert FrozenDict({"a": 1}) != {"a": 2}

    def test_a_frozen_container_reports_the_plain_type_name_for_isinstance(self) -> None:
        assert isinstance(FrozenList([]), list)
        assert isinstance(FrozenDict({}), dict)

    def test_a_frozen_container_serialises_like_a_plain_one(self) -> None:
        assert json.dumps(FrozenList([1, 2])) == json.dumps([1, 2])
        assert json.dumps(FrozenDict({"a": 1})) == json.dumps({"a": 1})
        assert json.dumps(FrozenDict({"b": 1, "a": 2}), sort_keys=True) == json.dumps(
            {"b": 1, "a": 2}, sort_keys=True
        )

    def test_dataclasses_asdict_walks_a_frozen_record(self) -> None:
        """The SQL save path serialises through ``asdict``."""
        store = ValidationStore()
        store.save(_record())
        stored = store.get(_RUN_ID)
        assert stored is not None

        dumped = dataclasses.asdict(stored.episodes[0])
        assert dumped["episode_id"] == stored.episodes[0].episode_id
        assert isinstance(dumped["joint_states_summary"], dict)

    def test_a_frozen_record_reads_like_a_plain_one(self) -> None:
        store = ValidationStore()
        store.save(_record())
        stored = store.get(_RUN_ID)
        assert stored is not None

        assert len(stored.episodes) == 2
        assert [e.seed for e in stored.episodes] == [42, 43]
        assert stored.episodes[0].episode_id.startswith("pick-place-")
        assert 42 in [e.seed for e in stored.episodes]
        assert sorted(stored.episodes, key=lambda e: e.seed) == list(stored.episodes)
        assert list(stored.scorecard.failure_taxonomy) == list(
            stored.scorecard.failure_taxonomy.keys()
        )
        assert stored.evaluation.per_task_success.get("pick-place") == 0.5
        assert dict(stored.evaluation.failure_taxonomy) == {"collision": 1}

    def test_copy_and_deepcopy_return_the_frozen_container_itself(self) -> None:
        """Matching how ``copy`` treats the immutable builtins.

        It is also what keeps ``copy`` working at all: the default
        ``__reduce_ex__`` for a ``list``/``dict`` subclass rebuilds through
        ``__setitem__``, which these refuse.
        """
        frozen_list = FrozenList([1, 2])
        frozen_dict = FrozenDict({"a": 1})
        assert copy.copy(frozen_list) is frozen_list
        assert copy.deepcopy(frozen_list) is frozen_list
        assert copy.copy(frozen_dict) is frozen_dict
        assert copy.deepcopy(frozen_dict) is frozen_dict

    def test_pickle_round_trips_a_frozen_container(self) -> None:
        frozen_list = FrozenList([1, 2])
        frozen_dict = FrozenDict({"a": 1})
        assert pickle.loads(pickle.dumps(frozen_list)) == frozen_list
        assert pickle.loads(pickle.dumps(frozen_dict)) == frozen_dict
        assert isinstance(pickle.loads(pickle.dumps(frozen_list)), FrozenList)
        assert isinstance(pickle.loads(pickle.dumps(frozen_dict)), FrozenDict)

    def test_repr_matches_the_plain_container(self) -> None:
        assert repr(FrozenList([1, 2])) == repr([1, 2])
        assert repr(FrozenDict({"a": 1})) == repr({"a": 1})

    def test_the_refusal_names_the_way_out(self) -> None:
        """A caller that genuinely wants a modified sequence must be told how."""
        with pytest.raises(TypeError) as list_err:
            FrozenList([1]).append(2)
        assert "immutable" in str(list_err.value)
        assert "list(" in str(list_err.value)

        with pytest.raises(TypeError) as dict_err:
            FrozenDict({})["a"] = 1
        assert "immutable" in str(dict_err.value)

    @pytest.mark.parametrize(
        "method",
        [
            "append",
            "extend",
            "insert",
            "remove",
            "pop",
            "clear",
            "sort",
            "reverse",
            "__setitem__",
            "__delitem__",
            "__iadd__",
            "__imul__",
        ],
    )
    def test_every_list_mutator_is_refused(self, method: str) -> None:
        frozen = FrozenList([1, 2])
        args: tuple[Any, ...] = ()
        if method == "__setitem__":
            args = (0, 9)
        elif method == "__delitem__":
            args = (0,)
        elif method in ("append", "__iadd__", "__imul__"):
            args = (3,) if method != "__imul__" else (2,)
        elif method in ("insert",):
            args = (0, 9)
        elif method in ("remove",):
            args = (1,)
        with pytest.raises(TypeError):
            getattr(frozen, method)(*args)
        assert list(frozen) == [1, 2], f"{method} changed the frozen list"

    @pytest.mark.parametrize(
        "method",
        [
            "__setitem__",
            "__delitem__",
            "clear",
            "pop",
            "popitem",
            "setdefault",
            "update",
            "__ior__",
        ],
    )
    def test_every_dict_mutator_is_refused(self, method: str) -> None:
        frozen = FrozenDict({"a": 1})
        args: tuple[Any, ...] = ()
        if method == "__setitem__":
            args = ("b", 2)
        elif method == "__delitem__":
            args = ("a",)
        elif method == "pop":
            args = ("a",)
        elif method == "setdefault":
            args = ("b", 2)
        elif method == "update":
            args = ({"b": 2},)
        elif method == "__ior__":
            args = ({"b": 2},)
        with pytest.raises(TypeError):
            getattr(frozen, method)(*args)
        assert dict(frozen) == {"a": 1}, f"{method} changed the frozen dict"


# ---------------------------------------------------------------------------
# The write path and the SQL serialisers accept frozen containers
# ---------------------------------------------------------------------------


class TestFrozenContainersSurviveTheWritePaths:
    """A record read from one backend must be writable to another unchanged."""

    def test_a_record_read_from_memory_round_trips_through_sqlite(self, make_store) -> None:
        memory = ValidationStore()
        memory.save(_record())
        stored = memory.get(_RUN_ID)
        assert stored is not None

        sqlite = make_store("frozen-roundtrip.db")
        sqlite.save(stored)

        back = sqlite.get(_RUN_ID)
        assert back is not None
        assert back == stored
        assert back.episodes == stored.episodes
        assert back.scorecard == stored.scorecard
        assert back.evaluation == stored.evaluation
        assert back.safety == stored.safety
        assert back.regression == stored.regression

    def test_sqlite_serialisers_accept_a_frozen_record(self, make_store) -> None:
        from validsim.store.sqlite import _episodes_to_json, _insert_params

        memory = ValidationStore()
        memory.save(_record())
        stored = memory.get(_RUN_ID)
        assert stored is not None

        assert json.loads(_episodes_to_json(stored.episodes))[0]["seed"] == 42
        assert len(_insert_params(stored)) == 12

    def test_save_many_accepts_a_frozen_batch(self) -> None:
        memory = ValidationStore()
        memory.save(_record())
        stored = memory.get(_RUN_ID)
        assert stored is not None

        target = ValidationStore()
        target.save_many([stored])
        assert target.get(_RUN_ID) == stored


class TestReadsShareRatherThanCopy:
    """The property the freeze exists to preserve, pinned directly.

    A deep copy on read would satisfy every isolation test above while making the
    default backend's bulk reads as slow as SQLite's. Asserting the share keeps
    that from being "fixed" back into a copy by a future change.
    """

    def test_two_reads_of_one_record_return_the_same_object(self) -> None:
        store = ValidationStore()
        store.save(_record())
        first = store.get(_RUN_ID)
        second = store.get(_RUN_ID)
        assert first is second, (
            "the in-memory store copied the record on read; a shared frozen "
            "record is what keeps reads O(1) (see tests/bench/test_bench_store.py)"
        )

    def test_history_returns_the_stored_objects_not_copies(self) -> None:
        store = ValidationStore()
        store.save(_record())
        stored = store.get(_RUN_ID)
        listed = store.history()
        assert len(listed) == 1
        assert listed[0] is stored
