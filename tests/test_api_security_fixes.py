"""Security regression tests for the audited rate-limit / auth fixes.

Covers the four issues hardened in :mod:`validsim.api.main`:

* **H1** — the sliding-window limiter must key on the *client IP only*, never
  on the attacker-supplied ``X-API-Key`` header. Two requests that carry
  different ``X-API-Key`` values but originate from the same client must share
  one bucket (so the header can no longer mint fresh buckets to dodge the
  limit).
* **H2** — the limiter's working set is bounded: a max-bucket cap evicts the
  least-recently-touched client, and a periodic sweep drops buckets whose
  newest hit has aged out of the window, so never-revisited IPs cannot leak
  memory.
* **H3** — ``POST /api/v1/jobs`` (the async enqueue route) is rate limited like
  the other write routes; reads stay unlimited.
* **M3** — the destructive ``DELETE`` endpoint requires a valid ``X-API-Key``
  whenever ``VALIDSIM_API_KEY`` is configured (401 before the 404 lookup), and
  stays open only when no key is configured (local/dev).

Auth/rate-limit/job-queue env is read at :func:`create_app` time, so each test
sets the environment, builds a fresh app, and restores it via ``clean_env``.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import (
    API_KEY_HEADER,
    RATE_LIMIT_ENV,
    RATE_WINDOW_ENV,
    _rate_limit_key,
    _SlidingWindowRateLimiter,
    _SWEEP_INTERVAL,
    create_app,
)
from validsim.store.memory import ValidationStore

ENV_KEY = "VALIDSIM_API_KEY"
ENV_ORIGINS = "VALIDSIM_CORS_ORIGINS"
# Cleared so the wired jobs queue is always in-memory (never Redis).
ENV_JOB_QUEUE = "VALIDSIM_JOB_QUEUE"
ENV_REDIS_URL = "VALIDSIM_REDIS_URL"
ENV_JOB_DEPTH = "VALIDSIM_JOB_QUEUE_MAX_DEPTH"

#: Env vars this suite owns; popped before each test and restored afterwards so
#: no value (especially the API key) leaks between cases.
_MANAGED_ENV = (
    ENV_KEY,
    ENV_ORIGINS,
    RATE_LIMIT_ENV,
    RATE_WINDOW_ENV,
    ENV_JOB_QUEUE,
    ENV_REDIS_URL,
    ENV_JOB_DEPTH,
)


@pytest.fixture()
def clean_env() -> None:
    """Isolate auth/CORS/rate-limit/job-queue env so nothing leaks between tests."""
    saved = {name: os.environ.get(name) for name in _MANAGED_ENV}
    for name in _MANAGED_ENV:
        os.environ.pop(name, None)
    yield  # type: ignore[misc]
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _client() -> TestClient:
    """Fresh TestClient over an app with its own store and limiter."""
    return TestClient(create_app(ValidationStore()))


def _request_body(checkpoint: str = "ckpt-alpha") -> dict[str, object]:
    """Minimal valid validation request body (small episode count)."""
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": 2,
            "adversarial_count": 1,
        },
    }


def _job_body(**overrides: Any) -> dict[str, Any]:
    """Minimal valid body for POST /api/v1/jobs."""
    body: dict[str, Any] = {"checkpoint_id": "ckpt-sec", "task_id": "pick-place", "episodes": 1}
    body.update(overrides)
    return body


def _create_run(client: TestClient, headers: dict[str, str] | None = None) -> str:
    """Create one validation run and return its id."""
    response = client.post("/api/v1/validations", json=_request_body(), headers=headers or {})
    assert response.status_code == 201, response.text
    return str(response.json()["run_id"])


# --------------------------------------------------------------------------- #
# H1 — rate limiter keys on client IP, not the attacker-supplied header        #
# --------------------------------------------------------------------------- #


class TestRateLimitKeyedByIp:
    def test_different_api_keys_share_one_bucket(self, clean_env: None) -> None:
        """Two different ``X-API-Key`` values from the same client share a bucket.

        This is the inverse of the old (insecure) per-key bucketing: rotating
        the header must NOT buy a fresh allowance, so the second request —
        carrying a brand-new key — is already over the limit of one.
        """
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        first = client.post(
            "/api/v1/validations", json=_request_body(), headers={API_KEY_HEADER: "key-a"}
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/validations", json=_request_body(), headers={API_KEY_HEADER: "key-b"}
        )
        assert second.status_code == 429

    def test_rate_limit_key_ignores_api_key_header(self) -> None:
        """The bucket key is the client IP even when an ``X-API-Key`` is present."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/validations",
            "client": ("203.0.113.7", 54321),
            "headers": [(b"x-api-key", b"attacker-rotated-key")],
        }
        assert _rate_limit_key(scope) == "203.0.113.7"

    def test_rate_limit_key_falls_back_to_unknown_without_client(self) -> None:
        """No client tuple -> a single shared ``unknown`` bucket (header ignored)."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/validations",
            "headers": [(b"x-api-key", b"whatever")],
        }
        assert _rate_limit_key(scope) == "unknown"


# --------------------------------------------------------------------------- #
# H2 — bounded working set: max-bucket cap + periodic stale-bucket sweep       #
# --------------------------------------------------------------------------- #


class TestLimiterMemoryBounds:
    def test_cap_evicts_oldest_bucket(self) -> None:
        """Once at ``max_buckets`` a new client evicts the least-recently-used."""
        limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=60.0, max_buckets=3)
        assert limiter.check("a", now=100.0) is None
        assert limiter.check("b", now=101.0) is None
        assert limiter.check("c", now=102.0) is None
        assert len(limiter._hits) == 3
        # A fourth distinct client pushes past the cap; the LRU head ("a") goes.
        assert limiter.check("d", now=103.0) is None
        assert len(limiter._hits) == 3
        assert "a" not in limiter._hits
        assert set(limiter._hits) == {"b", "c", "d"}

    def test_touch_refreshes_lru_recency(self) -> None:
        """A re-touched bucket moves to the MRU end and is evicted last."""
        limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=60.0, max_buckets=3)
        limiter.check("a", now=100.0)
        limiter.check("b", now=101.0)
        limiter.check("c", now=102.0)
        # Re-touch "a" so it becomes most-recently-used; "b" is now the LRU head.
        limiter.check("a", now=103.0)
        limiter.check("d", now=104.0)
        assert "b" not in limiter._hits
        assert "a" in limiter._hits

    def test_sweep_drops_stale_and_empty_buckets(self) -> None:
        """``_sweep`` removes buckets whose newest hit left the window (or empty)."""
        limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=10.0)
        limiter.check("stale", now=0.0)
        limiter.check("recent", now=100.0)
        limiter._hits["empty"] = deque()
        assert {"stale", "recent", "empty"} <= set(limiter._hits)
        limiter._sweep(current=100.0)
        # "stale" newest hit (t=0) is >window old -> dropped; empty -> dropped;
        # "recent" (t=100) is fresh -> kept.
        assert "stale" not in limiter._hits
        assert "empty" not in limiter._hits
        assert "recent" in limiter._hits

    def test_periodic_sweep_reclaims_never_revisited(self) -> None:
        """A one-shot visitor is reclaimed by the operations-triggered sweep."""
        limiter = _SlidingWindowRateLimiter(limit=2, window_seconds=10.0)
        limiter.check("ghost", now=0.0)  # a client that never comes back
        assert "ghost" in limiter._hits
        # Drive the limiter past the sweep interval with a client whose hits are
        # always in-window (time advances more than the window between calls so
        # every check is allowed and increments the operations counter).
        t = 1000.0
        for _ in range(_SWEEP_INTERVAL + 5):
            limiter.check("driver", now=t)
            t += 11.0
        assert "ghost" not in limiter._hits
        assert "driver" in limiter._hits


# --------------------------------------------------------------------------- #
# H3 — POST /api/v1/jobs is rate limited; reads are not                        #
# --------------------------------------------------------------------------- #


class TestJobsRateLimited:
    def test_jobs_post_is_rate_limited(self, clean_env: None) -> None:
        """The async enqueue route shares the per-client write budget."""
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        first = client.post("/api/v1/jobs", json=_job_body())
        assert first.status_code == 202
        second = client.post("/api/v1/jobs", json=_job_body())
        assert second.status_code == 429
        assert second.json()["detail"] == "rate limit exceeded"

    def test_jobs_read_not_rate_limited(self, clean_env: None) -> None:
        """Listing jobs is a read and stays unlimited even when the budget is spent."""
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        # Spend the single write credit on the enqueue route.
        assert client.post("/api/v1/jobs", json=_job_body()).status_code == 202
        for _ in range(10):
            assert client.get("/api/v1/jobs").status_code == 200


# --------------------------------------------------------------------------- #
# M3 — destructive DELETE requires auth whenever a key is configured           #
# --------------------------------------------------------------------------- #


class TestDeleteRequiresAuth:
    def test_delete_missing_key_401_when_configured(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        run_id = _create_run(client, headers={API_KEY_HEADER: "secret-key"})
        assert client.delete(f"/api/v1/validations/{run_id}").status_code == 401

    def test_delete_wrong_key_401_when_configured(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        run_id = _create_run(client, headers={API_KEY_HEADER: "secret-key"})
        response = client.delete(
            f"/api/v1/validations/{run_id}", headers={API_KEY_HEADER: "nope"}
        )
        assert response.status_code == 401

    def test_delete_correct_key_succeeds(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        run_id = _create_run(client, headers={API_KEY_HEADER: "secret-key"})
        response = client.delete(
            f"/api/v1/validations/{run_id}", headers={API_KEY_HEADER: "secret-key"}
        )
        assert response.status_code == 204
        # The run really is gone.
        assert client.get(
            f"/api/v1/validations/{run_id}", headers={API_KEY_HEADER: "secret-key"}
        ).status_code == 404

    def test_delete_auth_runs_before_404(self, clean_env: None) -> None:
        """An unknown id without a key is 401 (auth), not 404 (route logic)."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.delete("/api/v1/validations/vrun-missing1").status_code == 401

    def test_delete_open_when_auth_unconfigured(self, clean_env: None) -> None:
        """With no ``VALIDSIM_API_KEY`` the endpoint stays open (documented dev path)."""
        client = _client()
        run_id = _create_run(client)
        assert client.delete(f"/api/v1/validations/{run_id}").status_code == 204


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
