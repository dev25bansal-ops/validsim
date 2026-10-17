"""Immutable containers for stored evidence records.

Why this module exists
----------------------
:class:`~validsim.store.memory.StoredRun` is a *frozen* dataclass, which reads as
"immutable" but is not: ``episodes`` is a ``list`` and the scorecard/evaluation
artifacts carry mappings. The in-memory backend -- which is the **default**
backend, ``VALIDSIM_STORE`` unset -- kept one record per id and handed that
*same* object to every reader. Any caller holding a record from ``get()``,
``history()`` or ``list_for_checkpoint()`` could therefore rewrite the store's
evidence in place::

    rec = store.get(run_id)
    rec.evaluation.failure_taxonomy["injected"] = 999   # rewrites the record
    store.get(run_id).evaluation.failure_taxonomy       # {'injected': 999}

No write, no lock, no log line -- the same "silently rewritten verdict" the
append-only write contract exists to make impossible, just reached through the
read side. The persistent backends never had the hole because they rebuild from
JSON on every read, which made this a *backend divergence* as well as an
integrity hole.

Why freeze rather than copy on read
-----------------------------------
The obvious fix -- return a deep copy from every read -- is correct but costly
in exactly the place this backend exists to be fast. ``history()`` over 100 000
runs is ~39 ms as a share and ~8 500 ms as a deep copy, and that bulk read is
what ``/api/v1/dashboard/history`` and ``/api/v1/validations`` do on every
request. ``tests/bench/test_bench_store.py`` pins that ratio against SQLite
precisely so it cannot be given away by accident.

Freezing *at save time* pays the same single walk the write path already paid
(``save`` has always snapshotted its argument) and makes every later read a
share instead of a copy. The store keeps a record with **no mutable container
reachable from it**, so sharing it cannot leak: there is nothing left to mutate.

Equality and serialisation are preserved by *subclassing* rather than by
converting to a different type. ``FrozenList([1, 2]) == [1, 2]`` is ``True`` and
``FrozenDict({"a": 1}) == {"a": 1}`` is ``True``, so a frozen record still
compares equal to the caller's original and to the same record read back from
SQLite or PostgreSQL; and because they *are* a ``list``/``dict``,
``json.dumps``, ``dataclasses.asdict`` and the SQL serialisers render them as
the plain array/object they would render for an unfrozen container.

Scope
-----
This module is applied by the in-memory backend, which is the only backend that
shares its stored objects between reads. The SQL backends rebuild from persisted
JSON on every read, so a mutation through one of their read handles is already a
no-op on a private copy -- see the ``Read-path immutability`` note on
:class:`~validsim.store.memory.ValidationStore` for the exact wording of the
guarantee and of this one deliberate, benign difference.
"""

from __future__ import annotations

import dataclasses
from typing import Any

__all__ = ["FrozenDict", "FrozenList", "freeze"]

#: Recursion cap for :func:`freeze`. Real records are three to four levels deep
#: (``StoredRun`` -> episode -> summary dict), so this is a safety valve against
#: a caller-supplied structure that cycles, not a working limit.
MAX_FREEZE_DEPTH = 12


