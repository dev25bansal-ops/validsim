"""Redis-backend coverage for the retry path.

The in-memory queue finds claimable work by scanning every record for
`queued`, so a requeue only has to flip the status. The Redis backend keeps
job ids on a ready list that `claim_next` pops, so a requeue that does not
also re-list the id leaves the job invisible forever: never claimable, never
reaped (the reaper only looks at `running` jobs) and never dead-lettered,
while the API still reports it as pending work.

Both halves of the fix are exercised for real. The requeue script returns the
id to the ready list, and the cooldown check in the claim script keeps a job
that is still backing off from being claimed early -- without it, marking a
job `queued` is enough to hot-spin a failing backend and burn the whole
attempt budget in milliseconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from validsim.jobs.models import JobRecord, JobSpec, JobStatus
from validsim.jobs.queue import RedisJobQueue

pytest.importorskip("lupa", reason="Redis Lua scripts need lupa to execute")
fakeredis = pytest.importorskip("fakeredis", reason="needs fakeredis")


def _iso_in(seconds: float) -> str:
    """ISO-8601 instant `seconds` from real UTC now.

    `RedisJobQueue` reads the wall clock directly rather than through the
    in-memory `advance_clock` seam, so a test that wants the backoff to have
    elapsed has to date the instant in the past rather than move a fake clock.
    """
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds")


@pytest.fixture()
def queue() -> RedisJobQueue:
    """A Redis queue over a private in-process fake server.

    A fresh `FakeServer` per test gives isolation with no flushall step.
    """
    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return RedisJobQueue(_client_factory=lambda: client, lease_seconds=300)


def _spec(run_id: str) -> JobSpec:
    return JobSpec(run_id=run_id, checkpoint_id="ckpt-1", task_id="pick-place")


def _ready_ids(queue: RedisJobQueue) -> list[str]:
    """Ids currently on the ready list, normalised to `str`."""
    raw: list[Any] = list(queue._ensure_client().lrange(queue._ready_key(), 0, -1))
    return [v.decode() if isinstance(v, bytes) else str(v) for v in raw]


def test_requeued_job_returns_to_the_ready_list(queue: RedisJobQueue) -> None:
    """A requeued job must be claimable again, not merely marked queued.

    Without the RPUSH in the requeue script the record reads back as `queued`
    while the ready list is empty, so the job is stranded even though every
    status field looks correct.
    """
    job_id = queue.enqueue(_spec("vrun-requeue-01")).job_id
    claimed = queue.claim_next()
    assert claimed is not None and claimed.job_id == job_id

    requeued = queue.requeue_for_retry(
        job_id, claimed.lease_epoch, "backend unreachable", _iso_in(-5.0)
    )
    assert requeued is not None
    assert requeued.status is JobStatus.QUEUED

    # The record and the dispatch index must agree, or the job cannot run.
    assert job_id in _ready_ids(queue)

    reclaimed = queue.claim_next()
    assert reclaimed is not None, "requeued job was never claimable again"
    assert reclaimed.job_id == job_id
    assert reclaimed.lease_epoch > claimed.lease_epoch
    assert reclaimed.attempt == claimed.attempt + 1


def test_requeued_job_waits_for_its_backoff(queue: RedisJobQueue) -> None:
    """Honouring the backoff is what stops a failing backend hot-spinning."""
    job_id = queue.enqueue(_spec("vrun-requeue-02")).job_id
    claimed = queue.claim_next()
    assert claimed is not None

    assert queue.requeue_for_retry(job_id, claimed.lease_epoch, "boom", _iso_in(300.0)) is not None

    assert queue.claim_next() is None, "claimed a job that is still cooling down"

    # Deferred, not discarded: the id stays queued for a later poll.
    assert job_id in _ready_ids(queue)
    remaining = queue.retry_delay_for(job_id)
    assert remaining is not None and remaining > 0


def test_stale_epoch_cannot_requeue(queue: RedisJobQueue) -> None:
    """Fencing must survive the move into a Lua script.

    A worker whose lease was already reclaimed must not push the job back
    onto the ready list, which would start a third concurrent run of it.
    """
    job_id = queue.enqueue(_spec("vrun-requeue-03")).job_id
    first = queue.claim_next()
    assert first is not None

    assert queue.requeue_for_retry(job_id, first.lease_epoch - 1, "stale", _iso_in(-5.0)) is None

    record = queue.get(job_id)
    assert isinstance(record, JobRecord)
    assert record.status is JobStatus.RUNNING, "stale requeue must not settle it"
    assert job_id not in _ready_ids(queue)


def test_every_redis_script_compiles() -> None:
    """Guard the Lua itself, not just the Python that wraps it.

    The scripts live inside Python string literals, so Python happily parses a
    truncated one: `ast.parse` and `ruff` both pass while Redis rejects the
    script at EVAL time and the whole backend stops working. Only a real Lua
    compiler catches that.
    """
    import lupa

    from validsim.jobs import queue as queue_mod

    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    compile_only = runtime.eval(
        "function(s) local f, e = load(s) if f then return true end return false, e end"
    )

    names = sorted(n for n in dir(queue_mod) if n.startswith("_REDIS_") and n.endswith("_SCRIPT"))
    assert names, "no Redis scripts discovered -- the guard is not wired up"
    for name in names:
        result = compile_only(getattr(queue_mod, name))
        if isinstance(result, tuple):
            pytest.fail(f"{name} does not compile: {result[1]}")


def test_unfenced_update_status_queued_requeues_the_job(queue: RedisJobQueue) -> None:
    """A queued transition must re-list the id, fenced or not.

    The unfenced branch used to SET the record and stop. The record then read
    `queued` while the ready list stayed empty, so the job was claimable by no
    one -- the same stranding the worker retry path had.
    """
    job_id = queue.enqueue(_spec("vrun-us-01")).job_id
    claimed = queue.claim_next()
    assert claimed is not None

    updated = queue.update_status(job_id, JobStatus.QUEUED, retry_after=_iso_in(-5.0))
    assert updated is not None
    assert updated.status is JobStatus.QUEUED
    assert job_id in _ready_ids(queue)

    reclaimed = queue.claim_next()
    assert reclaimed is not None and reclaimed.job_id == job_id


def test_fenced_update_status_queued_keeps_backoff_and_no_false_finish(
    queue: RedisJobQueue,
) -> None:
    """The fenced branch used to route QUEUED into the terminal-write script.

    That script stamps `finished_at`, clears `retry_after` and copies the
    result pointer for whatever status it is handed, so a job put back to
    queued came back looking finished, lost its backoff, and was still never
    re-listed. All three are asserted here.
    """
    job_id = queue.enqueue(_spec("vrun-fs-01")).job_id
    claimed = queue.claim_next()
    assert claimed is not None
    ready_at = _iso_in(300.0)

    updated = queue.update_status(
        job_id,
        JobStatus.QUEUED,
        lease_epoch=claimed.lease_epoch,
        retry_after=ready_at,
    )
    assert updated is not None
    assert updated.status is JobStatus.QUEUED
    assert updated.finished_at is None, "a pending job must not look finished"
    assert updated.retry_after == ready_at, "the backoff must survive"
    assert updated.lease_expires_at is None
    assert job_id in _ready_ids(queue)

    # Backoff still honoured, so it is deferred rather than claimable now.
    assert queue.claim_next() is None
    assert job_id in _ready_ids(queue)


def test_fenced_update_status_queued_rejects_a_stale_epoch(
    queue: RedisJobQueue,
) -> None:
    """Fencing must hold on the shared queued path too."""
    job_id = queue.enqueue(_spec("vrun-fs-02")).job_id
    claimed = queue.claim_next()
    assert claimed is not None

    assert (
        queue.update_status(
            job_id,
            JobStatus.QUEUED,
            lease_epoch=claimed.lease_epoch - 1,
            retry_after=_iso_in(-5.0),
        )
        is None
    )
    record = queue.get(job_id)
    assert isinstance(record, JobRecord)
    assert record.status is JobStatus.RUNNING
    assert job_id not in _ready_ids(queue)


def test_unfenced_update_status_queued_raises_for_unknown_job(
    queue: RedisJobQueue,
) -> None:
    """The unfenced path reports an unknown id, as it always has."""
    with pytest.raises(KeyError):
        queue.update_status("vrun-does-not-exist", JobStatus.QUEUED)


def test_terminal_update_status_does_not_requeue(queue: RedisJobQueue) -> None:
    """Only a transition to queued may put a job back on the ready list."""
    job_id = queue.enqueue(_spec("vrun-term-01")).job_id
    claimed = queue.claim_next()
    assert claimed is not None

    updated = queue.update_status(
        job_id, JobStatus.FAILED, error="boom", lease_epoch=claimed.lease_epoch
    )
    assert updated is not None
    assert updated.status is JobStatus.FAILED
    assert job_id not in _ready_ids(queue), "a failed job must not be re-queued"
    assert queue.claim_next() is None


def test_a_cooling_head_does_not_hide_the_job_behind_it(queue: RedisJobQueue) -> None:
    """``claim_next`` must skip a cooling head, not stop at it.

    Popping exactly one id and re-pushing it made the Redis queue strictly weaker
    than the in-memory one, which scans every record: with a ready list of
    ``[cooling, cooling, fresh]`` the old script answered ``None`` on the first two
    polls while a claimable job sat one position back, so a poller that stops on
    ``None`` simply stalled. The in-memory backend returns the fresh job on the
    first call, and so must this one.
    """
    for run_id in ("vrun-cool-a", "vrun-cool-b", "vrun-fresh"):
        queue.enqueue(_spec(run_id))
    for run_id in ("vrun-cool-a", "vrun-cool-b"):
        queue.update_status(run_id, JobStatus.QUEUED, retry_after=_iso_in(3600.0))

    claimed = queue.claim_next()

    assert claimed is not None, "a claimable job was hidden behind cooling heads"
    assert claimed.spec.run_id == "vrun-fresh"


def test_the_scan_does_not_spin_on_an_all_cooling_list(queue: RedisJobQueue) -> None:
    """The scan is bounded, so a list with nothing ready still returns promptly.

    Every head requeues itself, so an unbounded loop would never terminate.
    """
    for run_id in ("vrun-x1", "vrun-x2", "vrun-x3"):
        queue.enqueue(_spec(run_id))
    for run_id in ("vrun-x1", "vrun-x2", "vrun-x3"):
        queue.update_status(run_id, JobStatus.QUEUED, retry_after=_iso_in(3600.0))

    before = _ready_ids(queue)
    assert queue.claim_next() is None
    after = _ready_ids(queue)

    # Each examined id went back on the tail, so the set is preserved and the
    # list has not grown by a duplicate of any single id.
    assert sorted(after) == sorted(before)

