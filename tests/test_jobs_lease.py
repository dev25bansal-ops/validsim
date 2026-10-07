"""Tests for job leases: expiry, renewal, and the reaper (catalog item 07).

A job claimed by a worker that is then killed must not stay ``running``
forever. These tests cover the fencing contract: a stale worker whose lease was
already reclaimed must not be able to renew or resurrect it.

The clock is driven by monkeypatching :func:`validsim.jobs.queue._utc_now`, the
existing seam, so every deadline expectation is independent of wall time.

Redis-side tests assert the *script contract* (which script, how many keys, what
ARGV) rather than simulating Lua: the in-repo fake client never executes a
script, so a test that pretended otherwise would prove nothing. The one
exception is :class:`TestReaperTimestampComparison`, which needs a Lua engine to
be meaningful and therefore runs against ``fakeredis`` (which executes scripts
via ``lupa``), skipping when either package is absent. There the script is
genuinely interpreted, so the offset cases assert real behavior.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import validsim.jobs.queue as queue_mod
from validsim.jobs import JobQueue, JobRecord, JobSpec, JobStatus, RedisJobQueue

_T0 = "2026-09-25T00:00:00+00:00"
_T0_DEADLINE = "2026-09-25T00:05:00+00:00"
_T0_JUST_BEFORE = "2026-09-25T00:04:59+00:00"
_T0_LATE = "2026-09-25T01:00:00+00:00"


def _spec(run_id: str = "vrun-cafe1234", **overrides: Any) -> JobSpec:
    base: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 60,
        "adversarial": 12,
    }
    base.update(overrides)
    return JobSpec(**base)


def _later(moment: str, *, seconds: int = 3600) -> str:
    """``moment`` shifted forward by ``seconds`` (always a UTC ISO string)."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(moment) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


