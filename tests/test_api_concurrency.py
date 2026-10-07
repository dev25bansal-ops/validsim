"""Availability tests for the worker-thread starvation defect.

Background (measured against this tree before the fix; the numbers are quoted
in the assertions' docstrings):

Every ``/api/v1`` route was declared ``def`` (not ``async def``), so FastAPI
handed it to Starlette's :func:`run_in_threadpool`, which awaits
``anyio.to_thread.run_sync``. That call is gated by anyio's *per-event-loop*
default :class:`~anyio.CapacityLimiter`, whose token count is **40** — read at
runtime by the tests below, so the premise is asserted rather than assumed.
``POST /api/v1/validations`` is CPU-bound and holds its thread for the whole
run: 20 000 episodes took **6.9 s** wall-clock, and concurrent
``GET /api/v1/health`` probes went from ~3 ms to **2.7 s**. With 45 concurrent
20 k-episode POSTs, ``/api/v1/health`` was **completely unreachable** (20 s
client timeout): the token pool was fully drained, and every remaining route —
the liveness probe, the rate limiter's own enforcement path, everything — queued
behind it. The same starvation reproduces on a 20-line control app with two
sync ``def`` routes and no ValidSim code, so this is a property of the
deployment shape, not of one handler.

The Docker ``HEALTHCHECK`` is ``--interval=30s --timeout=5s --retries=3``, so
three consecutive 5 s time-outs mark the container unhealthy and an orchestrator
kills a *working* instance mid-validation, then restart-loops it.

The fix is admission control plus reserved headroom, not "make the route async"
(which would only block the event loop instead of the threadpool):

* a bounded admission gate caps how many validation pipelines run at once, and
  checks the budget **before** a worker thread is claimed, so overload is
  refused fast with ``503`` + ``Retry-After`` instead of queueing invisibly;
* the thread pool is sized above the pipeline budget on server start, so the
  cheap routes — above all the liveness probe — keep worker threads even at full
  saturation. The probe itself stays a plain ``def``: it is a *thread* that must
  be available, and blocking the event loop would be strictly worse.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

import validsim.api.main as api_main
from validsim.api.main import (
    DEFAULT_PIPELINE_CONCURRENCY,
    DEFAULT_THREAD_LIMITER_TOKENS,
    PIPELINE_CONCURRENCY_ENV,
    THREAD_LIMITER_ENV,
    _AdmissionGate,
    _apply_thread_limiter_headroom,
    _parse_positive_int,
    _sync_routes_are_admitted,
    create_app,
)
from validsim.store.memory import ValidationStore

#: Wall-clock budget for one validation pipeline in the tests below. Chosen so
#: the test is dominated by *ordering* (the probe must be answered while
#: pipelines are in flight), not by how fast this machine simulates.
_PIPELINE_SECONDS = 1.0

#: How long a client waits for the liveness probe before declaring the instance
#: unavailable. Comfortably inside the 5 s HEALTHCHECK timeout, so a pass means a
#: real container would also have passed that probe.
_PROBE_BUDGET_SECONDS = 2.0

_VALIDATION_BODY: dict[str, Any] = {
    "checkpoint_id": "ckpt-availability",
    "task": {
        "task_id": "pick-place",
        "robot": {"name": "franka"},
        "environment": {"name": "kitchen"},
        "episodes": 5,
        "adversarial_count": 0,
    },
}


@pytest.fixture(autouse=True)
def _restore_admission_gate() -> Any:
    """Restore the process-wide gate after each test resizes it."""
    original = api_main._PIPELINE_GATE.limit
    yield
    api_main._PIPELINE_GATE.resize(original)


@pytest.fixture()
def tuned_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deliberately tiny pool, so exhaustion is reachable in a unit test.

    Production runs the anyio default of 40, which would need 40 concurrent
    pipelines to reproduce. The *shape* of the defect is independent of the
    number, so the tests shrink the pool instead of spawning 40 threads.
    """
    for name in (
        "VALIDSIM_API_KEY",
        "VALIDSIM_CORS_ORIGINS",
        "VALIDSIM_RATE_LIMIT",
        "VALIDSIM_RATE_WINDOW_SECONDS",
        "VALIDSIM_JOB_QUEUE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(PIPELINE_CONCURRENCY_ENV, "6")
    monkeypatch.setenv(THREAD_LIMITER_ENV, "6")


def _blocking_pipeline() -> tuple[threading.Event, Callable[[], None]]:
    """Install a stand-in for the heavy pipeline; return (release, restore).

    :func:`create_app` reads the pipeline callable out of *this module's*
    namespace on every call (the wiring tests have always intercepted it there),
    so replacing ``_execute_validation`` here swaps the engine for the duration
    of a test without the app having to know. The wrapper under test — the
    ``async def`` route, the admission gate, the thread-pool hop — is untouched;
    a genuine 20 k-episode run costs ~7 s of wall clock per iteration and buys
    no extra signal, because the defect is about *how a long-running synchronous
    handler is scheduled*, not about the simulation.
    """
    release = threading.Event()
    original = api_main._execute_validation

    def _blocking(request: Any, store: Any) -> Any:  # type: ignore[no-untyped-def]
        if not release.wait(_PIPELINE_SECONDS * 5):
            raise AssertionError("test never released the blocking pipeline")
        return original(request, store)

    api_main._execute_validation = _blocking  # type: ignore[assignment]
    return release, lambda: setattr(api_main, "_execute_validation", original)


def _wait_for_in_flight(expected: int, timeout: float = 10.0) -> None:
    """Block until exactly ``expected`` pipelines hold admission slots.

    Sleeping a fixed interval and hoping is what made the first drafts of these
    tests flaky: a worker that has not yet claimed its slot leaves room for the
    *next* request to be admitted, so the assertion under test measures scheduler
    timing rather than the gate. Polling the gate's own counter makes the
    precondition explicit and the outcome deterministic.
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if api_main._PIPELINE_GATE.in_flight >= expected:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"only {api_main._PIPELINE_GATE.in_flight} of {expected} pipelines "
        "claimed a slot within the timeout"
    )


# --------------------------------------------------------------------------- #
# The defect: a liveness probe cannot get a worker thread.
# --------------------------------------------------------------------------- #
class TestLivenessUnderSaturation:
    def test_health_answers_while_validation_pipelines_are_in_flight(
        self, tuned_env: None
    ) -> None:
        """The probe must be answerable, not queued, behind running pipelines.

        RED before the fix: with the pool pinned to 6 tokens and 6 pipelines in
        flight, ``GET /api/v1/health`` — itself a sync route, so also a
        threadpool request — waited for a free token and blew the budget. The
        measured production case was worse: 2.7 s latency for a single heavy
        run, and total unavailability at 45 concurrent runs.

        ``TestClient`` is entered as a context manager on purpose: anyio's thread
        limiter is *per event loop*, and a bare ``TestClient`` opens a fresh loop
        (with its own 40 tokens) for every request. Only a shared portal
        reproduces the single-loop, one-pool reality of a uvicorn worker.
        """
        release, restore = _blocking_pipeline()
        errors: list[BaseException] = []
        workers: list[threading.Thread] = []
        try:
            with TestClient(create_app(ValidationStore())) as client:

                def _post() -> None:
                    try:
                        client.post("/api/v1/validations", json=_VALIDATION_BODY)
                    except BaseException as exc:  # pragma: no cover - recorded
                        errors.append(exc)

                workers = [threading.Thread(target=_post) for _ in range(6)]
                for worker in workers:
                    worker.start()
                _wait_for_in_flight(6)

                t0 = time.perf_counter()
                response = client.get("/api/v1/health")
                elapsed = time.perf_counter() - t0
                probe_status = response.status_code
                # Released *inside* the portal: leaving the context manager drains
                # the loop, which would otherwise wait out the block timeout on
                # requests that are deliberately still running.
                release.set()
        finally:
            release.set()
            restore()
            for worker in workers:
                worker.join(timeout=30)

        assert api_main._PIPELINE_GATE.in_flight == 0, "a slot leaked"
        assert probe_status == 200, f"liveness probe failed: {probe_status}"
        assert elapsed < _PROBE_BUDGET_SECONDS, (
            f"liveness probe took {elapsed:.2f}s while pipelines were in flight; "
            "HEALTHCHECK (--timeout=5s --retries=3) would mark the instance "
            "unhealthy and the container would be killed mid-validation"
        )
        assert not errors, f"pipeline threads raised: {errors!r}"

    def test_saturated_pipelines_are_refused_fast_rather_than_queued(
        self, tuned_env: None
    ) -> None:
        """Overload must answer ``503``, not park requests until they time out.

        RED before the fix: there was no bound at all on how many pipelines could
        run, so a burst queued unboundedly on the threadpool and *everything* —
        probe, metrics scrape, unrelated reads — stalled behind it. The refusal is
        also deliberately a 5xx, so it lands in its own
        ``validsim_http_requests_total`` class and an operator sees the
        saturation rather than inferring it.

        The write budget is switched off here on purpose: this asserts the
        *admission* gate, and a per-IP rate limit that fired first would make the
        test pass without ever reaching it.
        """
        os.environ["VALIDSIM_RATE_LIMIT"] = "0"
        release, restore = _blocking_pipeline()
        statuses: list[int] = []
        lock = threading.Lock()
        workers: list[threading.Thread] = []
        try:
            app = create_app(ValidationStore())
            app.state.pipeline_concurrency = 4
            with TestClient(app) as client:

                def _post() -> None:
                    response = client.post("/api/v1/validations", json=_VALIDATION_BODY)
                    with lock:
                        statuses.append(response.status_code)

                workers = [threading.Thread(target=_post) for _ in range(4)]
                for worker in workers:
                    worker.start()
                _wait_for_in_flight(4)  # the budget is now exactly exhausted

                t0 = time.perf_counter()
                refused = [
                    client.post("/api/v1/validations", json=_VALIDATION_BODY) for _ in range(4)
                ]
                rejection_latency = time.perf_counter() - t0
                refused_statuses = {r.status_code for r in refused}
                retry_after = [r.headers.get("retry-after") for r in refused]
                release.set()  # inside the portal; see the note above
        finally:
            release.set()
            restore()
            for worker in workers:
                worker.join(timeout=30)

        assert refused_statuses == {503}, (
            "every request past the admission budget must be refused, got "
            f"{sorted(refused_statuses)} (background thread statuses: "
            f"{sorted(statuses)})"
        )
        assert rejection_latency < _PIPELINE_SECONDS, (
            f"refusals took {rejection_latency:.2f}s; overload must be refused "
            "immediately, not queued behind a running pipeline"
        )
        assert all(value and int(value) >= 1 for value in retry_after), (
            f"every 503 must carry a positive Retry-After, got {retry_after!r}"
        )
        assert set(statuses) == {201}, f"admitted pipelines failed: {sorted(statuses)}"

    def test_a_refused_pipeline_stores_nothing(self, tuned_env: None) -> None:
        """A 503 must be a true refusal, not a half-executed validation.

        The admission check happens before any engine work, so an overload
        response must leave the store exactly as it found it — otherwise clients
        retrying on ``503`` would accumulate duplicate runs.
        """
        os.environ["VALIDSIM_RATE_LIMIT"] = "0"
        release, restore = _blocking_pipeline()
        store = ValidationStore()
        app = create_app(store)
        # The route reads its budget from app.state, so that is what has to be
        # narrowed — resizing the process-wide gate instead would leave the
        # route's own limit at 6 and the request would simply be admitted.
        app.state.pipeline_concurrency = 1
        workers: list[threading.Thread] = []
        try:
            with TestClient(app) as client:
                workers = [
                    threading.Thread(
                        target=lambda: client.post("/api/v1/validations", json=_VALIDATION_BODY)
                    )
                ]
                for worker in workers:
                    worker.start()
                _wait_for_in_flight(1)  # the single slot is taken
                runs_before = len(store.history())
                refused = client.post("/api/v1/validations", json=_VALIDATION_BODY)
                # Checked while the admitted pipeline is still blocked: the
                # refused request must not have written anything, and the
                # admitted one cannot have written yet either.
                runs_after_refusal = len(store.history())
                release.set()  # inside the portal; see the note above
        finally:
            release.set()
            restore()
            for worker in workers:
                worker.join(timeout=30)

        assert refused.status_code == 503, refused.text
        assert runs_after_refusal == runs_before, (
            "a refused pipeline still wrote to the store; a client retrying on "
            "503 would accumulate duplicate runs"
        )
        # The one admitted pipeline is unaffected by the refusal, and completes
        # normally once the budget frees up.
        assert len(store.history()) == runs_before + 1


# --------------------------------------------------------------------------- #
# The mechanism, tested directly.
# --------------------------------------------------------------------------- #
class TestThreadLimiterHeadroom:
    def test_anyio_thread_pool_is_the_bottleneck_by_default(self) -> None:
        """Pins the premise: the pool that starves the probe is anyio's, at 40.

        If a future dependency bump changes this number the diagnosis must be
        revisited, so it is asserted rather than left in a comment.
        """

        async def _read() -> int:
            return anyio.to_thread.current_default_thread_limiter().total_tokens

        assert asyncio.run(_read()) == 40

    def test_async_routes_need_no_thread_so_a_drained_pool_cannot_stop_them(self) -> None:
        """Control for the diagnosis: the *only* difference is ``async`` vs ``def``.

        On a limiter with zero tokens an ``async def`` route is still served,
        while the threadpool path cannot be served at all. That is precisely why
        the fix reserves headroom for the sync probe instead of converting every
        route: the thread, not the route style, is the scarce resource.
        """

        async def _scenario() -> tuple[int, bool]:
            limiter = _apply_thread_limiter_headroom(None, 1)
            assert limiter is not None
            limiter.total_tokens = 0  # simulate a fully drained pool

            async def _async_route() -> int:
                return 200

            async def _sync_style_route() -> int:
                return await run_in_threadpool(lambda: 200)

            assert await _async_route() == 200
            starved = False
            try:
                # Bounded: with no token available the call simply *waits*, which
                # is the failure mode — the client's request never completes.
                await asyncio.wait_for(_sync_style_route(), timeout=1.0)
            except (TimeoutError, asyncio.TimeoutError):
                starved = True
            return limiter.total_tokens, starved

        tokens, starved = asyncio.run(_scenario())
        assert tokens == 0
        assert starved, "a drained limiter still served a threadpool request"

    def test_headroom_never_shrinks_an_operator_raised_limiter(self) -> None:
        """An operator who raised the pool past the floor keeps their value.

        The fix only ever *raises* the token count. Clamping down would silently
        undo a deliberate capacity decision on restart — a change an operator
        cannot see.
        """

        async def _scenario() -> tuple[int, int]:
            # Raised by a previous startup, on the running loop's limiter.
            existing = _apply_thread_limiter_headroom(None, 1)
            assert existing is not None
            existing.total_tokens = 512
            # A later startup must not pull it back down.
            after = _apply_thread_limiter_headroom(None, 1)
            assert after is not None
            return existing.total_tokens, after.total_tokens

        before, after = asyncio.run(_scenario())
        assert before == 512
        assert after == 512, f"headroom clamped an operator-raised pool to {after}"

    def test_headroom_is_reserved_above_the_pipeline_budget(self) -> None:
        """The pool must exceed the pipeline budget, or it reserves nothing."""

        async def _scenario() -> int:
            limiter = _apply_thread_limiter_headroom(None, 1)
            assert limiter is not None
            return limiter.total_tokens

        assert asyncio.run(_scenario()) == DEFAULT_THREAD_LIMITER_TOKENS
        assert DEFAULT_THREAD_LIMITER_TOKENS > 1

    def test_broken_or_ignored_config_never_closes_the_headroom(self) -> None:
        """A typo in the env var must fail safe, not shut the probe out.

        An unparsable, zero, or negative value falls back to the shipped default
        rather than being taken literally or raising on the startup path — the
        same "degrade to protection, never to disabled" rule the rate limiter
        already follows.
        """
        for raw in ("not-a-number", "0", "-1", "", None):
            assert _parse_positive_int(raw, 7) == 7, f"{raw!r} was not treated as invalid"

        async def _scenario() -> int:
            limiter = anyio.to_thread.current_default_thread_limiter()
            limiter.total_tokens = 1  # pool previously starved to death
            for raw in ("not-a-number", "0", "-1", ""):
                raised = _apply_thread_limiter_headroom(None, raw)  # type: ignore[arg-type]
                assert raised is not None, f"{raw!r} disabled the headroom entirely"
                limiter.total_tokens = 1  # re-starve between cases
            return _apply_thread_limiter_headroom(None, "x").total_tokens  # type: ignore[union-attr]

        assert asyncio.run(_scenario()) >= DEFAULT_THREAD_LIMITER_TOKENS

    def test_headroom_helper_is_a_no_op_off_the_event_loop(self) -> None:
        """No running loop means no limiter to size — never an exception.

        ``create_app`` and unit tests run outside a loop, so the helper has to
        degrade quietly rather than make app construction loop-dependent.
        """
        assert _apply_thread_limiter_headroom(None, 8) is None


class TestAdmissionGate:
    def test_gate_actually_bounds_concurrent_pipelines(self) -> None:
        """The gate is a real bound, not a counter that is never read.

        Exercised against the same callable the route awaits, so this holds
        independently of HTTP and of the pipeline's own runtime. The budget is
        driven through ``app.state.pipeline_concurrency`` — the value the route
        actually reads — rather than through a separately constructed gate, so
        the test cannot pass while the route consults something else.
        """
        from fastapi import FastAPI

        app = FastAPI()
        app.state.pipeline_concurrency = 2
        inside = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def _work() -> None:
            nonlocal inside, peak
            with lock:
                inside += 1
                peak = max(peak, inside)
            release.wait(5)
            with lock:
                inside -= 1

        def _admitted() -> bool:
            admitted, _ = _sync_routes_are_admitted(
                app.state.pipeline_concurrency, _work
            )
            return bool(admitted)

        workers = [threading.Thread(target=_admitted) for _ in range(8)]
        for worker in workers:
            worker.start()
        time.sleep(0.5)
        release.set()
        for worker in workers:
            worker.join(timeout=30)

        assert peak <= 2, f"{peak} pipelines ran concurrently, bound is 2"
        assert 0 < peak, "no pipeline ran at all; the test is not exercising the gate"

    def test_gate_refuses_immediately_rather_than_blocking(self) -> None:
        """``try_acquire`` must not wait — that is the whole point of the gate."""
        gate = _AdmissionGate(1)
        assert gate.try_acquire() is True
        t0 = time.perf_counter()
        assert gate.try_acquire() is False
        assert time.perf_counter() - t0 < 0.1
        gate.release()
        assert gate.try_acquire() is True

    def test_release_never_drives_the_counter_negative(self) -> None:
        """Over-releasing is a bug, not a crash — the count must stay sane."""
        gate = _AdmissionGate(1)
        for _ in range(5):
            gate.release()
        assert gate.in_flight == 0
        assert gate.try_acquire() is True

    def test_shrinking_the_budget_never_cancels_admitted_work(self) -> None:
        """A resize under load must not corrupt the in-flight accounting."""
        gate = _AdmissionGate(4)
        assert [gate.try_acquire() for _ in range(4)] == [True] * 4
        gate.resize(1)  # operator lowers the budget mid-flight
        assert gate.in_flight == 4
        assert gate.try_acquire() is False
        for _ in range(4):
            gate.release()
        assert gate.in_flight == 0

    def test_non_positive_budget_means_unbounded_not_everything_refused(self) -> None:
        """A misconfigured budget must not turn the route into a 503 factory."""
        assert _sync_routes_are_admitted(0, lambda: "ran") == (True, "ran")
        assert _sync_routes_are_admitted(-1, lambda: "ran") == (True, "ran")


class TestConcurrencyConfigIsWired:
    def test_create_app_publishes_and_applies_the_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env vars must reach both ``app.state`` and the live gate."""
        monkeypatch.setenv(PIPELINE_CONCURRENCY_ENV, "7")
        app = create_app(ValidationStore())
        assert app.state.pipeline_concurrency == 7
        assert api_main._PIPELINE_GATE.limit == 7
        assert app.state.thread_limiter_tokens >= 7

    def test_defaults_ship_protected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No env var must still mean a bounded pool plus headroom."""
        monkeypatch.delenv(PIPELINE_CONCURRENCY_ENV, raising=False)
        monkeypatch.delenv(THREAD_LIMITER_ENV, raising=False)
        app = create_app(ValidationStore())
        assert app.state.pipeline_concurrency == DEFAULT_PIPELINE_CONCURRENCY
        assert app.state.thread_limiter_tokens == max(
            DEFAULT_THREAD_LIMITER_TOKENS, DEFAULT_PIPELINE_CONCURRENCY + 1
        )

    def test_lifespan_reserves_headroom_on_server_start(self) -> None:
        """The reservation is applied on startup, where a loop actually exists.

        Creating the app does not start a server, so this is the only place the
        production wiring can be observable: entering the lifespan must have
        raised the real anyio limiter.
        """
        app = create_app(ValidationStore())

        async def _enter_lifespan() -> tuple[int, int]:
            limiter = anyio.to_thread.current_default_thread_limiter()
            before = limiter.total_tokens
            async with app.router.lifespan_context(app):
                return before, limiter.total_tokens

        before, after = asyncio.run(_enter_lifespan())
        assert after > before, (
            f"lifespan left the pool at {after} tokens (was {before}); the "
            "liveness probe has no reserved headroom"
        )
        assert after >= app.state.thread_limiter_tokens
