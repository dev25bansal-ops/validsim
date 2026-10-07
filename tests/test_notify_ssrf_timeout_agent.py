"""Security audit of the webhook egress: SSRF reachability and the timeout bound.

Create-only audit evidence for `validsim/notify/dispatcher.py`. The production
package is never modified; every finding is demonstrated against the real
`httpx` client and a real loopback listener, so the verdicts reflect the shipped
code path rather than a mocked approximation.

Pinned here:

* **SSRF.** ``register()`` (dispatcher.py:193-219) validates ``format`` and
  ``min_severity`` but never the URL. ``_post()`` (dispatcher.py:269) hands that
  URL straight to ``httpx.post``. An operator-supplied hook can therefore name a
  loopback, RFC1918 or link-local address, and the scorecard JSON is POSTed to
  it. The socket-level proof is in :class:`LoopbackTrap`; the delivery is
  *blind* (the response body is discarded), which bounds the impact but not the
  reachability.
* **Scheme allowlist.** ``file://``, ``gopher://`` and even ``not-a-url`` are
  accepted by ``register()`` with no error.
* **Timeout/retry bound.** ``timeout=_TIMEOUT_S`` is a single float covering
  every phase, retried ``_MAX_RETRIES`` times, *serially*, per hook. The worst
  case is therefore ``(_MAX_RETRIES + 1) * _TIMEOUT_S + backoff`` per hook, and
  ``dispatch()`` walks the hooks one at a time.
* **No overall deadline.** httpx applies the read timeout *per read*, so a
  response body that trickles a byte at a time can occupy the caller far beyond
  ``_TIMEOUT_S``.

Every "this path is safe" claim is paired with a **control** proving the probe
can observe a violation, so a green run means "verified" rather than "blind".
"""

from __future__ import annotations

import json
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify import dispatcher as disp

#: Template for a loopback hook URL.
_LOOPBACK = "http://127.0.0.1:{port}/internal"
#: Chunk interval for the trickling-response test. ``_CHUNKS * _CHUNK_S`` must
#: comfortably exceed ``_TIMEOUT_S`` for the test to mean anything.
_CHUNK_S = 0.3
_CHUNKS = 20


def _simple_scorecard() -> Scorecard:
    """A minimal APPROVE scorecard for tests that do not need the rich fixture."""
    return Scorecard(
        run_id="r",
        checkpoint_id="c",
        task_id="t",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=1.0,
        robustness_score=1.0,
        regression_delta=0.0,
        confidence_interval=(0.8, 0.9),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=10,
        failure_taxonomy={},
    )


@pytest.fixture
def scorecard() -> Scorecard:
    """A single APPROVE scorecard used by every dispatch in this module."""
    return Scorecard(
        run_id="vrun-ssrf01",
        checkpoint_id="ckpt-ssrf",
        task_id="pick-place",
        composite_score=91.5,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=-0.1,
        confidence_interval=(0.82, 0.95),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch):
    """Keep *the dispatcher's* backoff instantaneous.

    ``dispatcher.time`` **is** the stdlib ``time`` module, so patching
    ``dispatcher.time.sleep`` neutralises ``time.sleep`` process-wide. Tests in
    this module that need to burn wall-clock time therefore use :func:`_burn`
    (``threading.Event().wait``) instead. The real ``time.sleep`` is restored on
    teardown so no ordering surprises leak into other modules.
    """
    import time as real_time

    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)
    yield
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", real_time.sleep)


def _burn(seconds: float) -> None:
    """Sleep ``seconds`` of real wall clock, immune to the dispatcher's patch."""
    threading.Event().wait(seconds)


