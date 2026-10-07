"""The Redis backend must PUBLISH its dead-letter signal, not merely record it.

The gap
-------
Every dead-letter assertion in the suite captures Python-side ``logging``
records via a handler on the ``validsim.jobs`` logger. That is the **in-memory**
backend's channel only. Nothing in ``tests/**`` subscribed to the Redis
dead-letter channel, so a Redis deployment's entire observability story was
unguarded: deleting any one of the ``PUBLISH`` lines, or renaming the channel,
leaves every current test green while silently removing the only signal that
backend has.

This is the same class of defect jobs-queue fixed on the in-memory side -- the
terminal transition happens correctly, the *signal* is absent -- and it is most
likely to recur on the Redis path, because there the transition happens inside
Lua, where no Python-side assertion can see it. ``test_jobs_claim_deadletter_agent.py``
covers the in-memory ``claim_next`` settling path; this file covers the three
Redis ``PUBLISH 'validsim:jobs:dead-letter'`` sites.

THE TRAP -- read this before writing or trusting any test on this file
--------------------------------------------------------------------
The Redis claim script pops **heads** of ``validsim:jobs:ready``
(``local job_id = redis.call('LPOP', KEYS[1])``). It does **not** scan the whole
queue the way the in-memory backend does, so a spent job that is not at the head
is *never examined* and no dead-letter is ever published.

I reproduced this: enqueue a spent job and a fresh one, spend the first and
requeue it to the tail, then claim. The claim returns the fresh job and
**zero** messages are published -- because the spent job behind the head was
never looked at.

So a test that merely asserts "a dead-letter message arrives" can pass for the
wrong reason, or worse, a test written against a job that happens to be behind
another entry will assert nothing while reporting green. Every test here
therefore makes the spent job the **only** entry on the ready list, and
``test_a_spent_job_behind_the_head_is_never_examined`` pins the trap itself so
the next person cannot rediscover it by accident.

Every expected value below was measured by executing the real Lua through
``fakeredis`` + ``lupa``, not inferred.

Reproduce::

    python -m pytest tests/test_jobs_redis_deadletter_agent.py -q
"""

from __future__ import annotations

import pytest

from validsim.jobs.models import JobSpec, JobStatus

pytest.importorskip("lupa", reason="Redis Lua scripts need lupa to execute")
fakeredis = pytest.importorskip("fakeredis", reason="needs fakeredis")

from validsim.jobs.queue import RedisJobQueue  # noqa: E402

#: The channel every dead-letter must be published on.
DEAD_CHANNEL = "validsim:jobs:dead-letter"

#: A retry instant in the past, so no backoff delays the next claim. The Redis
#: queue reads the wall clock directly rather than through the in-memory
#: ``advance_clock`` seam, so the instant has to be dated rather than faked.
_PAST = "2020-01-01T00:00:00+00:00"


@pytest.fixture()
def redis_queue() -> tuple[RedisJobQueue, object]:
    """A Redis queue over a private fake server, plus a live subscriber.

    A fresh ``FakeServer`` per test gives isolation with no flushall step. The
    subscriber is returned un-consumed so each test can read messages itself.
    """
    from validsim.jobs.queue import RedisJobQueue as _RQ

    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    queue = _RQ(_client_factory=lambda: client, lease_seconds=300, max_attempts=1)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(DEAD_CHANNEL)
    return queue, pubsub


def _drain(pubsub: object, *, attempts: int = 6, timeout: float = 0.3) -> list[str]:
    """Read up to *attempts* messages, returning their payloads as strings."""
    out: list[str] = []
    for _ in range(attempts):
        message = pubsub.get_message(timeout=timeout)  # type: ignore[attr-defined]
        if message is None:
            continue
        channel = message.get("channel")
        if channel is None or str(channel) != DEAD_CHANNEL:
            continue
        data = message.get("data")
        out.append(data if isinstance(data, str) else str(data))
    return out


def _spec(run_id: str) -> JobSpec:
    return JobSpec(run_id=run_id, checkpoint_id="ckpt-1", task_id="pick-place")


def _spend_budget(queue: RedisJobQueue, run_id: str) -> str:
    """Enqueue, claim once, then requeue so the job sits ``QUEUED`` with the budget spent.

    ``max_attempts=1``, so the single claim consumes the whole budget. The requeue
    puts the id back on the ready list (via RPUSH, at the tail) while the claim
    script has already incremented ``attempt``. That is the spent-but-queued state
    the claim path is asked to settle, reached entirely through public API --
    no private state is poked.

    Returns the job id. **The job is the only entry on the ready list**, which is
    what makes it the head; see the module docstring for why that matters.
    """
    job_id = queue.enqueue(_spec(run_id)).job_id
    first = queue.claim_next()
    assert first is not None, "the job must be claimable once before its budget is spent"
    assert first.job_id == job_id
    requeued = queue.requeue_for_retry(job_id, first.lease_epoch, "boom", _PAST)
    assert requeued is not None
    assert requeued.status is JobStatus.QUEUED
    assert requeued.attempt == 1, "the attempt counter must survive the requeue"
    return job_id


