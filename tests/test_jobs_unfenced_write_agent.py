"""Cross-process atomicity for *unfenced* status writes on the Redis backend.

The fenced path and the queued path each do their read, their decision and
their write inside one Lua script, so a second process cannot slip between the
steps. An unfenced, non-queued transition used to be the exception: it read the
record with ``GET`` and wrote it back with ``SET`` -- two round trips with a
window in between.

That window was not merely "last writer wins". The whole record is rewritten
from the stale read, so every field the winner had just advanced went backwards
with it. A job reclaimed while the read was in flight came out of the write at
the *dead* owner's epoch, attempt count and reclaim count, which did two things
at once:

* the new owner's fenced completion was rejected by its own token, so the work
  it did was never recorded;
* the retry budget was refunded, so a job could be attempted more times than
  ``max_attempts`` allows.

The tests open that window deterministically by running the competing
reclaim+claim inside the client's ``get``. That is the same state change the
race produces; scheduling it rather than leaving it to thread luck is what
keeps the test reliable.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from validsim.jobs.models import JobRecord, JobSpec, JobStatus
from validsim.jobs.queue import (
    QueueContentionError,
    RedisJobQueue,
    _UNFENCED_WRITE_ATTEMPTS,
    _apply_status,
)

pytest.importorskip("lupa", reason="Redis Lua scripts need lupa to execute")
fakeredis = pytest.importorskip("fakeredis", reason="needs fakeredis")


def _spec(run_id: str = "vrun-unfenced-1") -> JobSpec:
    return JobSpec(run_id=run_id, checkpoint_id="ckpt-1", task_id="pick-place")


def _raw(server: Any) -> Any:
    """A bare client on the shared fake server -- another process."""
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


class _RacingClient:
    """Runs ``on_read`` against the inner client immediately after each read.

    ``on_read`` is deliberately called with the *inner* client so the competing
    write is not itself traced, and it self-gates if it should only fire once.
    """

    def __init__(self, inner: Any, on_read: Any) -> None:
        self._inner = inner
        self._on_read = on_read
        self.reads = 0
        self.writes = 0

    def get(self, key: str) -> Any:
        value = self._inner.get(key)
        self.reads += 1
        self._on_read(self._inner, key)
        return value

    def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self.writes += 1
        return self._inner.eval(script, numkeys, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _queue(server: Any, inner: Any) -> RedisJobQueue:
    return RedisJobQueue(_client_factory=lambda: inner, lease_seconds=300)


def _stored(inner: Any, queue: RedisJobQueue, job_id: str) -> JobRecord:
    return JobRecord.from_dict(json.loads(inner.get(queue._job_key(job_id))))


def _write(inner: Any, queue: RedisJobQueue, record: JobRecord) -> None:
    inner.set(queue._job_key(record.job_id), json.dumps(record.to_dict()))


def _expire_lease(inner: Any, queue: RedisJobQueue, job_id: str) -> None:
    """Backdate the live lease so the reaper can take the job.

    The Redis backend reads the wall clock rather than an injectable seam, so
    the lease is moved in storage instead of waiting for it.
    """
    record = _stored(inner, queue, job_id)
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(
        timespec="seconds"
    )
    _write(inner, queue, dataclasses.replace(record, lease_expires_at=past))


@pytest.fixture()
def server() -> Any:
    return fakeredis.FakeServer()


def test_unfenced_write_does_not_rewind_a_reclaimed_lease(server: Any) -> None:
    """A claim made after the read must survive the write that follows it."""
    owner = _queue(server, _raw(server))
    job_id = owner.enqueue(_spec()).job_id
    first = owner.claim_next()
    assert first is not None and first.lease_epoch == 1

    once = {"fired": False}

    def steal(inner: Any, _key: str) -> None:
        if once["fired"]:
            return
        once["fired"] = True
        _expire_lease(inner, owner, job_id)
        assert owner.reap_expired() == [job_id]
        assert owner.claim_next() is not None

    stale = _queue(server, _RacingClient(_raw(server), steal))
    updated = stale.update_status(job_id, JobStatus.FAILED, error="boom")

    assert updated is not None and updated.status is JobStatus.FAILED
    after = _stored(_raw(server), owner, job_id)
    assert after.lease_epoch == 2, "the unfenced write rewrote the winner's epoch"
    assert after.attempt == 2, "the retry budget was refunded by a stale write"
    assert after.reclaim_count == 1, "the reclaim count was rewound"


def test_unfenced_write_applies_the_transition_to_the_fresh_record(server: Any) -> None:
    """Losing the race retries, so the caller's own transition still lands."""
    owner = _queue(server, _raw(server))
    job_id = owner.enqueue(_spec()).job_id
    claimed = owner.claim_next()
    assert claimed is not None

    once = {"fired": False}

    def bump_once(inner: Any, key: str) -> None:
        if once["fired"]:
            return
        once["fired"] = True
        record = _stored(inner, owner, job_id)
        _write(inner, owner, dataclasses.replace(record, attempt=record.attempt + 1))

    racing = _queue(server, _RacingClient(_raw(server), bump_once))
    updated = racing.update_status(job_id, JobStatus.FAILED, error="boom")

    assert updated is not None
    assert updated.attempt == claimed.attempt + 1, "the write used the stale read"
    stored = _stored(_raw(server), owner, job_id)
    expected = _apply_status(stored, JobStatus.FAILED, "boom", None)
    assert stored.status is expected.status
    assert stored.error == expected.error
    assert stored.lease_epoch == claimed.lease_epoch, "the claim was not preserved"


