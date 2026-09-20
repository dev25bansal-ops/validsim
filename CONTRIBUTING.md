# Contributing to ValidSim

Thanks for your interest in contributing to **ValidSim** — Sim-to-Real CI/CD validation for robot foundation models. This guide covers everything you need to get a development environment running and to submit changes that match the project's conventions.

- **Python**: 3.10+ (required, see `pyproject.toml`)
- **Tooling**: pytest (testing), ruff (linting), GNU Make (optional convenience)
- **CI parity**: every command below is the same one run in `.github/workflows/ci.yml`

---

## 1. Development Setup

### Option A — make (recommended)

```bash
make install
```

This upgrades pip and installs both runtime and dev dependencies:

- `requirements.txt` — runtime deps (fastapi, uvicorn, pydantic, typer, httpx, psycopg, reportlab)
- `requirements-dev.txt` — dev/test tooling (pytest, ruff)

> **Windows note**: `make` requires GNU Make. On Windows use WSL or Git-Bash, or run the equivalent pip commands below (they work in PowerShell as-is).

### Option B — plain pip

```bash
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

### Optional: editable install

```bash
pip install -e .
validsim --help   # console script entry point
```

### Running the API / CLI locally

```bash
# FastAPI app with autoreload (dashboard at http://127.0.0.1:8000/)
uvicorn validsim.api.main:app --host 0.0.0.0 --port 8000 --reload

# Quick 100-episode validation sweep via the Typer CLI
python -m validsim.cli run --episodes 100
```

### Full local stack (Docker)

```bash
docker compose up --build -d   # api + redis + postgres
docker compose down            # add ARGS=-v to drop volumes
```

---

## 2. Running Tests

The full suite mirrors CI exactly:

```bash
python -m pytest tests/ -v
# or
make test
```

Configuration lives in `pytest.ini`:

- test discovery: `tests/test_*.py`, functions named `test_*`
- default addopts: `-q --tb=short`

Notes:

- `conftest.py` at the repo root inserts the repo root into `sys.path` so `validsim` resolves under pytest — no install is strictly required for tests.
- The suite covers every module (`tests/` has one `test_*.py` per `validsim` module, ~250 tests).
- Postgres store tests (`test_store_postgres.py`) skip automatically unless the `VALIDSIM_PG_URL` environment variable is set (`@pytest.mark.skipif(..., reason="no postgres")`) — no local Postgres needed for a green run.

### Manual verification before every PR

Run **both** of these; CI runs them too:

```bash
python -m pytest tests/
ruff check validsim tests
```

---

## 3. Linting

```bash
ruff check validsim tests
# or
make lint
```

Config (`pyproject.toml`):

- `line-length = 100`
- `target-version = "py310"`

Keep your code passing ruff with zero warnings. There is no separate formatter configured — match the existing style of surrounding code.

---

## 4. Code Style

The codebase follows a consistent, strict style. New code must match it.

### Type hints — everywhere

Every module starts with `from __future__ import annotations` and all public functions/dataclasses/Pydantic models are fully type-annotated. Use modern built-in generics (`list[EpisodeResult]`, `Sequence[...]`) and `Optional[...]` where nullable.

### Docstrings

Every module has a top-level docstring explaining its role (often including the key math or contract). Public classes and functions have docstrings; dataclass/Pydantic classes document their fields via an `Attributes:` section:

```python
class RobotSpec(BaseModel):
    """Description of the robot under test.

    Attributes:
        name: Human-readable robot identifier (e.g. ``"franka_panda"``).
        urdf_path: Optional filesystem path to the robot's URDF description.
        dof: Degrees of freedom. Defaults to 7 (typical manipulator arm).
    """
```

Module-level constants that encode domain knowledge get a `#:` doc comment:

```python
#: Penalty per significant regression in the regression component.
_REGRESSION_PENALTY = 25.0
```

### Frozen / immutable data models

Configuration and result objects must be immutable:

- Pydantic v2 models use `model_config = ConfigDict(frozen=True)` (see `validsim/config.py`)
- Engine results use `@dataclass(frozen=True)` (see `validsim/engine/`)

Never mutate a `TaskConfig`, `EpisodeResult`, `EvaluationResult`, etc. Build a new instance instead.

### `__all__` discipline

Each package module declares its public API explicitly with `__all__ = [...]` (see `validsim/__init__.py`, `validsim/engine/scorecard.py`). Keep it accurate: every public name should appear, private names (`_prefix`) should not.

### Other conventions