def _exhaust_reclaims(queue: JobQueue, clock: Any) -> str:
    """Claim/reap a job until its reclaim budget is spent; return the job id."""
    job_id = ""
    for _ in range(queue_mod._MAX_RECLAIMS):
        claimed = queue.claim_next()
        assert claimed is not None
        job_id = claimed.job_id
        clock.advance_to(_later(clock.now))
        assert queue.reap_expired() == [job_id]
    queue.claim_next()
    clock.advance_to(_later(clock.now))
    queue.reap_expired()
    return job_id


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A settable UTC clock driving every queue timestamp and lease deadline."""

    class _Clock:
        now = _T0

        def __call__(self) -> str:
            return self.now

        def advance_to(self, value: str) -> None:
            self.now = value

    frozen = _Clock()
    monkeypatch.setattr(queue_mod, "_utc_now", frozen)
    return frozen


# ---------------------------------------------------------------------------
# TTL resolution
# ---------------------------------------------------------------------------


class TestLeaseTtlResolution:
    def test_default_ttl_is_positive_and_generous(self) -> None:
        assert queue_mod._DEFAULT_LEASE_SECONDS > 0

    def test_explicit_arg_wins(self) -> None:
        assert queue_mod._resolve_lease_seconds(90) == 90

    def test_env_used_when_arg_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(queue_mod._LEASE_ENV, "120")
        assert queue_mod._resolve_lease_seconds(None) == 120

    def test_default_when_nothing_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(queue_mod._LEASE_ENV, raising=False)
        assert queue_mod._resolve_lease_seconds(None) == (
            queue_mod._DEFAULT_LEASE_SECONDS
        )

    def test_blank_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(queue_mod._LEASE_ENV, "   ")
        assert queue_mod._resolve_lease_seconds(None) == (
            queue_mod._DEFAULT_LEASE_SECONDS
        )

    @pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
    def test_malformed_env_rejected(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv(queue_mod._LEASE_ENV, raw)
        with pytest.raises(ValueError, match="positive integer"):
            queue_mod._resolve_lease_seconds(None)

    def test_malformed_arg_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            queue_mod._resolve_lease_seconds(-1)


# ---------------------------------------------------------------------------
# Claiming stamps a lease
# ---------------------------------------------------------------------------


class TestClaimStampsLease:
    def test_claim_sets_lease_deadline_and_epoch(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())

        claimed = q.claim_next()

        assert claimed is not None
        assert claimed.status is JobStatus.RUNNING
        assert claimed.started_at == _T0
        # 300s after 00:00:00, computed by the queue, not by this test.
        assert claimed.lease_expires_at == _T0_DEADLINE
        assert claimed.lease_epoch == 1

    def test_second_claim_of_same_job_bumps_epoch(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())

        first = q.claim_next()
        clock.advance_to(_T0_LATE)
        assert q.reap_expired() == [first.job_id]
        second = q.claim_next()

        assert first is not None and second is not None
        assert second.lease_epoch == first.lease_epoch + 1

    def test_terminal_state_clears_the_lease(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()

        done = q.update_status(jid, JobStatus.DONE, result=jid)

        assert done.lease_expires_at is None

    def test_failed_state_clears_the_lease(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()

        failed = q.update_status(jid, JobStatus.FAILED, error="boom")

        assert failed.lease_expires_at is None


# ---------------------------------------------------------------------------
# Renewal
# ---------------------------------------------------------------------------


class TestLeaseRenewal:
    def test_renew_extends_deadline(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())
        claimed = q.claim_next()
        assert claimed is not None

        clock.advance_to(_T0_LATE)
        renewed = q.renew_lease(claimed.job_id, claimed.lease_epoch)

        assert renewed is not None
        assert renewed.status is JobStatus.RUNNING
        assert renewed.lease_expires_at == "2026-09-25T01:05:00+00:00"
        # Renewal must not disturb the fencing token or the start time.
        assert renewed.lease_epoch == claimed.lease_epoch
        assert renewed.started_at == claimed.started_at

    def test_renew_unknown_job_returns_none(self) -> None:
        assert JobQueue().renew_lease("vrun-ffffffff", 1) is None

    def test_renew_on_queued_job_returns_none(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        assert q.renew_lease(jid, 1) is None

    def test_renew_on_terminal_job_returns_none(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()
        q.update_status(jid, JobStatus.DONE, result=jid)
        assert q.renew_lease(jid, 1) is None

    def test_renew_with_stale_epoch_fails(self, clock: Any) -> None:
        """The fencing property: a zombie worker cannot renew a reclaimed lease."""
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())
        stale = q.claim_next()
        assert stale is not None

        clock.advance_to(_T0_LATE)
        q.reap_expired()
        fresh = q.claim_next()
        assert fresh is not None
        assert fresh.lease_epoch != stale.lease_epoch

        assert q.renew_lease(stale.job_id, stale.lease_epoch) is None
        # The live worker's lease is untouched by the zombie's failed renewal.
        assert q.get(stale.job_id) == fresh

    def test_renew_with_future_epoch_fails(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()
        assert q.renew_lease(jid, 99) is None


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


class TestReaper:
    def test_unexpired_lease_is_not_reaped(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())
        q.claim_next()

        clock.advance_to(_T0_JUST_BEFORE)

        assert q.reap_expired() == []
        assert q.get("vrun-cafe1234").status is JobStatus.RUNNING

    def test_expired_lease_is_requeued(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()

        clock.advance_to(_T0_LATE)
        assert q.reap_expired() == [jid]

        record = q.get(jid)
        assert record is not None
        assert record.status is JobStatus.QUEUED
        assert record.lease_expires_at is None

    def test_reaped_job_can_be_claimed_again(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()
        clock.advance_to(_T0_LATE)
        q.reap_expired()

        reclaimed = q.claim_next()

        assert reclaimed is not None
        assert reclaimed.job_id == jid
        assert reclaimed.status is JobStatus.RUNNING

    def test_queued_jobs_are_never_reaped(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id

        clock.advance_to(_T0_LATE)

        assert q.reap_expired() == []
        assert q.get(jid).status is JobStatus.QUEUED

    def test_terminal_jobs_are_never_reaped(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()
        q.update_status(jid, JobStatus.DONE, result=jid)

        clock.advance_to(_T0_LATE)

        assert q.reap_expired() == []
        assert q.get(jid).status is JobStatus.DONE

    def test_renewed_lease_survives_the_original_deadline(self, clock: Any) -> None:
        """A healthy long-running job heartbeats instead of being reaped."""
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        claimed = q.claim_next()
        assert claimed is not None

        clock.advance_to(_T0_LATE)
        assert q.renew_lease(jid, claimed.lease_epoch) is not None
        clock.advance_to("2026-09-25T01:04:00+00:00")

        assert q.reap_expired() == []
        assert q.get(jid).status is JobStatus.RUNNING

    def test_reaper_returns_all_expired_ids(self, clock: Any) -> None:
        q = JobQueue(lease_seconds=300)
        a = q.enqueue(_spec("vrun-aaaaaaaa")).job_id
        b = q.enqueue(_spec("vrun-bbbbbbbb")).job_id
        q.claim_next()
        q.claim_next()

        clock.advance_to(_T0_LATE)

        assert sorted(q.reap_expired()) == sorted([a, b])


# ---------------------------------------------------------------------------
# Concurrency: two workers, one job
# ---------------------------------------------------------------------------


class TestConcurrentClaim:
    def test_two_threads_never_claim_the_same_job(self, clock: Any) -> None:
        import threading

        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())
        barrier = threading.Barrier(2)
        claims: list[JobRecord] = []
        lock = threading.Lock()

        def claim() -> None:
            barrier.wait(timeout=2.0)
            record = q.claim_next()
            if record is not None:
                with lock:
                    claims.append(record)

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert len(claims) == 1

    def test_reap_then_claim_hands_the_job_to_exactly_one_worker(
        self, clock: Any
    ) -> None:
        import threading

        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        q.claim_next()
        clock.advance_to(_T0_LATE)
        q.reap_expired()

        barrier = threading.Barrier(2)
        claims: list[JobRecord] = []
        lock = threading.Lock()

        def claim() -> None:
            barrier.wait(timeout=2.0)
            record = q.claim_next()
            if record is not None:
                with lock:
                    claims.append(record)

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert len(claims) == 1
        assert claims[0].job_id == jid


# ---------------------------------------------------------------------------
# Redis script contract (no Lua execution — see module docstring)
# ---------------------------------------------------------------------------


class _RecordingRedis:
    """Records ``eval`` calls; never interprets a script."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self.calls.append((script, numkeys, *args))
        if script == queue_mod._REDIS_ENQUEUE_SCRIPT:
            return 1
        return None

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    def exists(self, key: str) -> int:
        return int(key in self._kv)

    def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = list(self._lists.get(key, []))
        return items[start : len(items) if end < 0 else end]

    def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    def close(self) -> None:
        return None


