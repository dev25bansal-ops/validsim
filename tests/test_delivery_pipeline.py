"""Invariants of the delivery pipeline: a green check must mean the work happened.

Items 24-29 of docs/ISSUE_CATALOG.md share one root cause — nothing in the test
suite ever looked at `.github/`, `actions/`, `docker-compose.yml` or
`scripts/build.ps1`, so a workflow could report success while doing nothing.
These tests hold the specific properties that were missing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import validsim
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
_ACTION_PIN_RE = re.compile(r"^[0-9a-f]{40}$")


def _md_section(name: str) -> str:
    """One `## name` section of SECURITY.md, up to the next `##` heading."""
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    tail = text.split(f"## {name}", 1)[1]
    return tail.split("\n## ", 1)[0]


def _yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict[str, Any]]:
    doc = _yaml(REPO_ROOT / ".github" / "workflows" / "release.yml")
    return doc["jobs"][job]["steps"]


def _step_running(fragment: str, *, job: str = "package-check") -> dict[str, Any]:
    """The step whose shell command contains ``fragment`` — raises if absent."""
    return _step(job, "run", fragment)


def _step_using(action: str, *, job: str) -> dict[str, Any]:
    """The step that references ``action`` in its `uses:` line."""
    return _step(job, "uses", action)


def _step(job: str, field: str, fragment: str) -> dict[str, Any]:
    for step in _steps(job):
        if fragment in str(step.get(field, "")):
            return step
    raise AssertionError(f"release.yml job {job!r} has no step with {fragment!r} in {field!r}")


def _shell_script(step_id: str, *, action: str = "scorecard") -> str:
    doc = _yaml(REPO_ROOT / "actions" / action / "action.yml")
    for step in doc["runs"]["steps"]:
        if step.get("id") == step_id:
            return str(step.get("run", ""))
    raise AssertionError(f"actions/{action}/action.yml has no step id {step_id!r}")


class TestReleaseJobDoesRealWork:
    """Item 26: every packaging step used to be gated on a secret it does not need."""

    @pytest.mark.parametrize("command", ["python -m build", "twine check dist/*"])
    def test_packaging_steps_are_unconditional(self, command: str) -> None:
        step = _step_running(command)
        # `build` and `twine check` take no credentials, so gating them on
        # PYPI_API_TOKEN let the job pass having validated nothing.
        assert "if" not in step, f"{command!r} is skipped when the token is absent"

    def test_workflow_never_uploads_to_pypi(self) -> None:
        text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
        assert "twine upload" not in text

    def test_image_tag_is_not_double_prefixed(self) -> None:
        """`github.ref_name` already carries the `v` of a `v1.2.3` tag."""
        tags = str(_step_using("docker/build-push-action", job="docker")["with"]["tags"])
        rendered = re.sub(r"\$\{\{\s*github\.ref_name\s*\}\}", "v1.2.3", tags)
        assert "vv" not in rendered, rendered


class TestScorecardActionFailsClosed:
    """Item 27: a missing scorecard was a `::warning::` on an exiting-0 step."""

    def test_missing_scorecard_is_an_error_not_a_warning(self) -> None:
        script = _shell_script("render")
        assert "::error::" in script
        assert "::warning::" not in script

    def test_render_step_exits_non_zero_on_failure(self) -> None:
        script = _shell_script("render")
        guard = script.split("else", 1)
        assert len(guard) == 2, "the failure branch disappeared from the render step"
        assert re.search(r"\bexit 1\b", guard[1]), "failure branch must abort the check"

    def test_validate_action_configures_a_durable_store(self) -> None:
        """`gate` refuses to decide from the in-memory store, so the action must set one."""
        script = _shell_script("python", action="validate")
        assert "VALIDSIM_STORE=sqlite" in script
        assert "VALIDSIM_SQLITE_PATH=" in script


class TestCoverageFloorIsTruthful:
    """Item 25/20: the 90% claim must be enforced, and stated identically everywhere."""

    def test_a_floor_is_declared(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            doc = tomllib.load(fh)
        assert doc["tool"]["coverage"]["report"]["fail_under"] == pytest.approx(90)

    def test_ci_enforces_the_same_floor(self) -> None:
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")
        run = next(s for s in ci.splitlines() if "pytest" in s and "--cov" in s)
        assert "--cov-fail-under=90" in run

    def test_makefile_matches_ci(self) -> None:
        make = (REPO_ROOT / "Makefile").read_text("utf-8")
        assert "--cov-fail-under=90" in make

    def test_readme_claim_matches_the_config(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text("utf-8")
        assert "below 90%" in readme or "fails below 90" in readme


class TestExposedSurfaceAndDeps:
    """Items 28, 24, 04 and the class they belong to: declared, loopback, dev-installed."""

    def test_api_publishes_on_loopback_only(self) -> None:
        doc = _yaml(REPO_ROOT / "docker-compose.yml")
        ports = doc["services"]["api"]["ports"]
        assert all(str(p).startswith("127.0.0.1:") for p in ports), ports

    def test_build_script_installs_dev_requirements(self) -> None:
        script = (REPO_ROOT / "scripts" / "build.ps1").read_text("utf-8")
        assert "requirements-dev.txt" in script

    def test_runtime_and_test_dependencies_are_declared(self) -> None:
        runtime = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        dev = (REPO_ROOT / "requirements-dev.txt").read_text("utf-8").lower()
        assert "redis" in runtime  # validsim/jobs/queue.py imports it on a live path
        assert "pyyaml" in dev  # this module parses the workflow files


class TestAdvertisedConfigurationIsHonest:
    """A declared env var with no reader is a promise the code does not keep.

    ``.env.example`` is the file an operator copies to ``.env`` to discover what
    the product can be configured with. A variable listed there — especially one
    whose whole purpose is a documented behaviour, like ``VALIDSIM_CONFIG`` for
    "path to an explicit config file" — is an implicit promise that setting it
    does something. When nothing reads it, the operator gets silent no-op
    behaviour and, worse, a false model of how the system is configured.

    This is the regression gate: any variable in ``.env.example`` that no
    production module reads fails here, so the file and the code cannot drift
    apart again. Removing the variable and saying so in the file is the fix;
    adding the missing reader is the other one.
    """

    _ENV_DECL_RE = re.compile(r"^(?P<name>VALIDSIM_[A-Z0-9_]+)=", re.M)

    @staticmethod
    def _declared_env_vars() -> set[str]:
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        return {
            m.group("name")
            for m in TestAdvertisedConfigurationIsHonest._ENV_DECL_RE.finditer(text)
        }

    @staticmethod
    def _env_literals_read_in_production() -> set[str]:
        """Every ``VALIDSIM_*`` string literal appearing anywhere in ``validsim/``."""
        found: set[str] = set()
        for path in sorted((REPO_ROOT / "validsim").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found |= set(re.findall(r"VALIDSIM_[A-Z0-9_]+", path.read_text(encoding="utf-8")))
        return found

    def test_the_scan_actually_finds_variables(self) -> None:
        """Guard the scanner itself: a broken regex must not make this vacuous."""
        declared = self._declared_env_vars()
        read = self._env_literals_read_in_production()
        assert len(declared) > 20, f"only {len(declared)} vars parsed from .env.example"
        assert len(read) > 20, f"only {len(read)} VALIDSIM_* literals found in validsim/"

    def test_every_advertised_env_var_has_a_reader(self) -> None:
        advertised = self._declared_env_vars()
        read = self._env_literals_read_in_production()
        inert = sorted(advertised - read)
        assert not inert, (
            f"these variables are advertised in .env.example but no module in "
            f"validsim/ reads them, so setting them is a silent no-op: {inert}"
        )

    def test_config_file_support_is_not_advertised_as_shipped(self) -> None:
        """``VALIDSIM_CONFIG`` must not imply a working config-file loader.

        The two config modules (``config_loader``, ``project_config``) are
        parsed and redacted but never called on the shipped execution path.
        Advertising a knob for them would hand an operator the belief that a
        config file changes behaviour when it does not. Either the file loads
        it, or the file stops claiming it.
        """
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "VALIDSIM_CONFIG=" not in text, (
            ".env.example advertises VALIDSIM_CONFIG, but no shipped code path "
            "reads a config file — the operator would set it and see no effect"
        )

    def test_asset_root_knob_is_documented_where_it_is_honoured(self) -> None:
        """``VALIDSIM_ASSET_ROOT`` *is* read (``config.py``), so it may stay —
        but the comment must not imply a loader that does not exist."""
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "VALIDSIM_ASSET_ROOT=" in text
        # The escape protection is real and enforced at validation time.
        assert "traversal" in text or "escap" in text or "rejected" in text


class TestExternalActionsArePinned:
    """Item 32: mutable action tags must not choose code executed with CI credentials."""

    def test_every_third_party_uses_reference_is_a_full_commit_sha(self) -> None:
        """Reject mutable refs while allowing repository-local composite actions.

        ``examples/`` is included because a copy-paste example is the single
        most-copied file in a template repo: an unpinned ``@v1`` there does not
        just fail this gate, it ships to every consumer who copies the example.
        All 5 refs in ``examples/`` were pinned in an earlier round, but nothing
        enforced it, so the next unpinned ref would land silently.
        """
        candidates = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        candidates.extend(sorted((REPO_ROOT / "actions").glob("*/action.yml")))
        candidates.extend(sorted((REPO_ROOT / "examples").glob("*.yml")))
        assert candidates, "no workflow or action YAML files found"

        unpinned: list[str] = []
        for path in candidates:
            for step in _action_steps(_yaml(path)):
                reference = str(step.get("uses", ""))
                if not reference or reference.startswith("./"):
                    continue
                if not _ACTION_PIN_RE.fullmatch(reference.rsplit("@", 1)[-1]):
                    unpinned.append(f"{path.relative_to(REPO_ROOT)}: {reference}")

        assert not unpinned, (
            "third-party actions must be pinned to a 40-character SHA:\n" + "\n".join(unpinned)
        )


def _action_steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every step mapping from a workflow or composite-action document."""
    if "runs" in document:
        return list(document["runs"].get("steps", []))
    return [step for job in document.get("jobs", {}).values() for step in job.get("steps", [])]


