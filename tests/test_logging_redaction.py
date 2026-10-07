"""Redaction layer in the JSON log formatter.

Motivation
----------
:class:`~validsim.logging.JsonLogFormatter` merged every caller-supplied
``extra={...}`` field into the emitted JSON **verbatim**. The stdlib's
``extra=`` argument is how every caller in this codebase attaches structured
context (``api/main.py`` logs ``method``/``path``/``status``/``duration_ms``),
and nothing inspected the *names* of those fields before serialising them. So a
single ``logger.info("auth", extra={"api_key": key})`` wrote the credential to
stdout, to whatever log aggregator shipped it, and to any retained CI artifact
-- with no code change and no obvious mistake on the caller's part.

Structured logging is precisely the surface where secrets *accidentally*
travel: the whole point of ``extra=`` is "attach a field the operator will later
want", and the field people most want attached is the one they most regret
attaching.

Contract
--------
A field whose **name** is known-sensitive has its **value** replaced with
:data:`validsim.logging.REDACTED` before serialisation. Matching is on the
normalised name (lower-cased, ``-`` and ``_`` removed) so ``API_KEY``,
``api-key`` and ``apiKey`` are all the same field. Redaction is
**structural**: nested dicts, lists and tuples are walked, so a credential
buried one level down is masked too.

Two deliberate non-goals, both documented on the formatter:

* The free-text ``message`` is **not** scanned. Pattern-matching prose
  produces false positives on ordinary English ("token" in "tokenizer") and
  gives no guarantee anyway; the boundary is structured fields, where a field
  *name* is a reliable signal.
* Only *known* names are masked. A permissive substring rule would redact
  ``auth_enabled`` (a boolean this codebase logs on purpose) and every other
  field that merely contains a sensitive word, destroying diagnostics in the
  name of safety. The set is therefore explicit, and over-redaction of a
  derived name (``csrftoken``) is preferred to under-redaction of a real one.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from validsim.logging import (
    REDACTED,
    JsonLogFormatter,
    configure_logging,
    is_sensitive_key,
)

#: A credential that must never survive into a log line, in every spelling.
_API_KEY = "sk-live-DO-NOT-LOG-abc123"
#: A second, distinct secret so a test cannot pass by masking the wrong field.
_PASSWORD = "hunter2-DO-NOT-LOG"


def _emit(extra: dict[str, object], msg: str = "event") -> dict:
    """Format one INFO record carrying *extra* and return the parsed payload."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    record = logging.LogRecord(
        name="validsim.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    handler.emit(record)
    return json.loads(stream.getvalue())


class TestSensitiveKeyDetection:
    """The name classifier decides everything, so it is tested directly."""

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "PASSWORD",
            "passwd",
            "api_key",
            "apiKey",
            "API-KEY",
            "api-key",
            "x_api_key",
            "VALIDSIM_API_KEY",
            "VALIDSIM_LLM_API_KEY",
            "VALIDSIM_SMTP_PASSWORD",
            "token",
            "access_token",
            "refresh_token",
            "csrf_token",
            "secret",
            "client_secret",
            "private_key",
            "secret_key",
            "access_key",
            "authorization",
            "Authorization",
            "credentials",
            "cookie",
            "set-cookie",
        ],
    )
    def test_recognises_sensitive_names(self, key: str) -> None:
        assert is_sensitive_key(key) is True, f"{key!r} must be treated as sensitive"

    @pytest.mark.parametrize(
        "key",
        [
            "status",
            "method",
            "path",
            "duration_ms",
            "request_id",
            "run_id",
            "checkpoint_id",
            "task_id",
            "auth_enabled",
            "deployment",
            "composite_score",
            "threshold",
            "decision",
            "block_reasons",
            "monkey",
            "keyboard",
            "hockey",
        ],
    )
    def test_leaves_ordinary_names_alone(self, key: str) -> None:
        assert is_sensitive_key(key) is False, f"{key!r} must NOT be redacted"

    def test_classification_is_case_and_separator_insensitive(self) -> None:
        for spelling in ("api_key", "API_KEY", "api-key", "ApiKey", "api key"):
            assert is_sensitive_key(spelling) is True, spelling