class FrozenList(list):
    """A ``list`` that refuses every in-place mutation.

    Subclassing rather than wrapping is what keeps ``==``, ``json.dumps`` and
    ``dataclasses.asdict`` behaving exactly as they do for a plain ``list``; see
    the module docstring. Every mutating method raises :class:`TypeError` rather
    than silently doing nothing, so a caller that genuinely wants a modified
    sequence is told to build one (``list(record.episodes)``) instead of having
    its edit dropped.
    """

    __slots__ = ()

    _MESSAGE = (
        "a record read out of the validation store is immutable: its "
        "containers cannot be mutated in place, because the store shares them "
        "with every other reader. Build a copy first -- e.g. "
        "list(record.episodes) or dict(record.scorecard.failure_taxonomy)."
    )

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        """Raise the store's immutability error, ignoring the arguments."""
        raise TypeError(self._MESSAGE)

    append = extend = insert = remove = pop = clear = sort = reverse = _refuse
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _refuse

    def __copy__(self) -> FrozenList:
        """Return ``self``: an immutable container needs no copy.

        Matches how :mod:`copy` treats the immutable builtins. It also keeps
        ``copy.copy``/``copy.deepcopy`` working at all: the default
        ``__reduce_ex__`` for a ``list`` subclass rebuilds through
        ``__setitem__``, which this class refuses.
        """
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenList:
        """Return ``self``; every element is already immutable."""
        memo[id(self)] = self
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle via ``FrozenList(list(self))``, bypassing ``__setitem__``."""
        return (type(self), (list(self),))


class FrozenDict(dict):
    """A ``dict`` that refuses every in-place mutation.

    The counterpart of :class:`FrozenList` for the four mappings a record
    carries (``scorecard.failure_taxonomy``, ``evaluation.per_task_success``,
    ``evaluation.failure_taxonomy`` and each episode's
    ``joint_states_summary``).
    """

    __slots__ = ()

    _MESSAGE = FrozenList._MESSAGE

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        """Raise the store's immutability error, ignoring the arguments."""
        raise TypeError(self._MESSAGE)

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _refuse
    __ior__ = _refuse

    def __copy__(self) -> FrozenDict:
        """Return ``self``; see :meth:`FrozenList.__copy__`."""
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict:
        """Return ``self``; every value is already immutable."""
        memo[id(self)] = self
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle via ``FrozenDict(dict(self))``, bypassing ``__setitem__``."""
        return (type(self), (dict(self),))


def freeze(value: Any, *, _depth: int = 0) -> Any:
    """Return *value* with every mutable container reachable from it frozen.

    The walk is structural rather than a hand-enumerated list of fields, so a
    mutable field added to ``StoredRun`` or to any nested artifact is covered by
    construction instead of by someone remembering to extend this function.

    * ``dict`` -> :class:`FrozenDict`
    * ``list`` -> :class:`FrozenList`
    * ``tuple`` -> ``tuple`` of frozen elements (tuples cannot be reassigned in
      place, but their elements can, so the elements are still walked)
    * ``set`` -> ``frozenset`` (no field of any record dataclass is a ``set``
      today -- ``tests/test_store_read_immutability_agent.py`` asserts that -- so
      the ``frozenset``/``set`` equality mismatch is unreachable)
    * any nested dataclass -> a rebuilt instance with each ``init``-able field
      frozen, so a frozen-but-not-really dataclass like ``StoredRun`` becomes
      genuinely immutable

    Immutable leaves (``str``, numbers, ``bool``, ``None``, ``bytes``) are
    returned as they are, and an already-frozen container is returned unchanged
    so re-freezing a stored record is free.
    """
    if _depth >= MAX_FREEZE_DEPTH:
        return value
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict(
            {key: freeze(item, _depth=_depth + 1) for key, item in value.items()}
        )
    if isinstance(value, list):
        return FrozenList(freeze(item, _depth=_depth + 1) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item, _depth=_depth + 1) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _freeze_dataclass(value, _depth=_depth + 1)
    return value


def _freeze_dataclass(obj: Any, *, _depth: int = 0) -> Any:
    """Structurally rebuild *obj*, freezing each of its ``init``-able fields.

    ``dataclasses.replace`` is a **shallow** copy: it rebuilds the wrapper but
    shares every mutable field, which is exactly how a caller-reachable mutable
    escaped the original hand-enumerated snapshot. Rebuilding each field through
    :func:`freeze` closes that gap for this record, and skipping ``init=False``
    fields leaves the ones a caller cannot set alone.
    """
    changes = {
        field.name: freeze(getattr(obj, field.name), _depth=_depth + 1)
        for field in dataclasses.fields(obj)
        if field.init
    }
    return dataclasses.replace(obj, **changes)