def test_unfenced_write_raises_when_the_job_disappears(server: Any) -> None:
    """A record deleted between the read and the write is still a KeyError."""
    owner = _queue(server, _raw(server))
    job_id = owner.enqueue(_spec()).job_id
    inner = _raw(server)

    def delete_after_read(client: Any, key: str) -> None:
        client.delete(key)

    racing = _queue(server, _RacingClient(inner, delete_after_read))
    with pytest.raises(KeyError):
        racing.update_status(job_id, JobStatus.DONE, result="run-1")
    assert inner.get(owner._job_key(job_id)) is None


def test_unfenced_write_gives_up_loudly_when_outraced_every_time(server: Any) -> None:
    """Sustained contention is reported instead of silently overwriting."""
    owner = _queue(server, _raw(server))
    job_id = owner.enqueue(_spec()).job_id
    inner = _raw(server)

    def change_every_time(client: Any, key: str) -> None:
        record = _stored(client, owner, job_id)
        _write(client, owner, dataclasses.replace(record, attempt=record.attempt + 1))

    wrapper = _RacingClient(inner, change_every_time)
    contended = _queue(server, wrapper)
    with pytest.raises(QueueContentionError):
        contended.update_status(job_id, JobStatus.FAILED, error="boom")

    assert wrapper.writes == _UNFENCED_WRITE_ATTEMPTS
    # The stored record kept changing (that is what contended), but none of the
    # changes were the loser's: its transition is nowhere in storage.
    final = _stored(inner, owner, job_id)
    assert final.status is not JobStatus.FAILED, "the losing writer committed anyway"
    assert final.error is None, "the losing writer's error was recorded"


def test_dead_transition_reports_once_after_a_retry(
    server: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The dead-letter signal still fires, and still fires exactly once."""
    owner = _queue(server, _raw(server))
    job_id = owner.enqueue(_spec()).job_id
    once = {"fired": False}

    def bump_once(client: Any, key: str) -> None:
        if once["fired"]:
            return
        once["fired"] = True
        record = _stored(client, owner, job_id)
        _write(client, owner, dataclasses.replace(record, attempt=record.attempt + 1))

    racing = _queue(server, _RacingClient(_raw(server), bump_once))
    with caplog.at_level("WARNING"):
        updated = racing.update_status(job_id, JobStatus.DEAD, error="budget gone")

    assert updated is not None and updated.status is JobStatus.DEAD
    announced = [r for r in caplog.records if "dead-lettered" in r.getMessage()]
    assert len(announced) == 1, caplog.messages