class TestTopLevelRedaction:
    def test_api_key_is_not_emitted_in_plaintext(self) -> None:
        payload = _emit({"api_key": _API_KEY})
        assert payload["api_key"] == REDACTED
        assert _API_KEY not in json.dumps(payload)

    def test_password_is_not_emitted_in_plaintext(self) -> None:
        payload = _emit({"password": _PASSWORD})
        assert payload["password"] == REDACTED
        assert _PASSWORD not in json.dumps(payload)

    def test_bearer_authorization_header_is_masked(self) -> None:
        payload = _emit({"authorization": f"Bearer {_API_KEY}"})
        assert payload["authorization"] == REDACTED
        assert _API_KEY not in json.dumps(payload)

    def test_only_the_sensitive_field_is_masked(self) -> None:
        """Redaction must not blind the operator to the rest of the record."""
        payload = _emit(
            {"api_key": _API_KEY, "status": 200, "method": "POST", "path": "/v1/run"}
        )
        assert payload["status"] == 200
        assert payload["method"] == "POST"
        assert payload["path"] == "/v1/run"
        assert payload["api_key"] == REDACTED

    def test_a_non_string_secret_is_still_masked(self) -> None:
        """An int/bool/dict under a sensitive name carries no less risk."""
        payload = _emit({"token": 123456789, "secret": True})
        assert payload["token"] == REDACTED
        assert payload["secret"] == REDACTED

    def test_none_under_a_sensitive_name_is_masked_consistently(self) -> None:
        # Masking an absent value costs nothing and keeps the field's
        # "this is a secret" classification visible in the log line.
        assert _emit({"api_key": None})["api_key"] == REDACTED

    def test_auth_enabled_boolean_survives(self) -> None:
        """The false-positive guard.

        ``api/main.py`` logs ``extra={"auth_enabled": False, ...}``. A
        substring rule on "auth" would destroy that field.
        """
        payload = _emit({"auth_enabled": False, "deployment": "development"})
        assert payload["auth_enabled"] is False
        assert payload["deployment"] == "development"


class TestNestedRedaction:
    def test_secret_inside_a_nested_dict_is_masked(self) -> None:
        payload = _emit(
            {"request": {"path": "/v1", "auth": {"api_key": _API_KEY}, "status": 201}}
        )
        request = payload["request"]
        assert request["path"] == "/v1"
        assert request["status"] == 201
        assert request["auth"]["api_key"] == REDACTED
        assert _API_KEY not in json.dumps(payload)

    def test_secret_inside_a_list_of_dicts_is_masked(self) -> None:
        payload = _emit(
            {"accounts": [{"id": 1, "password": _PASSWORD}, {"id": 2, "ok": True}]}
        )
        assert payload["accounts"][0]["password"] == REDACTED
        assert payload["accounts"][0]["id"] == 1
        assert payload["accounts"][1]["ok"] is True
        assert _PASSWORD not in json.dumps(payload)

    def test_secret_inside_a_tuple_is_masked(self) -> None:
        payload = _emit({"pair": ("public", {"api_key": _API_KEY})})
        assert payload["pair"][0] == "public"
        assert payload["pair"][1]["api_key"] == REDACTED
        assert _API_KEY not in json.dumps(payload)

    def test_deeply_nested_secret_is_masked(self) -> None:
        payload = _emit({"a": {"b": {"c": {"d": {"password": _PASSWORD}}}}})
        assert _PASSWORD not in json.dumps(payload)
        assert payload["a"]["b"]["c"]["d"]["password"] == REDACTED

    def test_self_referential_structure_does_not_recurse_forever(self) -> None:
        """A cyclic ``extra`` value must not hang or raise inside the formatter.

        ``record.__dict__`` is caller-controlled, so a cycle is representable;
        the formatter's one job is to emit a line, not to raise.
        """
        loop: dict = {"api_key": _API_KEY}
        loop["self"] = loop
        payload = _emit({"cfg": loop})
        assert _API_KEY not in json.dumps(payload)
        # The log line still went out, with a usable value in place of the cycle.
        assert "cfg" in payload


class TestConfiguredLoggerEndToEnd:
    def test_configure_logging_redacts_a_real_record(self, tmp_path) -> None:
        """The guarantee must hold through the installed handler, not just
        a hand-built formatter -- otherwise a caller using the documented
        :func:`get_logger` path is unprotected."""
        # Assert on the stream we handed in, not on ``handlers[0]``:
        # ``configure_logging(force=True)`` removes only its own (marked) handler
        # and then appends the replacement, so index 0 belongs to whichever
        # handlers the host already had -- pytest installs its own live-logging
        # handlers for the session. The buffer is the only unambiguous handle on
        # the handler this call installed.
        buffer = io.StringIO()
        logger = configure_logging(force=True, stream=buffer)
        logger.info("login attempt", extra={"api_key": _API_KEY, "status": 401})
        for handler in logger.handlers:
            handler.flush()
        line = buffer.getvalue().strip()
        assert line, "the record never reached the configured stream"
        payload = json.loads(line)
        assert payload["api_key"] == REDACTED
        assert payload["status"] == 401
        assert _API_KEY not in line

    def test_non_ascii_payloads_still_serialise(self) -> None:
        """Redaction must not change the formatter's UTF-8 contract."""
        payload = _emit({"path": "/café/ naïve", "api_key": _API_KEY})
        assert payload["path"] == "/café/ naïve"
        assert payload["api_key"] == REDACTED


class TestExistingBehaviourPreserved:
    def test_reserved_record_fields_are_not_mistaken_for_extras(self) -> None:
        """``module``/``filename``/``funcName`` are stdlib attributes, not extras.

        They are filtered out by the reserved-attribute check, and must stay
        filtered out -- the redaction walk must not resurrect them.
        """
        payload = _emit({"request_id": "abc-123"})
        assert payload["request_id"] == "abc-123"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "validsim.test"
        assert "lineno" not in payload
        assert "pathname" not in payload

    def test_message_and_timestamp_are_untouched(self) -> None:
        payload = _emit({}, msg="plain message")
        assert payload["message"] == "plain message"
        assert payload["timestamp"].endswith("+00:00")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