- Prefer `Literal` types for closed sets (e.g. `RandomizationLevel = Literal["none", "partial", "full"]`)
- Validate inputs at the boundary (API/CLI) via strict Pydantic fields (`Field(..., min_length=1, ge=1, le=40)`) so bad config fails early
- Tests mirror module structure: one `tests/test_<module>.py` per `validsim` module, with branch-coverage tests for edge cases

---

## 5. Commit Conventions

History uses a lightweight conventional style, prefixed `chore:`, `feat:`, `fix:`, or `docs:`, followed by a summary of the iteration:

```
chore: iteration 4 - packaging + CLI --latest, Isaac worker adapter, PDF scorecard, model registry endpoints (250 tests)
chore: iteration 3 - dashboard v0, LLM scenario generator, Postgres store, git baseline
chore: initial ValidSim repository - MVP platform (190 tests), Obsidian vault, CI/CD pipeline, dashboard v0
```

Rules of thumb:

- `<type>: <short summary>` — keep the subject under ~72 characters when practical
- Types in use: `feat` (new functionality), `fix` (bug fixes), `chore` (build/packaging/iteration batches), `docs` (documentation only)
- Mention updated test counts in parentheses when you add tests
- Batch related work into one descriptive commit per iteration, as the history does

---

## 6. Pull Request Checklist

Before opening a PR, verify all of the following:

- [ ] `python -m pytest tests/` passes locally (all tests, no skips you introduced)
- [ ] `ruff check validsim tests` passes with zero warnings
- [ ] New code has type hints, docstrings, `__all__`, and frozen models where applicable
- [ ] New modules have a matching `tests/test_<module>.py`
- [ ] Dependencies changed? Update **both** `requirements*.txt` and `pyproject.toml` (they are kept in sync)
- [ ] Public API changes are reflected in `docs/` (e.g. `docs/github-actions.md`, `docs/isaac-worker.md`) and, where relevant, the engineering notes in `vault/04 - Engineering/`
- [ ] Commit messages follow the `type: summary` convention above
- [ ] PR description states what changed, why, and how it was verified

CI (`.github/workflows/ci.yml`) runs lint + tests + Docker build on every push/PR to `main`; a nightly deep validation suite runs at 03:00 UTC.

---

## 7. Project Architecture (one paragraph)

ValidSim is a cloud-native validation platform that sits between model training and real-world robot deployment: a checkpoint plus task config is submitted through a FastAPI API (`validsim/api/`) or Typer CLI (`validsim/cli.py`), validated against strict Pydantic config models (`validsim/config.py`), executed as massively parallel simulation episodes with domain randomization and LLM-generated adversarial scenarios (`validsim/sim/`, `validsim/scenarios/`), scored by the evaluation/safety/regression/scorecard engines (`validsim/engine/`) into a composite deploy gate (APPROVE/BLOCK), persisted via a store backend (`validsim/store/`, SQLite/Postgres/in-memory), and reported through a built-in dashboard, PDF scorecards, webhooks, and GitHub Actions plugins (`actions/`, `docs/github-actions.md`). The simulation backend is a mock in the MVP with an Isaac worker HTTP adapter behind `VALIDSIM_BACKEND=isaac` (`validsim/sim/isaac_worker.py`, contract in `docs/isaac-worker.md`).

**Deeper reading:**

- `docs/` — integration guides (GitHub Actions plugin, Isaac worker contract)
- `vault/04 - Engineering/` — the full engineering blueprint as Obsidian notes: `Solution Architecture.md`, `Module Specs.md`, `Data Flow.md`, `API Design.md`, `CLI Design.md`, `Tech Stack.md`, `Security Hardening.md`
- `README.md` — repository layout, product pipeline, and milestone status

---

## 8. Where Things Live

| Path | Purpose |
|---|---|
| `validsim/` | Platform package: `config.py`, `cli.py`, `api/`, `sim/`, `scenarios/`, `engine/`, `store/`, `notify/` |
| `tests/` | pytest suite, one file per module |
| `actions/` | GitHub Actions plugin (`validate`, `scorecard`) |
| `examples/` | Example workflow (`robot-validation.yml`) |
| `docs/` | Integration documentation |
| `scripts/build.ps1` | Continuous build entrypoint (venv bootstrap → deps → tests → vault status) |
| `vault/04 - Engineering/` | Engineering design notes |
| `builds/` | Build artifacts (gitignored) |

## 9. Getting Help

- Open an issue with a minimal reproduction and the output of `python -m pytest tests/ -q`
- For design context, check the relevant note in `vault/04 - Engineering/` first — most decisions are documented there
