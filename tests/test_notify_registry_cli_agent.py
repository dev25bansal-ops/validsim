"""Tests for the webhook registry and its CLI (``validsim webhook ...``).

Two things are being protected here, and they pull in opposite directions:

1. **An operator must be able to manage destinations.** Adding, removing and
   listing hooks has to work end to end from the command line.

2. **A hook's secret must not leak.** The secret is a bare credential, so it
   is written with owner-only permissions, never printed by ``list`` or
   ``show``, and never rendered into an error message.

The second is the reason several tests below assert on *absence*. A test
that only checks the happy path would pass even if the secret were being
echoed to stdout.

The registry also has to fail *loudly* on a corrupt file rather than
silently starting from empty: an operator who sees "no webhooks registered"
when the file is corrupt would conclude their alerts are unconfigured and
disable them.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim.cli import app
from validsim.notify.registry import (
    ENV_REGISTRY_FILE,
    HookRecord,
    HookRegistry,
    HookRegistryError,
    HookValidationError,
    registry_path_from_env,
)

runner = CliRunner()

SECRET = "super-secret-shared-key"


@pytest.fixture()
def registry_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temp registry file for every test."""
    path = tmp_path / "hooks.json"
    monkeypatch.setenv(ENV_REGISTRY_FILE, str(path))
    return path


# =====================================================================
# 1. The registry data model
# =====================================================================
class TestHookRecord:
    def test_to_dict_includes_the_secret_when_asked(self) -> None:
        record = HookRecord(name="a", url="https://x.example.com", secret="s")
        assert record.to_dict()["secret"] == "s"

    def test_to_dict_never_leaks_the_secret_when_not_asked(self) -> None:
        record = HookRecord(name="a", url="https://x.example.com", secret=SECRET)
        payload = record.to_dict(include_secret=False)
        assert "secret" not in payload
        assert payload["secret_set"] is True
        assert SECRET not in json.dumps(payload)

    def test_repr_does_not_leak_the_secret(self) -> None:
        """A traceback or a debug log would otherwise print the credential."""
        record = HookRecord(name="a", url="https://x.example.com", secret=SECRET)
        assert SECRET not in repr(record)

    def test_from_dict_rejects_a_missing_name(self) -> None:
        with pytest.raises(HookValidationError):
            HookRecord.from_dict({"url": "https://x.example.com"})

    def test_from_dict_rejects_a_missing_url(self) -> None:
        with pytest.raises(HookValidationError):
            HookRecord.from_dict({"name": "a"})

    def test_from_dict_rejects_an_unknown_format(self) -> None:
        with pytest.raises(HookValidationError):
            HookRecord.from_dict(
                {"name": "a", "url": "https://x.example.com", "format": "xml"}
            )

    def test_from_dict_accepts_the_engines_warning_spelling(self) -> None:
        """The regression engine says "warning"; the dispatcher says "warn"."""
        record = HookRecord.from_dict(
            {"name": "a", "url": "https://x.example.com", "min_severity": "warning"}
        )
        assert record.min_severity == "warn"