def _ready_ids(queue: RedisJobQueue) -> list[str]:
    raw = list(queue._ensure_client().lrange(queue._ready_key(), 0, -1))
    return [v.decode() if isinstance(v, bytes) else str(v) for v in raw]


class TestClaimPathPublishesTheDeadLetter:
    def test_a_spent_job_is_published_on_the_redis_channel(
        self, redis_queue: tuple[RedisJobQueue, object]
    ) -> None:
        """The headline: the Redis dead-letter channel carries the job id.

        This is the signal a Redis deployment has and the in-memory logging
        handler cannot see, because the transition happens inside Lua. Measured
        payload: ``vrun-rd-01 attempt budget exhausted before claim``.
        """
        queue, pubsub = redis_queue
        job_id = _spend_budget(queue, "vrun-rd-01")

        # The spent job must be the only ready entry, hence the head the script pops.
        assert _ready_ids(queue) == [job_id], (
            "the spent job must be the only entry on the ready list; otherwise "
            "the claim script never examines it and this test proves nothing"
        )

        claimed = queue.claim_next()

        messages = _drain(pubsub)
        assert claimed is None, (
            "a spent job must not be handed to a worker, but got "
            f"{claimed.job_id if claimed else claimed!r}"
        )
        assert messages, (
            "the Redis claim path settled a spent job but published NOTHING to "
            f"{DEAD_CHANNEL!r}; the job reached dead in silence"
        )
        assert job_id in messages[0], (
            f"the published payload does not name the job: {messages[0]!r}"
        )
        assert "attempt budget exhausted" in messages[0]

        # And the transition itself still happened -- signal is not the transition.
        settled = queue.get(job_id)
        assert settled is not None
        assert settled.status is JobStatus.DEAD
        assert settled.finished_at is not None

    def test_a_spent_job_behind_the_head_is_never_examined(
        self, redis_queue: tuple[RedisJobQueue, object]
    ) -> None:
        """THE TRAP, pinned so nobody loses an hour to it.

        The Redis claim script pops **heads** of the ready list and does not scan
        the whole queue the way the in-memory backend does. A spent job sitting
        behind a fresh one is therefore never examined, so no dead-letter is
        published and the claim simply returns the fresh job.

        Measured: ready list ``[vrun-fresh, vrun-spent]`` -> claim returns the
        fresh job, **zero** dead-letter messages.

        This is a real limitation of the backend, not a defect, and the safe
        behaviour: the script bounds its own scan rather than walking an unbounded
        list. But it is exactly the condition under which a dead-letter test
        silently asserts nothing. Asserted here so the caveat travels with the
        suite, and so any future change to the scan shows up as a diff on this
        test rather than as a mysteriously vanishing signal.
        """
        queue, pubsub = redis_queue

        spent = queue.enqueue(_spec("vrun-trap-spent")).job_id
        fresh = queue.enqueue(_spec("vrun-trap-fresh")).job_id

        first = queue.claim_next()
        assert first is not None and first.job_id == spent
        requeued = queue.requeue_for_retry(spent, first.lease_epoch, "boom", _PAST)
        assert requeued is not None
        assert requeued.attempt == 1

        # The requeue pushed the spent job to the TAIL, behind the fresh one.
        assert _ready_ids(queue) == [fresh, spent], (
            "this test's whole point is that the spent job is NOT at the head; "
            f"ready list is {_ready_ids(queue)}"
        )

        claimed = queue.claim_next()

        assert claimed is not None and claimed.job_id == fresh, (
            "expected the head (fresh) to be claimed; if the scan now reaches "
            "behind the head this test fails and the trap documentation is stale"
        )
        assert _drain(pubsub) == [], (
            "the spent job was behind the head and must NOT have been examined; "
            "if a dead-letter was published the scan reached it and every test "
            "in this file must re-check the head-of-queue assumption"
        )
        assert queue.get(spent).status is JobStatus.QUEUED, (
            "the spent job is still queued and still unpublished -- that is the "
            "state this backend leaves it in, not a settled dead job"
        )