class TestRedisLeaseScripts:
    def _queue(self) -> tuple[RedisJobQueue, _RecordingRedis]:
        client = _RecordingRedis()
        return RedisJobQueue(_client_factory=lambda: client, lease_seconds=300), client

    def test_claim_passes_the_lease_deadline_to_the_claim_script(
        self, clock: Any
    ) -> None:
        queue, client = self._queue()
        queue.enqueue(_spec())

        queue.claim_next()

        claim_calls = [c for c in client.calls if c[0] == queue_mod._REDIS_CLAIM_NEXT_SCRIPT]
        assert len(claim_calls) == 1
        # numkeys, ready key, payload prefix, then ARGV.
        assert claim_calls[0][1] == 1
        assert _T0_DEADLINE in claim_calls[0]

    def test_renew_uses_its_own_script_and_returns_none_when_unanswered(
        self,
    ) -> None:
        queue, client = self._queue()
        queue.enqueue(_spec())

        assert queue.renew_lease("vrun-cafe1234", 1) is None
        renew_calls = [c for c in client.calls if c[0] == queue_mod._REDIS_RENEW_LEASE_SCRIPT]
        assert len(renew_calls) == 1

    def test_reap_uses_its_own_script_over_the_index(
        self,
    ) -> None:
        queue, client = self._queue()
        queue.enqueue(_spec())

        assert queue.reap_expired() == []
        reap_calls = [c for c in client.calls if c[0] == queue_mod._REDIS_REAP_EXPIRED_SCRIPT]
        assert len(reap_calls) == 1
        # Both the insertion index and the ready list must be in the key set.
        assert reap_calls[0][1] == 2

    def test_redis_record_round_trips_the_lease_fields(self) -> None:
        queue, client = self._queue()
        record = JobRecord(
            spec=_spec(),
            status=JobStatus.RUNNING,
            created_at=_T0,
            started_at=_T0,
            lease_expires_at=_T0_DEADLINE,
            lease_epoch=3,
        )
        client.set("validsim:jobs:vrun-cafe1234", json.dumps(record.to_dict()))

        assert queue.get("vrun-cafe1234") == record

    def test_lease_fields_survive_a_legacy_payload_without_them(self) -> None:
        """A payload written before this change must still deserialize."""
        queue, client = self._queue()
        legacy = JobRecord(spec=_spec(), status=JobStatus.RUNNING, created_at=_T0)
        payload = json.loads(json.dumps(legacy.to_dict()))
        payload.pop("lease_expires_at", None)
        payload.pop("lease_epoch", None)
        client.set("validsim:jobs:vrun-cafe1234", json.dumps(payload))

        restored = queue.get("vrun-cafe1234")

        assert restored is not None
        assert restored.lease_expires_at is None
        assert restored.lease_epoch == 0


