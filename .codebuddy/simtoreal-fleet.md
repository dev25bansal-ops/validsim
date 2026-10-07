# ValidSim — 24/7 Parallel Agent Fleet

Workspace: `d:/SIM-TO-REAL`
Session/team dir: `d:/SIM-TO-REAL/.codebuddy/teams/d2653e6a9c474de7a74ef78b37343e1b`

## Topology

One lead (this session) + **9 async members**. Each member owns a **disjoint** file set — no two members may edit the same file.

| # | Member | Subagent type | Exclusive ownership |
|---|---|---|---|
| 1 | `engine-audit` | code-explorer | `validsim/engine/*`, `validsim/__init__.py` |
| 2 | `store-config` | code-reviewer | `validsim/store/*`, `validsim/config.py`, `validsim/config_loader.py`, `validsim/project_config.py`, `validsim/logging.py` |
| 3 | `api-surface` | security-auditor | `validsim/api/*` |
| 4 | `jobs-queue` | python-expert | `validsim/jobs/*`, `validsim/cli.py` |
| 5 | `sim-runtime` | python-expert | `validsim/sim/*`, `validsim/scenarios/*` |
| 6 | `notify-sec` | security-auditor | `validsim/notify/*`, `validsim/web/*` |
| 7 | `tests-guard` | test-automator | `tests/**`, `pytest.ini`, `conftest.py`, `pytest_symlink_free_tmp.py` |
| 8 | `site-astro` | frontend-developer | `site/**` |
| 9 | `docs-truth` | document-writer | `docs/**`, `audit/**`, `vault/**`, root `*.md` |
| 10 | `infra-ci` | deployment-engineer | `.github/**`, `infra/**`, `examples/**`, `scripts/**`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt` |

## Folder groups

| Group | Folder | Members |
|---|---|---|
| A | `validsim/engine/` | `engine-audit` |
| B | `validsim/store/` + config | `store-config` |
| C | `validsim/api/` | `api-surface` |
| D | `validsim/jobs/` + `cli.py` | `jobs-queue` |
| E | `validsim/sim/` + `scenarios/` | `sim-runtime` |
| F | `validsim/notify/` + `web/` | `notify-sec` |
| G | `tests/` | `tests-guard` |
| H | `site/` | `site-astro` |
| I | `docs/` + `audit/` + `vault/` | `docs-truth` |
| J | CI / infra / packaging | `infra-ci` |

## Global rules given to every member

1. **Prove before fixing.** One defect per iteration, with a minimal reproduction. "No defect found" is a valid result — no speculative refactors.
2. **Read before edit.** `read_file` first, then `replace_in_file`. Never rewrite a large file wholesale.
3. **Never weaken a test.** No editing, deleting, skipping, xfail-ing or loosening an existing test to get green. Adding new tests is fine.
4. **Never touch git.** No commit, push, or branch operations.
5. **Report, don't trespass.** A bug outside your scope goes in your final report, never in your editor.
6. **Honest numbers only.** If you did not measure it, do not state it. This applies hard to the test count — `README.md` records that the figure has contradicted itself six times.

## Machine constraint (binding on all members)

Only **~3-4GB free RAM**, with 10 agents running concurrently.

- Always `-p no:cacheprovider` on pytest.
- **Never** run the full suite in parallel; use targeted `-k` subsets.
- Max 2 `pytest-xdist` workers (`-n 2`), or none.
- Never `npm install` in `site/`; never start a dev server.
- If a command approaches the memory ceiling, **stop and report** rather than pushing through.

## Engine-member method

Each member: baseline → read owned code → find defects → pick highest-value provable one → minimal fix → re-run targeted subset → report (defect, repro evidence, fix with file+line citation, before/after results, what was deliberately not done).

## 24/7 loop

An hourly automation re-runs this orchestration: it re-reads each member's report, promotes unresolved findings into the next iteration's task list, and re-spawns members whose scope is idle. Continuity lives in `.codebuddy/automations/validsim-24-7-continuous-build/memory.md`.

## Known overlapping-ownership resolution

The initial briefs double-granted `validsim/store/*` and `validsim/config*.py` to both `engine-audit` and `store-config`. Resolved in favour of `store-config`; `engine-audit` was corrected mid-flight to `validsim/engine/*` + `validsim/__init__.py` only. It was asked to report any edits already made to those files so the conflict could be reconciled.