class TestReaperPublishesTheDeadLetter:
    """``_REDIS_REAP_EXPIRED_SCRIPT`` publishes on reclaim-budget exhaustion.

    A worker that dies mid-job never records anything itself, so this publish is
    the only evidence the failure mode happened. Measured payload:
    ``vrun-reap-02 reclaim-budget-exhausted``.

    Note this path needs the job **re-claimed** between reaps: ``reclaim_count``
    only advances when a claim takes the job and the lease then expires again.
    Reaping a queued job repeatedly does nothing, which is why the loop below
    alternates claim and reap rather than calling ``reap_expired`` twice.
    """

    def test_reclaim_exhaustion_is_published(
        self, redis_queue: tuple[RedisJobQueue, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import validsim.jobs.queue as queue_mod

        clock = {"now": "2026-09-25T00:00:00+00:00"}
        # Module-object form: unambiguous, and immune to whether the reader
        # passes a dotted string or a module. ``_utc_now`` is the same seam the
        # rest of the queue reads, so this is the documented way to move time.
        monkeypatch.setattr(queue_mod, "_utc_now", lambda: clock["now"])
        queue, pubsub = redis_queue
        queue._max_reclaims = 1
        queue._max_attempts = 9

        job_id = queue.enqueue(_spec("vrun-reap-02")).job_id

        settled_at = None
        for cycle in range(1, 5):
            claimed = queue.claim_next()
            assert claimed is not None, f"cycle {cycle}: the job must be claimable"
            clock["now"] = f"2026-09-25T00:{cycle * 10:02d}:00+00:00"
            queue.reap_expired()
            record = queue.get(job_id)
            if record.status is JobStatus.DEAD:
                settled_at = cycle
                break

        assert settled_at is not None, "the reaper never dead-lettered the job"

        messages = _drain(pubsub)
        assert messages, (
            "the reaper settled a reclaim-exhausted job but published NOTHING to "
            f"{DEAD_CHANNEL!r}"
        )
        assert job_id in messages[0]
        assert "reclaim-budget-exhausted" in messages[0]
        assert queue.get(job_id).status is JobStatus.DEAD


class TestFinishScriptPublishesTheDeadLetter:
    """``_REDIS_FINISH_SCRIPT`` publishes when a caller settles a job as dead.

    Measured payload: ``vrun-fin-01 manually killed`` -- the id and whatever
    reason the caller passed as ``ARGV[4]``.
    """

    def test_settling_a_job_as_dead_is_published(
        self, redis_queue: tuple[RedisJobQueue, object]
    ) -> None:
        queue, pubsub = redis_queue

        job_id = queue.enqueue(_spec("vrun-fin-01")).job_id
        claimed = queue.claim_next()
        assert claimed is not None

        updated = queue.update_status(
            job_id, JobStatus.DEAD, error="manually killed", lease_epoch=claimed.lease_epoch
        )

        messages = _drain(pubsub)
        assert updated is not None and updated.status is JobStatus.DEAD
        assert messages, (
            "a job settled as dead published nothing; the finish script's "
            f"dead-letter PUBLISH on {DEAD_CHANNEL!r} is not firing"
        )
        assert job_id in messages[0]
        assert "manually killed" in messages[0]

    def test_a_non_dead_transition_publishes_nothing(
        self, redis_queue: tuple[RedisJobQueue, object]
    ) -> None:
        """NEGATIVE CONTROL: the publish is gated on ``ARGV[2] == 'dead'``.

        Without this, a script that published on every terminal write would
        satisfy the test above while flooding the channel with ordinary
        completions -- and an operator paging on that channel would learn to
        ignore it.

        ``DONE`` is used rather than ``FAILED`` because both are non-dead, but
        ``DONE`` additionally carries a result pointer, which makes it the
        strictest of the two: the record is written through the same script with
        a populated ``result`` and ``dry`` still has to be silent.
        """
        queue, pubsub = redis_queue

        job_id = queue.enqueue(_spec("vrun-fin-02")).job_id
        claimed = queue.claim_next()
        assert claimed is not None

        updated = queue.update_status(
            job_id,
            JobStatus.DONE,
            result="vrun-result-01",
            lease_epoch=claimed.lease_epoch,
        )

        assert updated is not None and updated.status is JobStatus.DONE
        assert updated.result == "vrun-result-01"
        assert _drain(pubsub) == [], "a successful job must not publish a dead-letter"


class TestTheChannelNameIsPinned:
    """The channel string is the contract; a rename is a silent breaking change.

    Every existing dead-letter assertion in the suite reads the in-memory
    ``logging`` channel, so renaming this Redis channel would break every
    subscriber in production while leaving the whole suite green. Asserting the
    literal is the cheapest possible guard against that.
    """

    def test_all_three_redis_scripts_publish_on_the_same_channel(self) -> None:
        from validsim.jobs import queue as queue_mod

        scripts = {
            "claim": queue_mod._REDIS_CLAIM_NEXT_SCRIPT,
            "reap": queue_mod._REDIS_REAP_EXPIRED_SCRIPT,
            "finish": queue_mod._REDIS_FINISH_SCRIPT,
        }
        for name, script in scripts.items():
            assert DEAD_CHANNEL in script, (
                f"the {name} script no longer publishes on {DEAD_CHANNEL!r}; a "
                "rename silently removes the signal with no test failing"
            )

    def test_the_claim_script_still_publishes_claim_failed_separately(
        self,
    ) -> None:
        """The two channels must not be conflated.

        ``claim-failed`` reports a *recoverable* poll failure (missing payload,
        unreadable spec) and is deliberately a different signal from
        ``dead-letter``. Collapsing them would make an operator page on a
        transient blip.
        """
        from validsim.jobs import queue as queue_mod

        script = queue_mod._REDIS_CLAIM_NEXT_SCRIPT
        assert "validsim:jobs:claim-failed" in script
        assert script.count("validsim:jobs:dead-letter") >= 1