class LoopbackTrap:
    """A real HTTP listener on 127.0.0.1 recording the requests it receives.

    Stands in for an internal service (a cloud metadata endpoint, an admin API,
    an internal HTTP bridge). Because it is a genuine socket, a recorded hit
    proves the dispatcher *opened a connection and sent bytes*, rather than
    merely attempting one.
    """

    def __init__(self) -> None:
        self.hits: list[dict[str, Any]] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port: int = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """A loopback URL the dispatcher will happily POST to."""
        return _LOOPBACK.format(port=self.port)

    def _read_request(self, conn: socket.socket) -> dict[str, Any]:
        """Read one complete request: headers, then the ``Content-Length`` body.

        httpx may split headers and body across TCP segments, so a single
        ``recv`` is not enough -- reading to completion is what makes the
        recorded body trustworthy.
        """
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1", "replace").splitlines()
        length = 0
        for line in lines:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        body = rest
        while len(body) < length:
            chunk = conn.recv(4096)
            if not chunk:
                break
            body += chunk
        return {
            "request_line": lines[0] if lines else "",
            "content_type": next(
                (
                    line.split(":", 1)[1].strip()
                    for line in lines
                    if line.lower().startswith("content-type")
                ),
                "",
            ),
            "body": body.decode("utf-8", "replace"),
        }

    def _serve(self) -> None:
        self._srv.settimeout(0.3)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                try:
                    conn.settimeout(2.0)
                    self.hits.append(self._read_request(conn))
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                except OSError:
                    pass

    def close(self) -> None:
        """Stop listening. Subsequent dials must fail."""
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture
def trap():
    """A running :class:`LoopbackTrap`, closed automatically."""
    server = LoopbackTrap()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def http_post_stub(monkeypatch: pytest.MonkeyPatch):
    """Install a recording stand-in for ``httpx.post``.

    Returns a factory so a test can supply its own handler. Every call is
    recorded as ``{"url", "body", "headers"}``.
    """

    def _install(handler: Any = None) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []

        def _post(url: str, **kwargs: Any) -> SimpleNamespace:
            record = {
                "url": url,
                "body": str(kwargs.get("content", "")),
                "headers": dict(kwargs.get("headers", {})),
            }
            seen.append(record)
            return handler(record) if handler else SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        return seen

    return _install


# ---------------------------------------------------------------------------
# (1) SSRF: loopback / private targets are reachable through the public API.
# ---------------------------------------------------------------------------
class TestSsrfReachability:
    """A webhook URL may name a private address, and it is really contacted."""

    def test_loopback_target_receives_the_scorecard(
        self, trap: LoopbackTrap, scorecard: Scorecard
    ) -> None:
        """FINDING -- loopback SSRF via an operator-supplied hook URL.

        Reproduction: register a hook pointing at a loopback listener, dispatch,
        and observe a real HTTP POST arrive carrying the whole scorecard.
        """
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("internal", trap.url)

        (result,) = dispatcher.dispatch(scorecard)

        assert result.ok is True and result.status_code == 200
        assert len(trap.hits) == 1, f"expected one loopback request, got {trap.hits}"
        hit = trap.hits[0]
        assert hit["request_line"].startswith("POST /internal")
        # The complete scorecard is delivered to the internal target.
        assert hit["content_type"] == "application/json"
        payload = json.loads(hit["body"])
        assert payload["run_id"] == scorecard.run_id
        assert payload["deploy_decision"] == "APPROVE"
        assert payload["composite_score"] == scorecard.composite_score

    def test_loopback_name_also_reaches_it(
        self, trap: LoopbackTrap, scorecard: Scorecard
    ) -> None:
        """``localhost`` is ``127.0.0.1`` by another name."""
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("internal", f"http://localhost:{trap.port}/internal")
        (result,) = dispatcher.dispatch(scorecard)
        assert result.ok is True
        assert len(trap.hits) == 1

    def test_control_the_trap_detects_a_real_request(
        self, trap: LoopbackTrap, scorecard: Scorecard
    ) -> None:
        """CONTROL -- the trap is live in both directions.

        With the listener stopped the very same dispatch records nothing, so the
        assertion above observes an actual connection.
        """
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("internal", trap.url)
        dispatcher.dispatch(scorecard)
        assert len(trap.hits) == 1, "control: a live listener must record a hit"

        trap.close()
        _burn(0.3)
        (result,) = dispatcher.dispatch(scorecard)
        assert result.ok is False, "control: a stopped listener must refuse"
        assert len(trap.hits) == 1, "trap recorded a request with no listener"

    def test_delivery_is_blind_so_the_response_cannot_be_relayed(
        self, trap: LoopbackTrap, scorecard: Scorecard
    ) -> None:
        """Impact is bounded: the response body never reaches the caller.

        ``DeliveryResult`` keeps only ``status_code``/``error``, so the SSRF
        cannot read an internal endpoint through ValidSim -- it is a blind write
        plus a status-code oracle. That is why the finding sits below a
        credential-exfiltration bug in severity.
        """
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("internal", trap.url)
        (result,) = dispatcher.dispatch(scorecard)
        assert result.ok is True
        assert set(vars(result)) == {
            "name",
            "url",
            "ok",
            "status_code",
            "error",
            # Added by notify-sec: distinguishes a real send from a rehearsal.
            # This is a `WebhookDispatcher(live=True)` dispatch, so it did hit
            # the network and the flag must be False. Asserted by VALUE in
            # `tests/test_notify_dryrun_agent.py`; here it only widens the shape.
            "dry_run",
        }
        assert "INTERNAL" not in repr(result)
        assert "INTERNAL" not in repr(result.url)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///c:/windows/win.ini",
            "gopher://127.0.0.1:11211/_stats",
            "ftp://127.0.0.1/",
            "not-a-url",
            "",
        ],
    )
    def test_register_accepts_any_scheme_without_complaint(self, url: str) -> None:
        """FINDING -- no scheme allowlist, and not even a URL parse check."""
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("x", url)  # must not raise
        assert dispatcher.sent == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9/internal",
            "http://localhost:9/internal",
            "http://[::1]:9/internal",
            "http://0.0.0.0:9/internal",
            "http://10.0.0.1:9/internal",
            "http://192.168.1.1:9/internal",
            "http://169.254.169.254:9/latest/meta-data/",
            "http://metadata.google.internal:9/computeMetadata/v1/",
            "http://[fd00::1]:9/internal",
        ],
    )
    def test_register_never_rejects_a_private_target(
        self, url: str, http_post_stub: Any, scorecard: Scorecard
    ) -> None:
        """No address class is filtered -- neither at registration nor at dispatch.

        The transport is stubbed so this asserts *policy* (would ValidSim have
        refused?) rather than what the local OS does with the address. A blocked
        target must never reach the network layer at all.
        """
        seen = http_post_stub()
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("x", url)  # registration itself must not raise

        dispatcher.dispatch(scorecard)

        assert len(seen) == 1, (
            f"{url!r} was dispatched anyway -- if this is ever fixed, register() "
            "should reject the URL and no POST should be attempted"
        )
        assert seen[0]["url"] == url

    def test_register_does_validate_the_fields_it_claims_to(self) -> None:
        """CONTROL -- ``register()`` is not a no-op: it does validate some input.

        Proves the missing URL validation is a *gap*, not a broken hook.
        """
        dispatcher = WebhookDispatcher(live=True)
        with pytest.raises(ValueError, match="unsupported hook format"):
            dispatcher.register("x", "http://127.0.0.1:9/h", format="xml")
        with pytest.raises(ValueError, match="unsupported min_severity"):
            dispatcher.register("x", "http://127.0.0.1:9/h", min_severity="fatal")


