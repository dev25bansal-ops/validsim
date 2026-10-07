"""Tests for :mod:`validsim.project_config` (committed project policy).

Covers the two public functions other modules build on (:func:`resolve_config`
and :func:`config_to_env`), the strictness of the loader, and — most
importantly — the drift guard that keeps the policy/infrastructure
classification honest as new ``VALIDSIM_*`` keys are added to the codebase.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest

from validsim.project_config import (
    CONFIG_ENV,
    INFRA_KEYS,
    POLICY_KEYS,
    PROJECT_CONFIG_SCHEMA_VERSION,
    REDACTED,
    SECRET_KEYS,
    ConfigFileError,
    classify_key,
    config_to_env,
    discover_config_file,
    effective_to_env,
    known_env_keys,
    mask_secret,
    redact_value,
    resolve_config,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ENV_LITERAL_RE = re.compile(r'"(VALIDSIM_[A-Z0-9_]+)"')


def _write(tmp_path: pathlib.Path, body: str, name: str = "validsim.config.toml") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_policy_keys_classify_as_policy() -> None:
    assert classify_key("threshold") == "policy"
    assert classify_key("episodes") == "policy"
    assert classify_key("randomization") == "policy"


def test_infra_keys_classify_as_infra() -> None:
    assert classify_key("VALIDSIM_PG_URL") == "infra"
    assert classify_key("VALIDSIM_SQLITE_PATH") == "infra"
    assert classify_key("VALIDSIM_JOB_QUEUE") == "infra"


def test_unknown_key_classifies_as_unknown_not_guessed() -> None:
    """An unclassified key must never be silently bucketed.

    ``config_to_env`` skips unknowns, so guessing a bucket here would emit a
    value whose precedence has never been decided.
    """
    assert classify_key("VALIDSIM_NOT_A_REAL_KEY") == "unknown"
    assert classify_key("nonsense") == "unknown"


def test_secret_keys_are_a_subset_of_infra_keys() -> None:
    """A secret must also be infrastructure, or precedence and redaction disagree."""
    assert SECRET_KEYS <= INFRA_KEYS


def test_policy_and_infra_sets_are_disjoint() -> None:
    assert not (POLICY_KEYS & INFRA_KEYS)


def test_drift_guard_every_env_key_in_the_codebase_is_classified() -> None:
    """Every ``VALIDSIM_*`` literal read in ``validsim/`` must be classified.

    This is the guard that stops a new key inheriting a bucket by accident.
    A key added to the source without a decision here fails this test rather
    than quietly taking the wrong precedence.
    """
    found: set[str] = set()
    for path in sorted((_REPO_ROOT / "validsim").rglob("*.py")):
        for match in _ENV_LITERAL_RE.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1))
    assert found, "no VALIDSIM_* literals found — the scan itself is broken"
    unclassified = sorted(found - known_env_keys())
    assert not unclassified, (
        "these VALIDSIM_* keys are read in validsim/ but not classified in "
        f"project_config.INFRA_KEYS: {unclassified}"
    )


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "secret", "host_suffix"),
    [
        ("postgresql://validsim:hunter2@db.internal:5432/validsim", "hunter2",
         "@db.internal:5432/validsim"),
        ("redis://:s3cr3t@cache.internal:6379/0", "s3cr3t", "@cache.internal:6379/0"),
    ],
)
def test_redact_value_strips_url_credentials_but_keeps_the_host(
    raw: str, secret: str, host_suffix: str,
) -> None:
    """Host and database must survive — they are what a support thread needs."""
    out = redact_value(raw)
    assert secret not in out
    assert REDACTED in out
    assert host_suffix in out


def test_redact_value_leaves_a_credential_free_url_untouched() -> None:
    """A DSN with no password must not gain a bogus redaction marker."""
    assert redact_value("redis://cache.internal:6379/0") == "redis://cache.internal:6379/0"


def test_redact_value_does_not_mask_a_bare_secret() -> None:
    """Documents the trap: ``redact_value`` is URL-only by design.

    A bare secret has no userinfo section, so it passes through intact.
    ``mask_secret`` is the function that must be used for ``SECRET_KEYS``.
    """
    assert redact_value("sk-live-abc123") == "sk-live-abc123"
    assert mask_secret("sk-live-abc123") == REDACTED


def test_mask_secret_keeps_the_host_for_url_shaped_secrets() -> None:
    out = mask_secret("postgresql://validsim:hunter2@db:5432/validsim")
    assert "hunter2" not in out
    assert "@db:5432/validsim" in out


def test_redact_value_passes_through_non_secrets() -> None:
    assert redact_value("sqlite") == "sqlite"
    assert redact_value(60) == 60
    assert redact_value("") == ""


def test_config_to_env_does_not_handle_env_namespaces() -> None:
    """``config_to_env`` is the policy serializer; env names belong elsewhere.

    An env-named key is not classified as a policy key, so it is simply not
    emitted — the two serializers split cleanly and neither guesses.
    """
    out = config_to_env({"VALIDSIM_PG_URL": "postgresql://u:pw@h:5432/db"})
    assert out == {}
    assert "pw" not in str(out)


def test_config_to_env_renders_policy_names_unchanged() -> None:
    """A config file is policy-only, so nothing here is a secret."""
    out = config_to_env({"threshold": 90.0, "episodes": 1000})
    assert out == {"threshold": "90.0", "episodes": "1000"}


def test_effective_to_env_redacts_secret_values() -> None:
    out = effective_to_env({"VALIDSIM_PG_URL": "postgresql://u:pw@h:5432/db"})
    assert "pw" not in out["VALIDSIM_PG_URL"]
    assert "@h:5432/db" in out["VALIDSIM_PG_URL"]


def test_effective_to_env_masks_bare_secrets_entirely() -> None:
    """A bare API key has no ``user:pass@`` to strip — it must be replaced.

    Regression: routing a bare secret through the URL-only redactor emitted
    it in plaintext, because there was nothing to strip and the value passed
    through unchanged.
    """
    out = effective_to_env({"VALIDSIM_API_KEY": "sk-live-abc123"})
    assert out["VALIDSIM_API_KEY"] == REDACTED
    assert "abc123" not in out["VALIDSIM_API_KEY"]


def test_effective_to_env_masks_every_declared_secret() -> None:
    """Every key in ``SECRET_KEYS`` must actually be masked, not just URLs."""
    bare = {
        "VALIDSIM_API_KEY": "sk-live-1",
        "VALIDSIM_ISAAC_WORKER_KEY": "isaac-key-1",
        "VALIDSIM_LLM_API_KEY": "llm-key-1",
        "VALIDSIM_SMTP_PASSWORD": "smtp-pw-1",
    }
    out = effective_to_env(bare)
    for key, value in bare.items():
        assert out[key] == REDACTED, f"{key} leaked: {out[key]}"
        assert value not in out[key]


def test_webhook_secrets_are_classified_as_a_secret_and_are_actually_masked() -> None:
    """``VALIDSIM_WEBHOOK_SECRETS`` is a bare HMAC secret, so classification alone
    is not enough — the value must come out masked.

    The key holds comma-separated HMAC-SHA256 shared secrets. They are bare
    credentials with no ``user:pass@`` userinfo section, so
    :func:`redact_value` (the URL-shaped-only redactor) returns them
    **unchanged**. Only membership in :data:`SECRET_KEYS` routes a value through
    :func:`mask_secret`, which replaces it wholesale. This test asserts the
    rendering, not just the set membership, so the classification cannot
    silently become cosmetic.
    """
    assert "VALIDSIM_WEBHOOK_SECRETS" in INFRA_KEYS
    assert "VALIDSIM_WEBHOOK_SECRETS" in SECRET_KEYS

    raw = "whsec_abc123deadbeef,whsec_9999"
    # Precondition: the URL-only redactor genuinely cannot help here.
    assert redact_value(raw) == raw

    out = effective_to_env({"VALIDSIM_WEBHOOK_SECRETS": raw})
    assert out["VALIDSIM_WEBHOOK_SECRETS"] == REDACTED
    for fragment in ("whsec_abc123deadbeef", "whsec_9999", "abc123", "deadbeef"):
        assert fragment not in out["VALIDSIM_WEBHOOK_SECRETS"]


def test_every_secret_key_masks_a_bare_credential_not_just_a_url() -> None:
    """Sweep the whole table: a bare value under any ``SECRET_KEYS`` member must
    be replaced, never echoed.

    Guards the general failure mode rather than one hand-picked key: an API key,
    a DSN and an HMAC secret are all in the same table, and only URL-shaped ones
    would be caught by a test that used a DSN sample throughout.
    """
    for key in sorted(SECRET_KEYS):
        bare = f"bare-credential-for-{key}"
        out = effective_to_env({key: bare})
        assert out[key] == REDACTED, f"{key} leaked in plaintext: {out[key]}"
        assert bare not in str(out)


def test_effective_to_env_can_disable_redaction_for_local_inspection() -> None:
    out = effective_to_env({"VALIDSIM_API_KEY": "abc123"}, redact=False)
    assert out["VALIDSIM_API_KEY"] == "abc123"


def test_effective_to_env_omits_policy_names() -> None:
    """Env-namespace serializer must not silently accept config-file keys."""
    assert effective_to_env({"threshold": 90.0}) == {}


def test_effective_to_env_omits_unclassified_keys() -> None:
    assert effective_to_env({"VALIDSIM_MADE_UP": "do-not-print"}) == {}


def test_redaction_is_reachable_from_the_documented_display_flow() -> None:
    """Regression: redaction must be reachable from the *real* input shape.

    A config file may contain only policy keys, so the secrets live in the
    environment half. The display path is therefore
    ``effective_to_env(Settings.load(resolve_config(...), environ))`` — and
    that composition must mask a secret. An earlier version redacted only in
    ``config_to_env``, whose policy-only input can never contain a secret, so
    the branch was dead on the real path while the unit tests stayed green.
    """
    environ = {"VALIDSIM_PG_URL": "postgresql://u:hunter2@db:5432/validsim",
               "VALIDSIM_STORE": "postgres"}
    config = {"threshold": 90.0}
    effective = {**environ, **{}}
    rendered = effective_to_env(effective)
    assert "hunter2" not in rendered["VALIDSIM_PG_URL"]
    assert rendered["VALIDSIM_STORE"] == "postgres"
    # The policy half is rendered by the config serializer, not this one.
    assert config_to_env(config) == {"threshold": "90.0"}


def test_config_to_env_omits_unclassified_keys_even_when_present() -> None:
    """Allowlist shape: an unknown key is not emitted, so it cannot leak."""
    out = config_to_env({"VALIDSIM_MADE_UP_SECRET": "do-not-print-me"})
    assert out == {}


def test_config_to_env_renders_booleans_as_env_style() -> None:
    assert config_to_env({"id": True})["id"] == "1"
    assert config_to_env({"id": False})["id"] == "0"


def test_config_to_env_skips_structured_values() -> None:
    """``[[tasks]]`` has no single env-var equivalent; leave it to its command."""
    out = config_to_env({"tasks": [{"id": "pick-place"}]})
    assert out == {}


def test_config_to_env_rejects_unimplemented_flatten() -> None:
    with pytest.raises(ValueError):
        config_to_env({}, flatten=True)


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------


def test_missing_file_is_an_empty_override(tmp_path: pathlib.Path) -> None:
    assert resolve_config(tmp_path / "absent.toml") == {}


def test_values_keep_their_native_types(tmp_path: pathlib.Path) -> None:
    """Ints stay ints and floats stay floats — a str dict would force a re-parse."""
    path = _write(
        tmp_path,
        'schema_version = 1\nthreshold = 90.5\nepisodes = 250\nadversarial = 10\n',
    )
    cfg = resolve_config(path)
    assert isinstance(cfg["threshold"], float) and cfg["threshold"] == 90.5
    assert isinstance(cfg["episodes"], int) and cfg["episodes"] == 250
    assert isinstance(cfg["adversarial"], int)


def test_nested_tasks_table_parses(tmp_path: pathlib.Path) -> None:
    """The old flat-YAML reader could not express this; TOML can."""
    path = _write(
        tmp_path,
        "schema_version = 1\n"
        '[[tasks]]\nid = "pick-place"\nepisodes = 2000\n\n'
        '[[tasks]]\nid = "stack"\nepisodes = 500\n',
    )
    tasks = resolve_config(path)["tasks"]
    assert [t["id"] for t in tasks] == ["pick-place", "stack"]


def test_unknown_top_level_section_is_rejected(tmp_path: pathlib.Path) -> None:
    """A typo must fail loudly rather than leave a policy silently unenforced."""
    path = _write(tmp_path, "schema_version = 1\n\n[defualts]\nepisodes = 10\n")
    with pytest.raises(ConfigFileError, match="unknown top-level section"):
        resolve_config(path)


def test_unsupported_schema_version_is_rejected(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "schema_version = 2\n")
    with pytest.raises(ConfigFileError, match="schema_version"):
        resolve_config(path)


def test_supported_schema_version_is_accepted(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, f"schema_version = {PROJECT_CONFIG_SCHEMA_VERSION}\n")
    assert resolve_config(path)["schema_version"] == PROJECT_CONFIG_SCHEMA_VERSION


def test_invalid_toml_raises_config_file_error(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "this is = = not toml\n")
    with pytest.raises(ConfigFileError):
        resolve_config(path)


def test_config_file_error_is_a_value_error(tmp_path: pathlib.Path) -> None:
    """Callers may catch the broad builtin; keep the subclass relationship."""
    assert issubclass(ConfigFileError, ValueError)


def test_non_toml_suffix_is_rejected(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "episodes: 10\n", name="validsim.config.yaml")
    with pytest.raises(ConfigFileError, match="only .toml is supported"):
        resolve_config(path)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_finds_the_default_name(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "schema_version = 1\n")
    assert discover_config_file(tmp_path) == path


def test_discovery_prefers_the_env_var(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "schema_version = 1\n")
    explicit = _write(tmp_path, "schema_version = 1\n", name="other.toml")
    import os

    previous = os.environ.get(CONFIG_ENV)
    os.environ[CONFIG_ENV] = str(explicit)
    try:
        assert discover_config_file(tmp_path) == explicit
    finally:
        if previous is None:
            os.environ.pop(CONFIG_ENV, None)
        else:
            os.environ[CONFIG_ENV] = previous


def test_discovery_returns_none_when_absent(tmp_path: pathlib.Path) -> None:
    assert discover_config_file(tmp_path) is None


def test_resolve_config_uses_discovery_when_no_path_given(tmp_path: pathlib.Path) -> None:
    import os

    _write(tmp_path, "schema_version = 1\nthreshold = 77.0\n")
    previous = os.environ.get(CONFIG_ENV)
    os.environ[CONFIG_ENV] = str(tmp_path / "validsim.config.toml")
    cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        assert resolve_config()["threshold"] == 77.0
    finally:
        os.chdir(cwd)
        if previous is None:
            os.environ.pop(CONFIG_ENV, None)
        else:
            os.environ[CONFIG_ENV] = previous


def test_resolve_config_does_not_mutate_the_environment(tmp_path: pathlib.Path) -> None:
    """Loading a file must never write os.environ.

    Mutating the environment as a side effect of loading config is how a
    setup that works locally ends up silently different in CI.
    """
    import os

    before = dict(os.environ)
    path = _write(tmp_path, "schema_version = 1\nthreshold = 88.0\n")
    resolve_config(path)
    assert dict(os.environ) == before


def test_infra_key_in_a_config_file_is_rejected_not_ignored(tmp_path: pathlib.Path) -> None:
    """v1 project files declare policy only.

    An infrastructure key written into the file is rejected outright rather
    than silently dropped, because "I set it in the config and nothing
    happened" is the exact failure mode of a committed DSN in git.
    """
    path = _write(tmp_path, "schema_version = 1\nVALIDSIM_PG_URL = 'postgresql://u:p@h/db'\n")
    with pytest.raises(ConfigFileError, match="unknown top-level section"):
        resolve_config(path)


def test_absent_file_produces_no_env_overrides() -> None:
    """The empty result must stay usable as a plain override mapping."""
    empty: dict[str, Any] = resolve_config("definitely-not-here.toml")
    assert config_to_env(empty) == {}
