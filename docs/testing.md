---
tags: [engineering, testing, ci, pytest, coverage]
status: prototype (W6)
---

# 🧪 ValidSim Testing Strategy

How the suite is organised, why it stays green on a laptop with no GPU, no
Postgres, and no network, and how the same tests gate every deploy in CI. This
is the engineer-facing counterpart to [[GitHub Actions Integration]] and
[docs/runbook.md](runbook.md); the commands below are the exact ones
`.github/workflows/ci.yml` runs.

> [!important] The reproducibility contract
> Every simulation test is **deterministic**. The mock backend is seeded, the
> property/fuzz sweeps pin a master seed, and the live-dependency tests
> (Postgres, reportlab) self-skip when the dependency is absent. A green run on
> CI is byte-for-byte reproducible on your machine — there are no flaky tests
> to re-run, and `python -m pytest tests/` needs no Docker, GPU, or API key.

## 1. Suite at a glance

| Aspect | Value |
|---|---|
| Runner | pytest (`pytest>=8.0`, `pytest-cov>=5.0` in [requirements-dev.txt](../requirements-dev.txt)) |
| Discovery | `tests/test_*.py`, functions named `test_*` ([pytest.ini](../pytest.ini)) |
| Default flags | `-q --tb=short` (`addopts` in [pytest.ini](../pytest.ini)) |
| Size | ~250 tests across 70+ files — one-or-more `test_*.py` per `validsim` module |
| Coverage floor | **90%** (branch coverage), baseline established at ~95% ([pyproject.toml](../pyproject.toml)) |
| Path shim | root [conftest.py](../conftest.py) inserts the repo root into `sys.path`, so `validsim` resolves without an install |

`pytest.ini` keeps discovery minimal — there are no custom markers or
`pytest.mark` registries to learn. Categories are distinguished by **file
naming and structure**, not by marker selection (see §2 and §7).

```bash
make test     # python -m pytest tests/ -v   (full suite, verbose)
make cov      # coverage + 90% floor          (see §6)
make lint     # ruff check validsim tests
```

## 2. Test categories

The suite is layered from cheapest/most-isolated to most-integrated. Every
category below runs in the same `pytest tests/` invocation — the layers are a
*design* taxonomy, not separate CI stages.

| Category | Touches | Example files | Seeded? |
|---|---|---|---|
| **Unit** | one pure function/class, no I/O | `test_stats.py`, `test_benchmark.py`, `test_safety.py`, `test_config.py` | n/a (deterministic inputs) |
| **Integration** | a real collaborator (HTTP app, SQLite file, CLI runner, fake transport) | `test_api.py`, `test_store_sqlite.py`, `test_cli.py`, `test_isaac_worker.py` | yes, via mock backend |
| **Property-based** | invariants over randomly generated inputs | `test_stats_property.py` | yes — fixed master seed |
| **Fuzz** | hostile/malformed input must be rejected or degrade safely | `test_config_fuzz.py`, `test_scenarios_fuzz.py` | yes — fixed master seed |
| **End-to-end** | the whole pipeline / async path wired together | `test_pipeline.py`, `test_e2e_async.py` | yes — pinned to mock |

### 2.1 Unit tests

A unit test calls one function with hand-built inputs and asserts the exact
output — no fixtures, no filesystem, no network. [tests/test_stats.py](../tests/test_stats.py)
is the archetype: it feeds a small fixed sample to `bootstrap_ci` and pins the
ordering and the point estimate.

```python
# tests/test_stats.py
class TestBootstrapCi:
    def test_contains_point_estimate(self) -> None:
        values = _bernoulli(0.7, 400, seed=SEED)        # SEED = 42, fixed
        low, high, point = bootstrap_ci(values, seed=SEED)
        assert point == pytest.approx(statistics.mean(values))
        assert low <= point <= high
```