# ---------------------------------------------------------------------------
# Reaper timestamp comparison — executed for real (fakeredis + lupa)
# ---------------------------------------------------------------------------
#
# The reaper decides whether a lease has expired. Lua has no date type, so the
# obvious implementation compares the ISO-8601 strings with ``<=``. That is only
# equivalent to chronological order while every timestamp carries the *same*
# UTC offset, which is an invariant nothing in the code enforces: it holds today
# only because every writer happens to call the UTC ``_utc_now`` seam.
#
# These tests run the actual script through a Lua interpreter, so they fail if
# the comparison is textual and pass only when it is offset-independent.


@pytest.fixture()
def lua_redis() -> Any:
    """A Redis stand-in that really executes ``EVAL`` (needs ``lupa``)."""
    pytest.importorskip("lupa", reason="Lua execution needs lupa (fakeredis backend)")
    fakeredis = pytest.importorskip("fakeredis", reason="needs fakeredis")
    client = fakeredis.FakeRedis(decode_responses=True)
    try:
        client.eval("return 1", 0)
    except Exception as exc:  # noqa: BLE001 - a Lua-less build must skip, not fail
        pytest.skip(f"fakeredis cannot execute Lua scripts: {exc}")
    return client


def _running_payload(
    job_id: str, lease_expires_at: str, *, created_at: str = _T0
) -> dict[str, Any]:
    """A minimal ``running`` job record carrying ``lease_expires_at`` verbatim."""
    return {
        "job_id": job_id,
        "status": "running",
        "spec": {
            "run_id": job_id,
            "checkpoint_id": "ckpt-1",
            "task_id": "pick-place",
            "episodes": 60,
            "adversarial": 12,
        },
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": None,
        "error": None,
        "result": None,
        "lease_expires_at": lease_expires_at,
        "lease_epoch": 1,
    }


def _seed_running(lua_redis: Any, job_id: str, lease_expires_at: str) -> None:
    """Write one ``running`` job into the index, exactly as ``enqueue``+claim would."""
    lua_redis.set(f"validsim:jobs:{job_id}", json.dumps(_running_payload(job_id, lease_expires_at)))
    lua_redis.rpush("validsim:jobs:index", job_id)


def _reap(lua_redis: Any, now: str, max_depth: int = 1000) -> list[str]:
    """Run the reaper script exactly as :meth:`RedisJobQueue.reap_expired` does.

    The production ARGV is mirrored (prefix, ISO ``now``, max depth, and the same
    instant as epoch seconds) so a change to the calling convention is caught
    here rather than only against a live server.
    """
    raw = lua_redis.eval(
        queue_mod._REDIS_REAP_EXPIRED_SCRIPT,
        2,
        "validsim:jobs:index",
        "validsim:jobs:ready",
        "validsim:jobs:",
        now,
        max_depth,
        str(queue_mod._epoch_seconds(now)),
        1000,
    )
    return [str(job_id) for job_id in (raw or []) if not str(job_id).startswith("!")]


def _status_of(lua_redis: Any, job_id: str) -> str:
    """Read a job's persisted status back out of Redis."""
    return str(json.loads(lua_redis.get(f"validsim:jobs:{job_id}"))["status"])


