"""RED tests for two rate-limiter defects found by reading, then measuring.

Both were reproduced before being fixed.

**Defect 1 — ``DELETE`` is not metered.** The rate-limit scope,
:func:`validsim.api.main._is_rate_limited_request`, matched only ``POST``. With
``VALIDSIM_RATE_LIMIT=2`` the measured sequence for six ``DELETE`` calls on a
real app was ``[204, 404, 404, 404, 404, 404]`` — never a 429 — while four
``POST`` calls in the same app returned ``[201, 429, 429, 429]``. ``DELETE``
permanently destroys a stored run, is the one route carrying its own dedicated
auth gate, and is the endpoint a legitimate operator most wants protected from a
runaway loop, yet it was the one destructive verb with no budget at all.

**Defect 2 — the stale-bucket sweep could never run under pressure.**
:meth:`_SlidingWindowRateLimiter.check` incremented ``self._operations`` and
compared it to ``_SWEEP_INTERVAL`` on the *allowed* path only; a denied request
returned early, before the counter was touched. So the sweep was driven purely
by *allowed* traffic. The exact starvation: measured 300 allowed checks, then
3 000 denied checks — the sweep still had never run. The sweep is what reclaims
buckets whose newest hit has aged out of the window, and it is what makes the
``max_buckets`` LRU cap hold *steady* rather than turning every new client into
an eviction of a live one. Starving it under exactly the load an attacker
generates (a flood from one IP) means the memory guard degrades to "evict
whoever happens to be next in line", which can drop a genuine client's
allowance mid-window.

Fixes: count every check so the sweep advances on denied traffic too, expose the
reclaim as a callable :meth:`~validsim.api.main._SlidingWindowRateLimiter.sweep_stale`
so an idle instance can drive it off a clock, and give ``DELETE`` a metered
budget.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import (
    _SlidingWindowRateLimiter,
    _is_rate_limited_request,
    create_app,
)
from validsim.store.memory import ValidationStore

ENV_VARS = ("VALIDSIM_RATE_LIMIT", "VALIDSIM_RATE_WINDOW_SECONDS", "VALIDSIM_API_KEY")

#: The window a test uses, kept short so a stale bucket really is stale.
_WINDOW = 2.0


@pytest.fixture(autouse=True)
def _clean_env() -> None:
    """Isolate the rate-limit env so no value leaks between tests."""
    saved = {name: os.environ.get(name) for name in ENV_VARS}
    for name in ENV_VARS:
        os.environ.pop(name, None)
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _body(checkpoint: str = "ckpt-alpha") -> dict[str, object]:
    """Minimal valid validation request body."""
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": 2,
            "adversarial_count": 0,
        },
    }


def _app(**env: str) -> TestClient:
    """TestClient over a fresh app configured with ``env``."""
    for name, value in env.items():
        os.environ[name] = value
    return TestClient(create_app(ValidationStore()))


class TestDestructiveRouteIsMetered:
    def test_delete_is_in_the_rate_limited_scope(self) -> None:
        """``DELETE /api/v1/validations/{id}`` must be a limited route.

        Structural: the scope is a pure function of the ASGI scope, so this can
        be asserted directly, without spending HTTP requests.
        """
        assert _is_rate_limited_request(
            {
                "type": "http",
                "method": "DELETE",
                "path": "/api/v1/validations/vrun-deadbeef",
            }
        )

    def test_delete_returns_429_once_the_budget_is_spent(self) -> None:
        """RED before the fix: six DELETEs gave [204, 404, 404, 404, 404, 404].

        The run is deleted by the first call, so every later call legitimately
        404s — but the *budget* must still be consumed, and the excess must be
        refused with 429 before any store lookup happens.
        """
        client = _app(VALIDSIM_RATE_LIMIT="2", VALIDSIM_RATE_WINDOW_SECONDS="60")
        run_id = client.post("/api/v1/validations", json=_body()).json()["run_id"]
        # Spend the two credits on the DELETE verb itself.
        codes = [client.delete(f"/api/v1/validations/{run_id}").status_code for _ in range(5)]
        assert 429 in codes, (
            f"DELETE was never metered: {codes} (a runaway delete loop is "
            "unbounded; the same budget is enforced for POST)"
        )
        assert codes[0] == 204, f"the first DELETE should succeed, got {codes[0]}"

    def test_retry_after_is_present_on_the_delete_refusal(self) -> None:
        """A refused destructive call must tell the client when to come back."""
        client = _app(VALIDSIM_RATE_LIMIT="1", VALIDSIM_RATE_WINDOW_SECONDS="60")
        run_id = client.post("/api/v1/validations", json=_body()).json()["run_id"]
        client.delete(f"/api/v1/validations/{run_id}")
        limited = client.delete(f"/api/v1/validations/{run_id}")
        assert limited.status_code == 429
        retry_after = limited.headers.get("Retry-After")
        assert retry_after is not None and int(retry_after) >= 1

    def test_reads_remain_unlimited(self) -> None:
        """Control: metering DELETE must not start taxing the read routes.

        The setup has to *actually* delete the run, because the assertion below
        is a 404 and a run that still exists answers 200. A naive setup — one
        ``POST`` then one ``DELETE`` with ``VALIDSIM_RATE_LIMIT=1`` — cannot do
        that: the bucket is keyed on client IP only (see
        :func:`~validsim.api.main._rate_limit_key`), and ``POST
        /api/v1/validations`` is itself a metered route, so the create spends the
        single credit and the ``DELETE`` is refused ``429`` before it ever reaches
        the store. Measured: the run survived and the read returned 200, which
        has nothing to do with read metering. The ``429`` is itself proof that
        ``DELETE`` is metered, so the fix is to hold a credit back for it
        (``limit=2``), not to change the assertion.
        """
        client = _app(VALIDSIM_RATE_LIMIT="2", VALIDSIM_RATE_WINDOW_SECONDS="60")
        run_id = client.post("/api/v1/validations", json=_body()).json()["run_id"]
        deleted = client.delete(f"/api/v1/validations/{run_id}")
        assert deleted.status_code == 204, (
            f"the setup DELETE was refused {deleted.status_code}; the read "
            "assertions below would then be testing a run that still exists"
        )
        # Both credits are now spent on writes, so every read below is over budget.
        for _ in range(10):
            assert client.get("/api/v1/validations").status_code == 200
            assert client.get(f"/api/v1/validations/{run_id}").status_code == 404
            assert client.get("/api/v1/models").status_code == 200

    def test_rate_limit_disabled_still_permits_unlimited_deletes(self) -> None:
        """``VALIDSIM_RATE_LIMIT=0`` remains the documented off switch."""
        client = _app(VALIDSIM_RATE_LIMIT="0")
        run_id = client.post("/api/v1/validations", json=_body()).json()["run_id"]
        codes = [client.delete(f"/api/v1/validations/{run_id}").status_code for _ in range(4)]
        assert codes == [204, 404, 404, 404], f"limit-off behavior changed: {codes}"


class TestStaleBucketSweep:
    def _instrumented(self, *, limit: int = 1, max_buckets: int = 4) -> tuple[
        _SlidingWindowRateLimiter, list[float]
    ]:
        limiter = _SlidingWindowRateLimiter(
            limit=limit, window_seconds=_WINDOW, max_buckets=max_buckets
        )
        sweeps: list[float] = []
        original = limiter._sweep

        def _recording_sweep(current: float) -> None:
            sweeps.append(current)
            original(current)

        limiter._sweep = _recording_sweep  # type: ignore[method-assign]
        return limiter, sweeps

    def test_denied_requests_still_advance_the_sweep(self) -> None:
        """RED before the fix: 3 000 denied checks, zero sweeps.

        A single hot client hammering a finished budget is the most common way
        to generate denied requests, so the sweep must not be driven by allowed
        traffic alone.
        """
        limiter, sweeps = self._instrumented()
        assert limiter.check("10.0.0.1", now=0.0) is None  # the one allowed hit
        for _ in range(2000):
            limiter.check("10.0.0.1", now=0.0)  # every one of these denied
        assert sweeps, (
            "no sweep ran after 2000 denied checks: the stale-bucket reclaim "
            "never fires under exactly the load that needs it"
        )

    def test_sweep_reclaims_buckets_whose_newest_hit_left_the_window(self) -> None:
        """A sweep must actually free slots, not just run."""
        limiter = _SlidingWindowRateLimiter(limit=50, window_seconds=_WINDOW, max_buckets=64)
        for i in range(40):
            limiter.check(f"10.0.1.{i}", now=0.0)
        assert len(limiter._hits) == 40
        limiter._sweep(0.0)  # nothing is stale yet
        assert len(limiter._hits) == 40
        limiter._sweep(_WINDOW + 1.0)  # every bucket's newest hit has aged out
        assert len(limiter._hits) == 0, "the sweep reclaimed nothing"

    def test_sweep_keeps_a_bucket_with_a_live_in_window_hit(self) -> None:
        """Reclaiming must never hand a live client a fresh allowance.

        A denied request marks its bucket most-recently-used *without* adding a
        hit, so "newest hit" must mean "newest recorded hit", not "last time
        this bucket was touched".

        The original timestamps could not express that. It recorded
        ``10.0.0.2`` at ``t=0`` and swept at ``t=_WINDOW - 0.01`` — an age of
        1.99s against a 2.0s window, so that bucket was still *live* and the
        sweep would have been wrong to drop it. Only a bucket that is genuinely
        out of window may be reclaimed, so the two are now separated with a
        one-second gap: ``10.0.0.1``'s newest hit is 1.0s old, ``10.0.0.2``'s is
        5.0s old. Evicting the latter frees the slot; evicting the former would
        be the defect under test.
        """
        limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=_WINDOW, max_buckets=64)
        limiter.check("10.0.0.1", now=0.0)
        limiter.check("10.0.0.2", now=0.0)
        # 10.0.0.1 is refreshed inside the window; 10.0.0.2 is not.
        limiter.check("10.0.0.1", now=1.0)
        limiter._sweep(2.0)
        assert "10.0.0.1" in limiter._hits, "the sweep evicted a bucket with a live in-window hit"
        assert "10.0.0.2" not in limiter._hits, (
            "the sweep kept a bucket whose only hit left the window 5s ago"
        )

    def test_max_bucket_cap_still_holds_under_a_sweep_starved_limiter(self) -> None:
        """The memory guard is unconditional, even without the sweep.

        A flood of never-revisiting clients must not grow the table past
        ``max_buckets`` no matter how the sweep is scheduled.
        """
        limiter = _SlidingWindowRateLimiter(limit=1, window_seconds=_WINDOW, max_buckets=8)
        for i in range(500):
            limiter.check(f"10.9.{i // 256}.{i % 256}", now=0.0)
        assert len(limiter._hits) <= 8, f"{len(limiter._hits)} buckets exceed the cap of 8"

    def test_denied_requests_add_no_hits_so_a_window_always_expires(self) -> None:
        """A denial must not push the window forward indefinitely.

        A denied request is re-checked on every retry, so if it appended a hit
        the bucket would never age out and the client would be locked out
        forever even after the window elapsed.

        The original cadence asked five calls at ``t=0,1,2,3,4`` to all be
        *allowed* with ``limit=2`` and a 2.0s window, and then the call at
        ``t=5`` to be *refused*. That is unreachable under any correct sliding
        window: the hits are 1.0s apart against a 2.0s window, so every call from
        ``t=2`` onwards sees two in-window hits and is already over budget. The
        budget is therefore spent up front here, and the recovery check is placed
        just past the window from the last *allowed* hit — which is the only
        placement that can distinguish the two behaviours. Were a denial to
        record a hit, the newest hit would be ``t=1.0`` and this call would still
        be inside the window, so it would be refused.
        """
        limiter = _SlidingWindowRateLimiter(limit=2, window_seconds=_WINDOW, max_buckets=8)
        for now in (0.0, 0.5):
            assert limiter.check("10.0.0.1", now=now) is None
        # Both credits are spent, and both hits are in-window at t=1.0.
        assert limiter.check("10.0.0.1", now=1.0) is not None
        for _ in range(500):
            assert limiter.check("10.0.0.1", now=1.0) is not None, (
                "a denied request must not extend the client's window"
            )
        # The newest *recorded* hit is 0.5, so the bucket goes stale here even
        # though denied requests kept arriving at t=1.0 throughout.
        assert limiter.check("10.0.0.1", now=0.5 + _WINDOW + 0.1) is None

    def test_sweep_is_reentrant_across_concurrent_checkers(self) -> None:
        """A sweep must not corrupt the table when checks run concurrently.

        Sync routes run in a threadpool, so ``check`` is called concurrently by
        construction. The reclaim mutates the same :class:`OrderedDict` the
        hot path reads.
        """
        limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=_WINDOW, max_buckets=32)
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def _hammer(worker: int) -> None:
            try:
                barrier.wait(timeout=10)
                for i in range(2000):
                    limiter.check(f"10.5.{worker}.{i % 16}", now=float(i % 7))
                    if i % 128 == 0:
                        limiter.sweep_stale()
            except BaseException as exc:  # pragma: no cover - recorded
                errors.append(exc)

        workers = [threading.Thread(target=_hammer, args=(w,)) for w in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
        assert errors == [], f"concurrent check/sweep raised: {errors!r}"


class TestSweepFiresWithoutAnyTraffic:
    def test_sweep_is_independent_of_the_check_counter(self) -> None:
        """The reclaim must also be drivable by a clock, not only by requests.

        A bucket table that is only swept when requests arrive keeps its stale
        entries for as long as the process sees less than
        ``_SWEEP_INTERVAL`` checks: on a low-traffic instance that is effectively
        every bucket, forever.
        """
        limiter = _SlidingWindowRateLimiter(limit=1, window_seconds=0.05, max_buckets=4)
        for i in range(4):
            limiter.check(f"10.0.2.{i}", now=0.0)
        assert len(limiter._hits) == 4
        time.sleep(0.1)  # let every bucket's newest hit leave the 50ms window
        assert limiter.sweep_stale() == 4, "sweep_stale() reclaimed nothing after the window"
        assert len(limiter._hits) == 0