Note the discipline: even the *test data* is generated from a pinned seed, so
the assertion is stable across runs. [tests/test_benchmark.py](../tests/test_benchmark.py)
follows the same shape for `compare_scorecards`, including the defensive
branches (missing keys, `None`, non-numeric values, frozen dataclasses).

### 2.2 Integration tests

Integration tests exercise a module against a **real collaborator** that is
still local and deterministic:

- **HTTP** — [tests/test_api.py](../tests/test_api.py) builds the FastAPI app
  with `create_app(ValidationStore())` and drives it through
  `fastapi.testclient.TestClient`. A fresh in-memory store per test means no
  shared state and no cleanup.
- **Filesystem** — [tests/test_store_sqlite.py](../tests/test_store_sqlite.py)
  opens a real SQLite database under pytest's `tmp_path` fixture and proves
  round-trips survive a close/reopen.
- **CLI** — [tests/test_cli.py](../tests/test_cli.py) invokes the Typer app via
  `typer.testing.CliRunner`, redirecting the scorecard cache to a temp file
  (autouse `cache_file` fixture) and asserting the CI exit-code contract
  (`0`/`1`/`2`).
- **Wire contract** — [tests/test_isaac_worker.py](../tests/test_isaac_worker.py)
  drives the GPU-worker adapter through `httpx.MockTransport`, so the
  [docs/isaac-worker.md](isaac-worker.md) HTTP contract is proven without a
  GPU, container, or socket.

```python
# tests/test_api.py
@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))

def test_create_returns_scorecard(self, client: TestClient) -> None:
    response = client.post("/api/v1/validations", json=_request_body())
    assert response.status_code == 201
    assert response.json()["episode_count"] == 72   # 60 nominal + 12 adversarial
```

### 2.3 Property-based tests

[tests/test_stats_property.py](../tests/test_stats_property.py) checks
*invariants that must hold for a wide space of inputs* rather than one
hand-picked example. It deliberately **avoids `hypothesis`** (the suite stays
dependency-free) and instead drives a fixed master `random.Random` seed through
~50 randomized iterations per property:

```python
# tests/test_stats_property.py
MASTER_SEED = 0xC0FFEE     # pinned -> the whole sweep is reproducible
ITERATIONS = 50

def test_ci_width_non_decreasing_in_confidence(self) -> None:
    rng = random.Random(MASTER_SEED)
    for _ in range(ITERATIONS):
        values = _bernoulli_sample(rng, rng.randint(10, 80), rng.uniform(0.05, 0.95))
        seed = rng.randrange(1, 10**9)
        widths = [ ... for confidence in CONFIDENCE_LADDER ]
        for narrower, wider in zip(widths, widths[1:]):
            assert narrower <= wider + 1e-12
```

The master seed is what makes this a *test* and not a flaky experiment: each
iteration's per-call `seed` is drawn from the pinned stream, so a green run
reproduces exactly. The properties here complement (never duplicate) the
example-based assertions in `test_stats.py` / `test_stats_edge.py`.

### 2.4 Fuzz tests

Fuzz tests hammer a boundary with **known-bad and hostile input** and assert it
either rejects cleanly or degrades safely. Like the property tests they use a
pinned master seed and no external framework.

- [tests/test_config_fuzz.py](../tests/test_config_fuzz.py) (`MASTER_SEED =
  0x5EED_C0FF`, ~150 iterations) mutates one `TaskConfig` / `ValidationRequest`
  field at a time from curated pools of bad values and asserts a
  `pydantic.ValidationError` every time. It also carries a **positive control**
  (`TestPositiveControl`) proving the harness accepts what Pydantic legitimately
  coerces (`True`, `"1000"`, `1000.0`) — so the "bad" pools stay honest.
- [tests/test_scenarios_fuzz.py](../tests/test_scenarios_fuzz.py) (`MASTER_SEED
  = 0x5CE_0F00`) feeds the LLM scenario generator deliberately hostile payloads
  — non-taxonomy categories, absurd difficulties, prose-wrapped JSON, malformed
  JSON, provider exceptions — and asserts it **never raises** and always returns
  exactly `n` schema-valid scenarios, falling back to the deterministic
  rule-based generator when the model output is unusable.