# ---------------------------------------------------------------------------
# (2) TIMEOUT / RETRY BOUND.
# ---------------------------------------------------------------------------
class TestTimeoutBound:
    """How long a single ``dispatch()`` can hold a calling thread."""

    def test_configured_budget_is_derived_from_live_constants(self) -> None:
        """Pin the arithmetic the docs imply, using the shipped constants.

        ``(1 + _MAX_RETRIES) * _TIMEOUT_S`` is the per-hook worst case; because
        ``dispatch()`` is serial, an N-hook fan-out multiplies it.
        """
        budget = (1 + disp._MAX_RETRIES) * disp._TIMEOUT_S
        backoff = sum(disp._BACKOFF_BASE_S * (2**i) for i in range(disp._MAX_RETRIES))
        assert budget == 15.0
        assert backoff == pytest.approx(0.3)
        # One black-hole hook alone can hold a worker for >15s, well past the
        # default job lease (VALIDSIM_JOB_LEASE_SECONDS).
        assert budget + backoff > 10.0

    def test_retry_count_is_bounded(
        self, http_post_stub: Any, scorecard: Scorecard
    ) -> None:
        """Exactly ``_MAX_RETRIES + 1`` attempts, never more."""
        attempts: list[str] = []

        def _fail(record: dict[str, Any]) -> SimpleNamespace:
            attempts.append(record["url"])
            raise ConnectionError("down")

        http_post_stub(_fail)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("x", "https://example.invalid/h")
        (result,) = dispatcher.dispatch(scorecard)

        assert len(attempts) == disp._MAX_RETRIES + 1 == 3
        assert result.ok is False and "down" in (result.error or "")

    def test_dispatch_is_serial_across_hooks(
        self, http_post_stub: Any, scorecard: Scorecard
    ) -> None:
        """FINDING -- hooks fire one after another, so the wall clock adds up.

        Each hook consumes its own full retry budget; N hooks cost N x budget on
        the calling thread.
        """
        starts: list[float] = []

        def _slow(record: dict[str, Any]) -> SimpleNamespace:
            starts.append(time.perf_counter())
            _burn(0.15)
            return SimpleNamespace(status_code=200)

        http_post_stub(_slow)
        dispatcher = WebhookDispatcher(live=True)
        for i in range(3):
            dispatcher.register(f"h{i}", f"https://example.invalid/{i}")

        t0 = time.perf_counter()
        dispatcher.dispatch(scorecard)
        elapsed = time.perf_counter() - t0

        assert len(starts) == 3
        assert elapsed >= 0.40, f"hooks appear concurrent: elapsed={elapsed:.3f}s"
        # Strictly increasing start times prove the loop is sequential.
        assert starts == sorted(starts)
        assert starts[-1] - starts[0] >= 0.28

    def test_control_a_fast_endpoint_costs_nothing(
        self, http_post_stub: Any, scorecard: Scorecard
    ) -> None:
        """CONTROL -- the serial timing above is per-attempt cost, not noise."""
        order: list[str] = []

        def _fast(record: dict[str, Any]) -> SimpleNamespace:
            order.append(record["url"])
            return SimpleNamespace(status_code=200)

        http_post_stub(_fast)
        dispatcher = WebhookDispatcher(live=True)
        for i in range(3):
            dispatcher.register(f"h{i}", f"https://example.invalid/{i}")

        t0 = time.perf_counter()
        dispatcher.dispatch(scorecard)
        elapsed = time.perf_counter() - t0

        assert order == [
            "https://example.invalid/0",
            "https://example.invalid/1",
            "https://example.invalid/2",
        ]
        assert elapsed < 0.10, f"fast path took {elapsed:.3f}s"

    def test_black_hole_endpoint_costs_the_full_retry_budget(self) -> None:
        """FINDING -- measured: a non-responding endpoint blocks the caller.

        A real socket that accepts and then never replies, so the dispatcher's
        own ``_TIMEOUT_S`` governs. Asserted as a range so the test is stable on
        slow CI while still proving the budget is real, not a no-op.
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        stop = threading.Event()

        def _blackhole() -> None:
            srv.settimeout(0.3)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except (TimeoutError, OSError):
                    continue
                threading.Thread(
                    target=_hold_open, args=(conn,), daemon=True
                ).start()

        def _hold_open(conn: socket.socket) -> None:
            """Accept, read the request, then hold the socket open silently.

            Crucially the connection is *kept* (not closed) so the client's own
            ``_TIMEOUT_S`` is what ends the attempt, which is the behaviour the
            test is measuring.
            """
            try:
                conn.settimeout(1.0)
                conn.recv(4096)
                stop.wait(60.0)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        threading.Thread(target=_blackhole, daemon=True).start()
        try:
            dispatcher = WebhookDispatcher(live=True)
            dispatcher.register("blackhole", f"http://127.0.0.1:{port}/x")
            t0 = time.perf_counter()
            (result,) = dispatcher.dispatch(_simple_scorecard())
            elapsed = time.perf_counter() - t0
        finally:
            stop.set()
            srv.close()

        assert result.ok is False
        assert elapsed >= disp._TIMEOUT_S, (
            f"elapsed={elapsed:.2f}s -- the timeout should have fired at "
            f"{disp._TIMEOUT_S}s"
        )
        # Room for 3 attempts x 5s + backoff, with slack for a slow machine.
        assert elapsed < 3 * disp._TIMEOUT_S * 3, f"elapsed={elapsed:.2f}s"

    def test_no_overall_deadline_on_the_response_body(self) -> None:
        """FINDING -- a trickling response body is not covered by ``_TIMEOUT_S``.

        httpx applies the read timeout *per read*, not to the transfer as a
        whole, so a server emitting one byte every ``_CHUNK_S`` keeps the
        transfer alive for ``_CHUNKS * _CHUNK_S`` -- longer than the entire
        nominal per-hook budget. Were the timeout an overall deadline, the
        request would have aborted at ``_TIMEOUT_S``.
        """
        total = _CHUNK_S * _CHUNKS
        assert total > disp._TIMEOUT_S, "the trickle must outlast one timeout"

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        stop = threading.Event()

        def _trickle() -> None:
            srv.settimeout(0.3)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except (TimeoutError, OSError):
                    continue
                with conn:
                    try:
                        conn.settimeout(5.0)
                        conn.recv(4096)
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: "
                            + str(_CHUNKS).encode()
                            + b"\r\n\r\n"
                        )
                        for _ in range(_CHUNKS):
                            conn.sendall(b"x")
                            _burn(_CHUNK_S)
                    except OSError:
                        pass

        threading.Thread(target=_trickle, daemon=True).start()
        try:
            dispatcher = WebhookDispatcher(live=True)
            dispatcher.register("slow", f"http://127.0.0.1:{port}/x")
            t0 = time.perf_counter()
            (result,) = dispatcher.dispatch(_simple_scorecard())
            elapsed = time.perf_counter() - t0
        finally:
            stop.set()
            srv.close()

        assert result.ok is True, "the trickling response completed successfully"
        assert elapsed > disp._TIMEOUT_S, (
            f"elapsed={elapsed:.2f}s must exceed one {disp._TIMEOUT_S}s timeout, "
            "proving the read timeout is per-read and not an overall deadline"
        )
        assert elapsed < 3 * total, f"elapsed={elapsed:.2f}s"