class TestSecurityPolicyReachesSomeone:
    """Item 29: unfilled placeholders made the documented intake channel dead."""

    def test_no_unfilled_placeholders(self) -> None:
        text = (REPO_ROOT / "SECURITY.md").read_text("utf-8")
        offenders = re.findall(r"[\w.-]+@[\w.-]+\.example|TO-BE-PUBLISHED", text)
        assert not offenders, offenders

    def test_a_working_intake_channel_is_documented(self) -> None:
        text = (REPO_ROOT / "SECURITY.md").read_text("utf-8")
        assert "Report a vulnerability" in text

    def test_unsafe_defaults_are_in_scope(self) -> None:
        flat = re.sub(r"\s+", " ", _md_section("3. Scope"))
        assert "the operator's configuration, not a ValidSim vulnerability" not in flat

    def test_supported_versions_table_covers_the_shipped_version(self) -> None:
        rows = set(re.findall(r"\b(\d+\.\d+)\.x\b", _md_section("4. Supported versions")))
        major, minor = validsim.__version__.split(".")[:2]
        allowed = {f"{major}.{minor}", f"{major}.{int(minor) - 1}"}
        assert rows, "no version rows found — the extraction is wrong, not the policy"
        assert rows <= allowed, (
            f"SECURITY.md advertises support for {sorted(rows)} but the package is "
            f"{validsim.__version__!r}"
        )