> [!tip] Fuzz vs. property-based
> Both are seeded random sweeps; the difference is intent. Property tests assert
> *mathematical invariants* hold over valid inputs; fuzz tests assert the system
> *survives invalid/hostile inputs*. Reuse the same idiom — a module-level
> `MASTER_SEED` + `ITERATIONS` constant and a `random.Random(MASTER_SEED)` loop.

### 2.5 End-to-end tests

E2E tests wire the whole system together and run it for real:

- [tests/test_pipeline.py](../tests/test_pipeline.py) drives
  `validsim.engine.pipeline.run_and_score` — the single sequence the API, CLI,
  and job worker all delegate to — with the deterministic `MockIsaacBackend`
  and an in-memory store. No HTTP, no CLI, no monkeypatching of engine
  internals. It pins the guarantees the shared-pipeline refactor must not break:
  a fixed `(checkpoint_id, task)` seed reproduces the composite score,
  adversarial scenarios actually add episodes, and the finished run persists.
- [tests/test_e2e_async.py](../tests/test_e2e_async.py) exercises the *whole*
  async path through **real HTTP handlers**: `POST /api/v1/jobs` → `JobWorker`
  drains the shared queue → `GET /api/v1/validations/{id}` and
  `GET /api/v1/jobs/{id}` confirm the persisted run and terminal `done` state.
  The queue and store are the *same objects* injected onto `app.state` for both
  the app and the worker — the contract that makes the async pipeline coherent.

```python
# tests/test_e2e_async.py — enqueue → worker → fetch, all over HTTP
job_id = _enqueue(client)                                    # 202, status "queued"
assert client.get(f"/api/v1/validations/{job_id}").status_code == 404  # not yet
done = harness.worker().run_once()                           # drain the queue
assert done.status is JobStatus.DONE and done.result == job_id
assert client.get(f"/api/v1/validations/{job_id}").status_code == 200  # now persisted
```

The async e2e suite owns its environment: a `clean_env` fixture clears
`VALIDSIM_API_KEY`, `VALIDSIM_JOB_QUEUE`, `VALIDSIM_REDIS_URL`, and
`VALIDSIM_BACKEND` before each test and restores them after, so no leftover
deployment config (Redis queue, GPU backend, auth) can leak into the happy path.

## 3. Why sim tests are reproducible: the seeded mock backend

The MVP simulation backend is
[`MockIsaacBackend`](../validsim/sim/runner.py) — a fast, seeded stand-in for
the real Isaac Sim worker. Reproducibility rests on three facts:

1. **Per-episode determinism.** `run_episode` builds a private
   `random.Random(seed)`; every observable (success, failure mode, contact
   force, human distance, duration, joint summary) is drawn from that one RNG.
   Same `seed` ⇒ identical `EpisodeResult`.
2. **Unique, derived episode seeds.** `run_validation` gives episode `i` the
   seed `seed + i` (nominal block) and adversarial episodes `seed +
   episodes + j`, so seeds are unique within a run yet fully determined by the
   run seed.
3. **Stable run seed across processes.** The pipeline seeds from
   `stable_seed(checkpoint_id, task_id)`, which is `zlib.crc32(...) &
   0x7FFFFFFF` — *not* Python's `hash()`, which is salted per process. So the
   same `(checkpoint, task)` yields the same seed on every machine and every CI
   run.

```python
# tests/test_runner.py — the contract the whole sim suite leans on
def test_episode_is_deterministic(self) -> None:
    task = _task()
    a = MockIsaacBackend().run_episode(task, seed=SEED, randomization_level="full")
    b = MockIsaacBackend().run_episode(task, seed=SEED, randomization_level="full")
    assert a == b

def test_seed_is_derived_from_checkpoint_and_task(self) -> None:
    assert stable_seed("ckpt-a", "pick-place") != stable_seed("ckpt-b", "pick-place")
```