class TestReaperTimestampComparison:
    """The expiry decision must follow the *instant*, not the text.

    Each case is anchored to :func:`datetime.fromisoformat`, so the test states
    the ground truth ("this lease is in the future") and the script must agree.
    """

    #: ``(job_id, lease deadline, now, is actually expired)``.
    OFFSET_CASES = [
        # Same +00:00 offset: lexical order and chronological order coincide,
        # so these pin the ordinary path and must not regress.
        ("vrun-same00a", "2026-09-25T00:30:00+00:00", _T0_JUST_BEFORE, False),
        ("vrun-same00b", _T0_DEADLINE, _T0_JUST_BEFORE, False),
        ("vrun-same00c", _T0_DEADLINE, "2026-09-25T00:05:00+00:00", True),
        # A deadline written in a NEGATIVE offset reads *earlier* as text while
        # being *later* as an instant. Comparing as strings reaps a lease that is
        # still live for hours, handing a running job to a second worker.
        ("vrun-negoffset", "2026-09-25T09:00:00-05:00", "2026-09-25T12:00:00+00:00", False),
        # The mirror image: a deadline in a POSITIVE offset reads *later* as text
        # while being *earlier* as an instant, so a dead worker's job is never
        # reclaimed and strands in `running` forever.
        ("vrun-posoffset", "2026-09-25T19:00:00+09:00", "2026-09-25T12:00:00+00:00", True),
        # Same instant, two spellings: lexically the +01:00 form sorts later and
        # escapes expiry at the exact deadline.
        ("vrun-sameinst", "2026-09-25T12:00:00+01:00", "2026-09-25T11:00:00+00:00", True),
    ]

    @pytest.mark.parametrize(
        ("job_id", "expires", "now", "expired"),
        OFFSET_CASES,
        ids=[case[0] for case in OFFSET_CASES],
    )
    def test_expiry_follows_the_instant_not_the_text(
        self, lua_redis: Any, job_id: str, expires: str, now: str, expired: bool
    ) -> None:
        from datetime import datetime

        # Guard: the case data itself must be what we think it is.
        assert (datetime.fromisoformat(expires) <= datetime.fromisoformat(now)) is expired

        _seed_running(lua_redis, job_id, expires)

        reaped = _reap(lua_redis, now)

        if expired:
            assert reaped == [job_id]
            assert _status_of(lua_redis, job_id) == "queued"
        else:
            assert reaped == [], "a live lease was stolen by a text comparison"
            assert _status_of(lua_redis, job_id) == "running"

    def test_mixed_offsets_in_one_pass_are_judged_independently(
        self, lua_redis: Any
    ) -> None:
        """Several leases with different offsets in a single reaper sweep.

        A shared textual sort cannot get all of these right: the expired ones
        need reclaiming and the live ones do not, in the same invocation.
        """
        from datetime import datetime

        now = "2026-09-25T12:00:00+00:00"
        # (job id, deadline) with the expected verdict derived below, never by
        # hand -- a mislabelled fixture would otherwise look like a product bug.
        cases = [
            ("vrun-dead0001", "2026-09-25T11:00:00+00:00"),  # 11:00Z, past
            ("vrun-dead0002", "2026-09-25T19:00:00+09:00"),  # 10:00Z, past
            ("vrun-alive01", "2026-09-25T15:00:00+00:00"),  # 15:00Z, future
            ("vrun-alive02", "2026-09-25T22:30:00+01:00"),  # 21:30Z, future
        ]
        reference = datetime.fromisoformat(now)
        for job_id, deadline in cases:
            assert (datetime.fromisoformat(deadline) <= reference) is job_id.startswith(
                "vrun-dead"
            ), f"bad fixture for {job_id}: {deadline} vs {now}"
            _seed_running(lua_redis, job_id, deadline)

        reaped = sorted(_reap(lua_redis, now))

        assert reaped == ["vrun-dead0001", "vrun-dead0002"]
        assert _status_of(lua_redis, "vrun-alive01") == "running"
        assert _status_of(lua_redis, "vrun-alive02") == "running"

    def test_a_running_record_without_a_lease_is_reclaimed(
        self, lua_redis: Any
    ) -> None:
        """No lease, or a null lease, must not strand work forever.

        Such a record predates the lease feature (or was written by an older
        process); leaving it ``running`` forever is the one outcome both backends
        must avoid, so the reaper treats an absent deadline as expired — matching
        the in-memory :func:`validsim.jobs.queue._is_expired`.
        """
        _seed_running(lua_redis, "vrun-nolease1", _T0)
        lua_redis.set(
            "validsim:jobs:vrun-nolease2",
            json.dumps(_running_payload("vrun-nolease2", _T0)),
        )
        payload = _running_payload("vrun-nullease", _T0)
        payload["lease_expires_at"] = None
        lua_redis.set("validsim:jobs:vrun-nullease", json.dumps(payload))
        for job_id in ("vrun-nullease", "vrun-nolease2"):
            lua_redis.rpush("validsim:jobs:index", job_id)

        payload = _running_payload("vrun-garbage", _T0)
        payload["lease_expires_at"] = "not-a-timestamp"
        lua_redis.set("validsim:jobs:vrun-garbage", json.dumps(payload))
        lua_redis.rpush("validsim:jobs:index", "vrun-garbage")

        reaped = sorted(_reap(lua_redis, _T0_LATE))

        assert reaped == ["vrun-garbage", "vrun-nolease1", "vrun-nolease2", "vrun-nullease"]

    def test_reaped_job_is_pushed_to_the_ready_list_once(
        self, lua_redis: Any
    ) -> None:
        """Reclaim is an RPUSH, so a second sweep must not duplicate the entry."""
        _seed_running(lua_redis, "vrun-once0001", "2026-09-25T13:00:00+00:00")

        assert _reap(lua_redis, "2026-09-25T14:00:00+00:00") == ["vrun-once0001"]
        assert lua_redis.lrange("validsim:jobs:ready", 0, -1) == ["vrun-once0001"]
        # Already queued, so the second sweep has nothing running left to reap.
        assert _reap(lua_redis, "2026-09-25T15:00:00+00:00") == []
        assert lua_redis.lrange("validsim:jobs:ready", 0, -1) == ["vrun-once0001"]

    def test_reaper_respects_the_ready_list_capacity(
        self, lua_redis: Any
    ) -> None:
        """A full ready list blocks reclaim, so the cap is never exceeded."""
        for i in range(3):
            _seed_running(lua_redis, f"vrun-cap{i:06d}", "2026-09-25T13:00:00+00:00")

        reaped = _reap(lua_redis, "2026-09-25T14:00:00+00:00", max_depth=2)

        assert len(reaped) == 2
        assert lua_redis.llen("validsim:jobs:ready") == 2
        # The third is left running for a later sweep, not silently lost.
        assert _status_of(lua_redis, "vrun-cap000002") == "running"

    def test_corrupt_payload_in_the_index_does_not_abort_the_sweep(
        self, lua_redis: Any
    ) -> None:
        """One unparseable payload must not stop the whole reap pass.

        ``cjson.decode`` raises on garbage; without ``pcall`` the script aborts
        after partially iterating, so every *later* job in the index would stay
        stranded in ``running``.
        """
        lua_redis.set("validsim:jobs:vrun-broken1", "{not json")
        lua_redis.rpush("validsim:jobs:index", "vrun-broken1")
        _seed_running(lua_redis, "vrun-later001", "2026-09-25T13:00:00+00:00")
        # A JSON scalar decodes fine but is not a record; indexing it raises.
        lua_redis.set("validsim:jobs:vrun-scalar1", "42")
        lua_redis.rpush("validsim:jobs:index", "vrun-scalar1")
        _seed_running(lua_redis, "vrun-last0001", "2026-09-25T13:00:00+00:00")

        reaped = sorted(_reap(lua_redis, "2026-09-25T14:00:00+00:00"))

        assert reaped == ["vrun-last0001", "vrun-later001"]


