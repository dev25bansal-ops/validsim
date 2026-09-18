# ValidSim — Build & CI/CD Infrastructure

How **ValidSim validates itself**: the same continuous-validation discipline we
sell to robot-fleet teams, applied to our own codebase.

## Pipeline flow

```
local dev (make)          GitHub Actions CI (push/PR → main)        Nightly (03:00 UTC)
─────────────────         ──────────────────────────────────        ─────────────────────
make install / lint       ruff check validsim tests                 full pytest suite
make test / cli-run       pytest + JUnit XML → summary + artifact   30-day retained results
make docker-build / up    docker build (validsim:ci, push=false)    catches flakes &
                              ▲                                     dependency drift
                              └── needs: test — red suite ⇒ no image, ever
```

## Components

| File | Role |
|---|---|
| `Dockerfile` | Prod image: `python:3.12-slim`, non-root `USER` (uid 1001), requirements-first layer caching, `HEALTHCHECK` on `/api/v1/health`. |
| `.dockerignore` | Keeps `tests/`, `vault/`, docs, and CI tooling out of the runtime image. |
| `docker-compose.yml` | Local MVP stack: `api` + `redis:7` (future job queue) + `postgres:16` (runs/scorecards), all healthchecked. GPU Isaac Sim worker is a commented placeholder — no GPU host yet. |
| `.github/workflows/ci.yml` | Lint → test → docker build; the build job is gated on tests passing. |
| `.github/workflows/nightly.yml` | 24/7 story: full suite every night, artifacts archived. |
| `Makefile` (repo root) | One-command parity with CI: `make lint test run cli-run docker-build docker-up clean`. |

## MVP sprint fit — Week 1 deliverable

Week 1 of the 8-week plan is **"repo + Docker + CI"**, and it is all here:
`make docker-up` boots the API with Postgres/Redis healthchecks; CI proves every
commit; nightly proves the suite stays green while Week 2 (validation-run API +
Redis job queue + GPU worker) is built on top of this foundation.