Because the backend is deterministic, the integration and e2e tests in §2 can
assert **exact** values — `episode_count == 72`, `composite_score` equal across
two calls — instead of tolerating statistical noise. The real GPU backend is a
drop-in swap behind `VALIDSIM_BACKEND=isaac`
([docs/isaac-worker.md](isaac-worker.md)); it is verified only against
`httpx.MockTransport` fakes today, never against a live worker, so CI has no
GPU dependency.

> [!note] Pin the backend in any test that runs a pipeline
> When a test builds an app or worker, inject `MockIsaacBackend()` explicitly
> and/or clear `VALIDSIM_BACKEND` (as `test_e2e_async.py` does). Relying on the
> default leaves the door open for a developer's environment to select the
> `isaac` backend and break determinism.

## 4. Live-integration gate: Postgres (`VALIDSIM_PG_URL`)

Most of the suite is driver-absent-safe. [tests/test_store_postgres.py](../tests/test_store_postgres.py)
tests the Postgres store **without ever importing `psycopg` for real or
contacting a database**: SQL construction is asserted through an injected
`_conn_factory` fake, and the lazy-import / fail-fast paths are exercised by
monkeypatching `_import_psycopg` or blocking the import in a subprocess.

The one class that *does* need a server is gated at the class level:

```python
# tests/test_store_postgres.py
@pytest.mark.skipif(not os.environ.get("VALIDSIM_PG_URL"), reason="no postgres")
class TestPostgresIntegration:
    def test_save_get_round_trip(self) -> None:
        store = PostgresValidationStore(table="validations")
        ...
```

- **No `VALIDSIM_PG_URL` (default, and CI):** the class is skipped — a green
  run needs no local Postgres.
- **`VALIDSIM_PG_URL` set** to a reachable instance (e.g. the
  `docker compose up` Postgres from [docs/runbook.md](runbook.md) §1): the live
  round-trip tests run against it.

```powershell
# PowerShell — opt in to the live Postgres tests
$env:VALIDSIM_PG_URL = "postgresql://validsim:validsim@127.0.0.1:5432/validsim"
python -m pytest tests/test_store_postgres.py -v
```

> [!warning] Skips are expected, not failures
> `pytest` reports these as `skipped`, and CI's JUnit summary counts them
> (see §8). A skip here is correct behaviour on a machine without Postgres —
> do **not** "fix" it by deleting the gate. The PR checklist's "no skips you
> introduced" rule refers to *newly added* skips, not this pre-existing one.

## 5. Optional-dependency gate: reportlab (`importorskip`)

PDF export depends on the optional `reportlab` package. Tests that render a
real PDF use `pytest.importorskip` at module top so the file is skipped — not
errored — when the dependency is missing:

```python
# tests/test_pdf.py
pytest.importorskip("reportlab", reason="PDF export requires the optional reportlab dep")

def test_approve_renders_pdf_bytes(self) -> None:
    data = scorecard_pdf_bytes(_approve_card())
    assert data.startswith(b"%PDF-")
    assert len(data) > 500          # a real document, not a stub
```

The same guard appears in [tests/test_api.py](../tests/test_api.py) just above
`TestScorecardPdfExport`, so the non-PDF API tests always run while the PDF
endpoint tests skip cleanly without reportlab. This mirrors the runtime
behaviour documented in [docs/runbook.md](runbook.md) §4.3: the API returns
**501** with a `"pip install reportlab"` detail when the dependency is absent —
everything else runs fine.

> [!tip] `skipif` vs `importorskip`
> Use `@pytest.mark.skipif(<env/condition>)` when the *capability* is optional
> (a live server, a feature flag). Use `pytest.importorskip("<pkg>")` when the
> *import itself* would raise `ModuleNotFoundError`. Both produce a `skipped`,
> never an error, so a dependency-free checkout still gets a green suite.