# ---------------------------------------------------------------------------
# Reclaim (reap) budget: a job whose worker keeps dying must not loop forever
# ---------------------------------------------------------------------------
#
# The reaper returns an expired claim to ``queued`` unconditionally, and nothing
# anywhere counted the reclaims. A worker that dies on every attempt (OOM-killed
# GPU process, unmounted volume, a poison checkpoint) therefore produced an
# unbounded loop: queued -> running -> dead -> reaped -> queued, forever, with the
# job never reaching a terminal state and nothing ever reporting it. The
# opposite failure was just as bad: terminating on the *first* reclaim would
# turn a single OOM-kill into a dead job.
#
# What is needed is a bound, and a *signal* when the bound is reached. These
# tests pin both, on the in-memory backend (which every test can drive without a
# server).


class TestReclaimBudget:
    def test_repeated_reclaims_eventually_reach_the_dead_letter_state(
        self, clock: Any
    ) -> None:
        """A job reclaimed past the budget stops being reclaimable.

        Once the budget is spent the job must land in the dead-letter terminal
        state, so ``reap_expired`` is idempotent afterwards and the loop ends.
        """
        # max_attempts is raised so only the *reclaim* budget is under test: both
        # default to 3, and a claim/reap loop spends them in lockstep, so leaving
        # the defaults would let the attempt budget dead-letter the job first and
        # this test would silently stop measuring the reaper at all.
        q = JobQueue(lease_seconds=300, max_attempts=1000)
        jid = q.enqueue(_spec()).job_id
        for _ in range(queue_mod._MAX_RECLAIMS):
            clock.advance_to(_later(clock.now))
            assert q.claim_next() is not None
            clock.advance_to(_later(clock.now))
            assert q.reap_expired() == [jid], "reap must still work within budget"

        # One more round: the claim succeeds (nothing is wrong with the job), but
        # the reclaim that follows is refused because the budget is spent.
        clock.advance_to(_later(clock.now))
        final = q.claim_next()
        assert final is not None
        clock.advance_to(_later(clock.now))
        assert q.reap_expired() == [], "budget is spent: the job must not loop"
        record = q.get(jid)
        assert record is not None
        assert record.status is JobStatus.DEAD

    def test_dead_lettered_job_is_terminal_for_every_operation(self, clock: Any) -> None:
        """``dead`` is a terminal state, not another flavor of ``running``."""
        q = JobQueue(lease_seconds=300, max_attempts=1000)
        jid = q.enqueue(_spec()).job_id
        _exhaust_reclaims(q, clock)

        # Never claimable again, never reaped, never renewable.
        assert q.claim_next() is None
        assert q.reap_expired() == []
        assert q.renew_lease(jid, 99) is None
        record = q.get(jid)
        assert record is not None
        assert record.status is JobStatus.DEAD
        assert record.finished_at is not None
        assert record.lease_expires_at is None
        assert "reclaim" in (record.error or "").lower()

    def test_a_healthy_job_never_accumulates_reclaims(self, clock: Any) -> None:
        """The count only grows when a lease actually expires.

        A long job heartbeats through many ``running`` intervals; none of that
        is a failure and none of it may consume the reclaim budget.
        """
        q = JobQueue(lease_seconds=300)
        jid = q.enqueue(_spec()).job_id
        claimed = q.claim_next()
        assert claimed is not None
        for step in range(1, queue_mod._MAX_RECLAIMS * 2):
            clock.advance_to(_later(_T0_LATE, seconds=step * 400))
            assert q.renew_lease(jid, claimed.lease_epoch) is not None
            clock.advance_to(_later(_T0_LATE, seconds=step * 400 + 200))
            assert q.reap_expired() == []

        assert q.get(jid).status is JobStatus.RUNNING
        assert q.get(jid).lease_epoch == 1

    def test_reclaim_count_is_incremented_only_by_a_reap(self, clock: Any) -> None:
        """Re-claiming the *same* epoch is not a reclaim: nothing was lost."""
        q = JobQueue(lease_seconds=300)
        q.enqueue(_spec())
        q.claim_next()
        assert q.claim_next() is None  # still running, so no second claim

        clock.advance_to(_T0_LATE)
        assert len(q.reap_expired()) == 1
        record = q.get("vrun-cafe1234")
        assert record is not None
        assert record.reclaim_count == 1

    def test_reclaim_count_is_bounded_by_the_configured_budget(self) -> None:
        """The budget must be a real, non-trivial bound -- not unbounded."""
        assert 1 <= queue_mod._MAX_RECLAIMS <= 100