# =====================================================================
# 2. Validation
# =====================================================================
class TestHookValidation:
    def test_a_relative_url_is_rejected(self) -> None:
        """A relative URL would be dispatched against the wrong host."""
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("a", "/relative/path")

    def test_a_non_http_scheme_is_rejected(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("a", "file:///etc/passwd")

    def test_an_empty_name_is_rejected(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("   ", "https://x.example.com")

    def test_a_name_with_whitespace_is_rejected(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("my hook", "https://x.example.com")

    def test_a_name_starting_with_a_dash_is_rejected(self) -> None:
        """Otherwise the name would be read as a CLI flag."""
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("--force", "https://x.example.com")

    def test_an_over_long_name_is_rejected(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.add("x" * 65, "https://x.example.com")

    def test_a_duplicate_name_is_rejected_by_default(self) -> None:
        """Silently replacing a destination would move a team's alerts."""
        registry = HookRegistry(Path("/tmp/unused.json"))
        registry.add("a", "https://one.example.com")
        with pytest.raises(HookValidationError):
            registry.add("a", "https://two.example.com")

    def test_a_duplicate_name_can_be_replaced_explicitly(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        registry.add("a", "https://one.example.com")
        registry.add("a", "https://two.example.com", replace_existing=True)
        assert len(registry) == 1
        assert registry.get("a").url == "https://two.example.com"

    def test_removing_an_unknown_hook_is_an_error(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.remove("ghost")

    def test_an_update_only_changes_the_supplied_fields(self) -> None:
        """A partial update must not reset the format or severity."""
        registry = HookRegistry(Path("/tmp/unused.json"))
        registry.add(
            "a", "https://x.example.com", format="slack", min_severity="warn"
        )
        updated = registry.update("a", url="https://y.example.com")
        assert updated.format == "slack"
        assert updated.min_severity == "warn"
        assert updated.url == "https://y.example.com"

    def test_an_update_can_clear_the_secret(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        registry.add("a", "https://x.example.com", secret=SECRET)
        assert registry.update("a", clear_secret=True).secret is None

    def test_an_update_of_an_unknown_hook_is_an_error(self) -> None:
        registry = HookRegistry(Path("/tmp/unused.json"))
        with pytest.raises(HookValidationError):
            registry.update("ghost", url="https://x.example.com")


# =====================================================================
# 3. Persistence
# =====================================================================
class TestRegistryPersistence:
    def test_a_missing_file_loads_as_an_empty_registry(self, tmp_path: Path) -> None:
        """``webhook list`` must work before anything has been added."""
        registry = HookRegistry.load(tmp_path / "nope.json")
        assert len(registry) == 0

    def test_a_saved_registry_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "hooks.json"
        registry = HookRegistry(path)
        registry.add(
            "a",
            "https://x.example.com",
            format="slack",
            secret=SECRET,
            min_severity="warn",
        )
        registry.save()

        reloaded = HookRegistry.load(path)
        assert len(reloaded) == 1
        record = reloaded.get("a")
        assert record is not None
        assert record.url == "https://x.example.com"
        assert record.format == "slack"
        assert record.secret == SECRET
        assert record.min_severity == "warn"

    def test_the_file_is_owner_only(self, tmp_path: Path) -> None:
        """The file holds a credential; it must not be group- or world-readable."""
        path = tmp_path / "hooks.json"
        registry = HookRegistry(path)
        registry.add("a", "https://x.example.com", secret=SECRET)
        registry.save()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_an_existing_wide_open_file_is_tightened_on_save(
        self, tmp_path: Path
    ) -> None:
        """O_CREAT does not change the mode of an existing file, so the
        permission fix must be applied explicitly."""
        path = tmp_path / "hooks.json"
        path.write_text('{"hooks": []}', encoding="utf-8")
        os.chmod(path, 0o644)
        registry = HookRegistry(path)
        registry.add("a", "https://x.example.com", secret=SECRET)
        registry.save()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_a_corrupt_file_is_an_error_not_an_empty_registry(
        self, tmp_path: Path
    ) -> None:
        """Silently starting empty would make an operator believe their
        alerts are unconfigured."""
        path = tmp_path / "hooks.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(HookRegistryError):
            HookRegistry.load(path)

    def test_an_unsupported_schema_version_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "hooks.json"
        path.write_text('{"schema_version": 99, "hooks": []}', encoding="utf-8")
        with pytest.raises(HookRegistryError):
            HookRegistry.load(path)

    def test_an_invalid_hook_entry_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "hooks.json"
        path.write_text('{"hooks": [{"name": "a"}]}', encoding="utf-8")
        with pytest.raises(HookValidationError):
            HookRegistry.load(path)

    def test_to_env_renders_the_positional_form(self, tmp_path: Path) -> None:
        registry = HookRegistry(tmp_path / "hooks.json")
        registry.add("a", "https://one.example.com", secret="s1", format="slack")
        registry.add("b", "https://two.example.com", format="json")
        env = registry.to_env()
        assert (
            env["VALIDSIM_WEBHOOK_URLS"]
            == "https://one.example.com,https://two.example.com"
        )
        assert env["VALIDSIM_WEBHOOK_SECRETS"] == "s1,"
        assert env["VALIDSIM_WEBHOOK_FORMATS"] == "slack,json"

    def test_the_env_override_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_REGISTRY_FILE, "/tmp/somewhere/hooks.json")
        assert registry_path_from_env() == Path("/tmp/somewhere/hooks.json")

    def test_the_env_override_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv(ENV_REGISTRY_FILE, raising=False)
        monkeypatch.chdir(tmp_path)
        assert registry_path_from_env() == tmp_path / ".validsim" / "hooks.json"


# =====================================================================
# 4. The CLI
# =====================================================================
class TestWebhookCli:
    def test_add_then_list(self, registry_file: Path) -> None:
        added = runner.invoke(
            app,
            [
                "webhook",
                "add",
                "slack",
                "https://hooks.slack.com/services/T/B/X",
                "--format",
                "slack",
                "--min-severity",
                "warn",
            ],
        )
        assert added.exit_code == 0, added.output
        assert "Added webhook slack" in added.output

        listed = runner.invoke(app, ["webhook", "list"])
        assert listed.exit_code == 0, listed.output
        assert "slack" in listed.output
        assert "https://hooks.slack.com/services/T/B/X" in listed.output

    def test_list_on_an_empty_registry_is_not_an_error(
        self, registry_file: Path
    ) -> None:
        result = runner.invoke(app, ["webhook", "list"])
        assert result.exit_code == 0, result.output
        assert "No webhooks registered" in result.output

    def test_add_writes_the_file(self, registry_file: Path) -> None:
        result = runner.invoke(
            app, ["webhook", "add", "a", "https://x.example.com", "--secret", SECRET]
        )
        assert result.exit_code == 0, result.output
        assert registry_file.exists()
        data = json.loads(registry_file.read_text(encoding="utf-8"))
        assert data["hooks"][0]["name"] == "a"
        assert data["hooks"][0]["secret"] == SECRET

    def test_list_never_prints_the_secret(self, registry_file: Path) -> None:
        runner.invoke(
            app, ["webhook", "add", "a", "https://x.example.com", "--secret", SECRET]
        )
        listed = runner.invoke(app, ["webhook", "list"])
        assert listed.exit_code == 0
        assert SECRET not in listed.output
        assert "secret=set" in listed.output

    def test_show_never_prints_the_secret(self, registry_file: Path) -> None:
        runner.invoke(
            app, ["webhook", "add", "a", "https://x.example.com", "--secret", SECRET]
        )
        shown = runner.invoke(app, ["webhook", "show", "a"])
        assert shown.exit_code == 0, shown.output
        assert SECRET not in shown.output
        assert '"secret_set": true' in shown.output

    def test_list_json_never_prints_the_secret(self, registry_file: Path) -> None:
        runner.invoke(
            app, ["webhook", "add", "a", "https://x.example.com", "--secret", SECRET]
        )
        listed = runner.invoke(app, ["webhook", "list", "--json"])
        assert listed.exit_code == 0, listed.output
        assert SECRET not in listed.output
        payload = json.loads(listed.output)
        assert payload["hooks"][0]["secret_set"] is True

    def test_remove_deletes_the_hook(self, registry_file: Path) -> None:
        runner.invoke(app, ["webhook", "add", "a", "https://x.example.com"])
        removed = runner.invoke(app, ["webhook", "remove", "a"])
        assert removed.exit_code == 0, removed.output
        listed = runner.invoke(app, ["webhook", "list"])
        assert "No webhooks registered" in listed.output

    def test_remove_of_an_unknown_hook_fails(self, registry_file: Path) -> None:
        result = runner.invoke(app, ["webhook", "remove", "ghost"])
        assert result.exit_code == 2
        assert "no hook named" in result.output

    def test_a_duplicate_add_fails_and_keeps_the_original(
        self, registry_file: Path
    ) -> None:
        runner.invoke(app, ["webhook", "add", "a", "https://one.example.com"])
        duplicate = runner.invoke(
            app, ["webhook", "add", "a", "https://two.example.com"]
        )
        assert duplicate.exit_code == 2
        assert "already exists" in duplicate.output
        listed = runner.invoke(app, ["webhook", "list"])
        assert "https://one.example.com" in listed.output
        assert "https://two.example.com" not in listed.output

    def test_replace_overwrites(self, registry_file: Path) -> None:
        runner.invoke(app, ["webhook", "add", "a", "https://one.example.com"])
        replaced = runner.invoke(
            app, ["webhook", "add", "a", "https://two.example.com", "--replace"]
        )
        assert replaced.exit_code == 0, replaced.output
        listed = runner.invoke(app, ["webhook", "list"])
        assert "https://two.example.com" in listed.output

    def test_an_invalid_url_fails(self, registry_file: Path) -> None:
        result = runner.invoke(app, ["webhook", "add", "a", "not-a-url"])
        assert result.exit_code == 2
        assert "absolute http(s) URL" in result.output

    def test_an_invalid_format_fails(self, registry_file: Path) -> None:
        result = runner.invoke(
            app,
            ["webhook", "add", "a", "https://x.example.com", "--format", "xml"],
        )
        assert result.exit_code == 2
        assert "unsupported hook format" in result.output

    def test_an_invalid_severity_fails(self, registry_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "webhook",
                "add",
                "a",
                "https://x.example.com",
                "--min-severity",
                "nope",
            ],
        )
        assert result.exit_code == 2
        assert "unsupported min_severity" in result.output

    def test_the_warning_spelling_is_accepted_and_normalised(
        self, registry_file: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "webhook",
                "add",
                "a",
                "https://x.example.com",
                "--min-severity",
                "warning",
            ],
        )
        assert result.exit_code == 0, result.output
        listed = runner.invoke(app, ["webhook", "list"])
        assert "min_severity=warn" in listed.output

    def test_a_corrupt_registry_fails_loudly(self, registry_file: Path) -> None:
        registry_file.write_text("{not json", encoding="utf-8")
        result = runner.invoke(app, ["webhook", "list"])
        assert result.exit_code == 2
        assert "invalid JSON" in result.output
        # It must not claim the registry is empty.
        assert "No webhooks registered" not in result.output

    def test_env_renders_the_exportable_form(self, registry_file: Path) -> None:
        runner.invoke(
            app, ["webhook", "add", "a", "https://x.example.com", "--secret", SECRET]
        )
        result = runner.invoke(app, ["webhook", "env"])
        assert result.exit_code == 0, result.output
        assert "export VALIDSIM_WEBHOOK_URLS=https://x.example.com" in result.output

    def test_the_registry_flag_overrides_the_env(self, tmp_path: Path) -> None:
        other = tmp_path / "other.json"
        result = runner.invoke(app, ["webhook", "list", "--registry", str(other)])
        assert result.exit_code == 0, result.output
        assert str(other) in result.output

    def test_help_lists_the_subcommands(self) -> None:
        result = runner.invoke(app, ["webhook", "--help"])
        assert result.exit_code == 0
        for command in ("add", "remove", "list"):
            assert command in result.output