## 6. Coverage: the 90% floor

Coverage is configured in [pyproject.toml](../pyproject.toml) under
`[tool.coverage]`:

| Setting | Value | Meaning |
|---|---|---|
| `run.source` | `["validsim"]` | Measure the package only (tests excluded via `omit`) |
| `run.branch` | `true` | **Branch** coverage, not just line coverage |
| `report.fail_under` | `90` | The enforced floor — below this, the run exits non-zero |
| `report.show_missing` | `true` | Print missing line/ranges |

> [!important] Floor 90, baseline ~95
> The suite sits at roughly **95%** line/branch coverage, but the number
> fluctuates 93–95% across runs. The enforced floor is deliberately set at
> **90%** so CI fails on a *real* regression without tripping on normal
> measurement jitter. Treat 90% as a hard gate and ~95% as the number to keep
> near — a PR that drops you toward the floor is a smell even if it "passes."

`exclude_lines` carves out unreachable-but-legitimate code from the denominator:
`pragma: no cover`, `if TYPE_CHECKING:`, `raise NotImplementedError`, and the
`if __name__ == "__main__":` guard.

### Running coverage locally

```bash
make cov
# → python -m pytest tests/ --cov=validsim --cov-report=term-missing --cov-fail-under=90
```

The `--cov-fail-under=90` flag on the `cov` recipe enforces the floor directly
on the command line. CI reaches the same 90% floor through the
`fail_under = 90` value in `pyproject.toml` (coverage.py reads it automatically)
rather than a CLI flag — see §8. To see a per-file breakdown locally:

```bash
python -m pytest tests/ --cov=validsim --cov-report=term-missing
```

## 7. Writing a new test

The rule from [CONTRIBUTING.md](../CONTRIBUTING.md) §4: **tests mirror module
structure.** Every `validsim` module has at least one matching test file, and
new modules must too.

| You changed… | Add/extend… |
|---|---|
| `validsim/engine/stats.py` | `tests/test_stats.py` (unit) + `tests/test_stats_property.py` (invariants) |
| `validsim/config.py` | `tests/test_config.py` (unit) + `tests/test_config_fuzz.py` (rejection) |
| `validsim/store/<backend>.py` | `tests/test_store_<backend>.py` (integration; gate live deps per §4/§5) |
| `validsim/api/…` | `tests/test_api*.py` (integration via `TestClient`) |
| `validsim/cli.py` | `tests/test_cli*.py` (integration via `CliRunner`) |

A focused extra file is fine when a module needs many angles — the API and
store areas already split into `test_api_auth.py`, `test_api_rate_limit.py`,
`test_store_concurrency.py`, etc. Keep the base name tied to the module.

Checklist for a new test file:

1. **Mirror the module path**: `validsim/engine/scorecard.py` →
   `tests/test_scorecard.py`.
2. Start with `from __future__ import annotations` (matches the package style).
3. Build inputs with small local helpers (`_scorecard()`, `_task()`,
   `_request_body()`) so each test reads as one assertion.
4. Prefer **in-memory / `tmp_path`** collaborators (a fresh `ValidationStore()`,
   a SQLite file under `tmp_path`) over shared global state.
5. Any test that runs a pipeline **pins the mock backend** (§3).
6. Cover the **edge/branch** cases, not just the happy path — the scorecard
   cares about empty inputs, `None`, out-of-range, and malformed types.
7. If it needs an optional dep or live server, **gate it** (§4, §5) so a bare
   checkout stays green.
8. If you add randomized coverage, pin a `MASTER_SEED` (§2.3).

```python
# tests/test_<module>.py — skeleton
"""Tests for <one-line description of the module>."""

from __future__ import annotations

import pytest

from validsim.<package>.<module> import <Thing>


def _make_input(**overrides):
    base = {...}
    base.update(overrides)
    return base


class Test<Behaviour>:
    def test_<expectation>(self) -> None:
        assert <Thing>(...) == ...
```

Before opening the PR, run both commands CI runs (CONTRIBUTING §2):

```bash
python -m pytest tests/
ruff check validsim tests
```

## 8. CI wiring

Two workflows run this suite; both are the same `pytest` you run locally.

### 8.1 `ci.yml` — every push / PR to `main`

[.github/workflows/ci.yml](../.github/workflows/ci.yml) has two jobs:

1. **`test`** (Python 3.12): `ruff check` → `pytest` under coverage → artifacts
   + step summary.
2. **`docker`**: builds the image, `needs: test` — a failing suite can never
   produce an image (the same gate-the-deploy discipline ValidSim sells).

The test step runs pytest with coverage **and** JUnit in one pass:

```yaml
- name: Run tests (pytest with coverage)
  run: python -m pytest tests/ \
       --cov=validsim --cov-report=term-missing --cov-report=xml \
       --junitxml=pytest-results.xml
```

| Output | Consumed by |
|---|---|
| `coverage.xml` (`--cov-report=xml`) | uploaded as the `coverage-xml` artifact (14-day retention); the 90% floor is enforced via `fail_under` in `pyproject.toml` |
| `pytest-results.xml` (`--junitxml`) | parsed into the **job step summary** (tests/failures/errors/skipped/time) and uploaded as the `pytest-results` artifact |

> [!note] Skips show up in the summary, by design
> The step-summary script prints the `skipped` count from the JUnit report. The
> Postgres (§4) and reportlab (§5) gates therefore appear as skips on the
> hosted runner — expected, not a regression.

### 8.2 `nightly.yml` — the deep sweep

[.github/workflows/nightly.yml](../.github/workflows/nightly.yml) applies
ValidSim's own 24/7 continuous-validation story to itself: every night at
**03:00 UTC** (and on `workflow_dispatch`) the **full suite** runs on a clean
checkout with `timeout-minutes: 60`:

```yaml
- name: Run full test suite (pytest)
  run: python -m pytest tests/ -v --junitxml=nightly-pytest-results.xml
```

Unlike `ci.yml` this is verbose (`-v`, no coverage gate) and archives the JUnit
report for **30 days** (`nightly-pytest-<run_number>`), so flaky tests,
dependency drift, or time-of-day effects that slipped past per-PR CI surface
before anything is demoed.

> [!warning] Nightly is not a deploy gate
> `ci.yml` is the merge gate (lint + coverage + JUnit + Docker build).
> `nightly.yml` is a *sweep* that archives evidence — it does not block a PR.
> The separate nightly **adversarial validation** sweep (the product feature,
> not the test suite) is documented in [docs/github-actions.md](github-actions.md) §7.2.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: validsim` under pytest | Run from the repo root so [conftest.py](../conftest.py) adds it to `sys.path` (`python -m pytest tests/`), or `pip install -e .`. |
| `TestPostgresIntegration` failing (not skipping) | `VALIDSIM_PG_URL` is set but unreachable — unset it, or start Postgres via `docker compose up` ([runbook](runbook.md) §1). |
| PDF tests erroring with `ModuleNotFoundError: reportlab` | You removed/bypassed the `importorskip` guard; restore it (§5). Install reportlab only if you intend to exercise PDF export. |
| `Coverage failure: total of X is less than fail-under=90` | A real regression — add tests for the new branches; don't lower the floor. |
| A "randomized" test passes locally, fails on re-run | You forgot to pin a `MASTER_SEED` (§2.3) or inject the mock backend (§3). |
| Nightly passes but PR CI fails (or vice-versa) | PR CI enforces coverage + ruff; nightly does not. Run `make cov` and `make lint` locally. |

Links: [[GitHub Actions Integration]] · [[Isaac Sim GPU Worker — HTTP Contract]] ·
[docs/runbook.md](runbook.md) · [docs/isaac-worker.md](isaac-worker.md) ·
[CONTRIBUTING.md](../CONTRIBUTING.md)
