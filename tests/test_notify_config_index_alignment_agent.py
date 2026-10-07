"""Index alignment between webhook URLs and their positional companion lists.

The defect
----------
``VALIDSIM_WEBHOOK_URLS`` is one entry per destination; ``VALIDSIM_WEBHOOK_SECRETS``,
``..._FORMATS`` and ``..._MIN_SEVERITY`` pair with it **by index**. The parser
used for companions dropped blank entries, which collapsed the indices: with
``SECRETS=",s2"`` over two URLs, hook 1 was signed with hook 2's key and hook 2
was left **unsigned** -- the exact inverse of the operator's intent. The fix
introduces ``_split_positional()``, which keeps blank placeholders.

Why this file is worth its own module
-------------------------------------
The pre-existing notify tests all configure hooks through the Python API
(``dispatcher.register(...)``), which never touches the env parser -- so the whole
index-alignment layer had no coverage at all, which is why this shipped green.

All expected values below were executed against ``notify_config_from_env()`` and
cross-checked against notify-sec's independently verified table; every row agreed.

Environment is passed as an explicit mapping, never ``monkeypatch.setenv``, so
nothing leaks between tests and the resolution is fully determined by the dict.
"""

from __future__ import annotations

from validsim.notify.config import notify_config_from_env

_A = "https://a.example/h"
_B = "https://b.example/h"
_C = "https://c.example/h"
_U2 = f"{_A},{_B}"
_U3 = f"{_A},{_B},{_C}"

_URLS = "VALIDSIM_WEBHOOK_URLS"
_SECRETS = "VALIDSIM_WEBHOOK_SECRETS"
_FORMATS = "VALIDSIM_WEBHOOK_FORMATS"
_MIN_SEVERITY = "VALIDSIM_WEBHOOK_MIN_SEVERITY"


def _hooks(**env: str) -> list[tuple[str, object]]:
    """Resolve *env* into ``(name, secret, format, min_severity)`` tuples."""
    cfg = notify_config_from_env(env)
    return [(h.name, h.secret, h.format, h.min_severity) for h in cfg.hooks]


def _secrets(**env: str) -> list[object]:
    """Just the secrets, in hook order."""
    return [secret for _name, secret, _fmt, _sev in _hooks(**env)]


class TestBlankPlaceholderHoldsItsPosition:
    """The defect class: a blank must occupy a slot, not vanish."""

    def test_leading_blank_leaves_hook_one_unsigned(self) -> None:
        """The money shot: hook 2's key must not sign hook 1.

        Pre-fix this resolved to ``["s2", None]`` -- hook 1 signed with hook 2's
        secret (wrong destination, wrong key) and hook 2 sent **unsigned**. An
        unsigned webhook silently loses its signature verification, and a
        wrongly-signed one fails at the receiver. Both are silent.
        """
        assert _secrets(**{_URLS: _U2, _SECRETS: ",s2"}) == [None, "s2"], (
            "a leading blank must hold hook 1's slot so s2 lands on hook 2; got "
            f"{_secrets(**{_URLS: _U2, _SECRETS: ',s2'})}"
        )

    def test_leading_blank_across_three_hooks(self) -> None:
        """The misrouting cascades: two later hooks both shift down."""
        assert _secrets(**{_URLS: _U3, _SECRETS: ",s2,s3"}) == [None, "s2", "s3"]

    def test_trailing_blank_leaves_the_last_hook_unsigned(self) -> None:
        """The mirror image: a trailing blank must not pull hook 2's key down.

        This row behaved correctly even pre-fix, which is exactly why it is worth
        pinning: it distinguishes "blank dropped" from "blank moved", and the
        fix had to preserve it.
        """
        assert _secrets(**{_URLS: _U2, _SECRETS: "s1,"}) == ["s1", None]

    def test_all_blank_resolves_to_no_secrets_at_all(self) -> None:
        assert _secrets(**{_URLS: _U2, _SECRETS: ","}) == [None, None]

    def test_a_blank_in_the_middle_keeps_both_neighbours_in_place(self) -> None:
        """Not just leading/trailing: an interior blank must hold its own slot.

        Added beyond notify-sec's table. A parser that special-cased the first or
        last field would pass every row above and still corrupt this one.
        """
        assert _secrets(**{_URLS: _U3, _SECRETS: "s1,,s3"}) == ["s1", None, "s3"]


class TestSameDefectViaTheOtherCompanions:
    def test_blank_format_does_not_shift_slack_onto_the_wrong_hook(self) -> None:
        assert _hooks(**{_URLS: _U2, _FORMATS: ",slack"}) == [
            ("webhook-1", None, "json", "info"),
            ("webhook-2", None, "slack", "info"),
        ]

    def test_blank_severity_does_not_invert_the_routing_table(self) -> None:
        """Operationally the worst of the three.

        Pre-fix, ``MIN_SEVERITY=",warn"`` gave hook 1 ``warn`` and left hook 2 at
        ``info``: an inverted routing table in which the hook the operator meant
        to make loud stayed quiet on ``info`` events while a hook they expected
        to be quiet started filtering them. Nothing warns about this -- the
        dispatcher simply drops events.
        """
        hooks = _hooks(**{_URLS: _U2, _MIN_SEVERITY: ",warn"})
        assert [h[3] for h in hooks] == ["info", "warn"], (
            f"severities must stay positional, got {[h[3] for h in hooks]}"
        )

    def test_blank_critical_does_not_over_escalate_the_quiet_hook(self) -> None:
        """The ``",critical"`` variant, and the security-relevant one.

        ``critical`` is the top severity band, so under the old blank-dropping
        parser ``MIN_SEVERITY=",critical"`` assigned ``critical`` to **hook 1**
        and left hook 2 at ``info``. That is a paging defect, not a cosmetic
        mislabel: hook 1 would escalate to a critical alert on every ``info``
        event the operator explicitly configured to be filtered out, and hook 2
        -- the one that was meant to be loud -- stayed silent. Notifications
        train operators to ignore alerts, so a false critical is worse than no
        alert at all.
        """
        hooks = _hooks(**{_URLS: _U2, _MIN_SEVERITY: ",critical"})
        assert [h[3] for h in hooks] == ["info", "critical"], (
            "a leading blank must hold hook 1's slot at the default severity; "
            f"got {[h[3] for h in hooks]}"
        )

    def test_a_trailing_critical_leaves_the_first_hook_alone(self) -> None:
        """The mirror case, pinned so the pair is read as one behaviour.

        ``"info,critical"`` must escalate hook 2 only. Pre-fix this row behaved
        correctly, so it distinguishes "blank dropped" from "blank moved" --
        exactly as the trailing-blank secret row does.
        """
        hooks = _hooks(**{_URLS: _U2, _MIN_SEVERITY: "info,critical"})
        assert [h[3] for h in hooks] == ["info", "critical"]


class TestShorterCompanionListsStillPad:
    """Must-not-regress: a genuinely shorter companion list pads with ``None``.

    Not a "safe default" sentinel. ``_at()`` returns ``None`` for an absent index
    and ``HookSpec.secret`` is ``str | None``, so an unsigned hook is ``None``.
    ``"info"`` and ``"json"`` are the *field* defaults applied afterwards to every
    hook regardless -- they are not substitutes for a missing secret.
    """

    def test_one_secret_over_two_urls_pads_the_second_with_none(self) -> None:
        assert _secrets(**{_URLS: _U2, _SECRETS: "s1"}) == ["s1", None]

    def test_full_pairing_needs_no_padding(self) -> None:
        assert _secrets(**{_URLS: _U2, _SECRETS: "s1,s2"}) == ["s1", "s2"]

    def test_an_absent_companion_leaves_every_hook_unsigned(self) -> None:
        assert _secrets(**{_URLS: _U2}) == [None, None]

    def test_a_wholly_empty_companion_is_treated_as_absent(self) -> None:
        """``SECRETS=`` must not become a one-element list holding ``""``."""
        assert _secrets(**{_URLS: _U2, _SECRETS: ""}) == [None, None]

    def test_format_and_severity_defaults_apply_to_every_hook(self) -> None:
        """The defaults are field-level, so they hold for *both* hooks."""
        hooks = _hooks(**{_URLS: _U2, _SECRETS: "s1"})
        assert [(h[2], h[3]) for h in hooks] == [("json", "info"), ("json", "info")]


class TestInvalidCompanionValuesStillFallBack:
    """Must-not-regress: an unusable entry falls back rather than raising."""

    def test_an_invalid_format_falls_back_to_json(self) -> None:
        assert _hooks(**{_URLS: _A, _FORMATS: "xml"}) == [
            ("webhook-1", None, "json", "info")
        ]

    def test_an_invalid_severity_falls_back_to_info(self) -> None:
        assert _hooks(**{_URLS: _A, _MIN_SEVERITY: "loud"}) == [
            ("webhook-1", None, "json", "info")
        ]

    def test_a_blank_plus_an_invalid_sibling_is_not_a_regression_guard(self) -> None:
        """Documents why this case is useless as a guard -- both hooks end up json.

        ``FORMATS=",xml"`` puts a blank on hook 1 (valid, absent -> json) and an
        invalid value on hook 2 (invalid -> json). The result is ``json`` either
        way, so the case passes both before and after the fix. Recorded so a
        future reader does not mistake it for coverage; the real guard for the
        blank-placeholder class is the valid-sibling ``FORMATS=",slack"``.
        """
        hooks = _hooks(**{_URLS: _U2, _FORMATS: ",xml"})
        assert [h[2] for h in hooks] == ["json", "json"], (
            "this case is documented as non-discriminating; if the engine "
            "changes so it DOES discriminate, replace it with a real guard"
        )


class TestHookNamesArePositionalOnTheRawUrlIndex:
    """``webhook-N`` numbers the URL's slot in the *raw* env string.

    Verified: ``URLS=not-a-url,https://b.example/h`` yields exactly one hook named
    ``webhook-2``. Name and secret are therefore both positional on the raw index,
    which is what makes the drop case below coherent rather than surprising.
    """

    def test_three_urls_are_numbered_one_to_three(self) -> None:
        names = [h[0] for h in _hooks(**{_URLS: _U3})]
        assert names == ["webhook-1", "webhook-2", "webhook-3"]

    def test_names_start_at_one_not_zero(self) -> None:
        names = [h[0] for h in _hooks(**{_URLS: _U2})]
        assert names == ["webhook-1", "webhook-2"]

    def test_a_dropped_url_leaves_a_gap_in_the_numbering(self) -> None:
        """KNOWN SHARP EDGE -- documented, deliberately preserved, NOT a bug.

        Dropping a malformed destination re-indexes the *survivors* only in the
        sense that they keep their original numbers, so the surviving hook is
        called ``webhook-2`` even though it is the only hook. That is intentional:
        one bad destination must not disable the others, and renumbering would
        silently repoint a companion list at the wrong destination -- the exact
        class of bug this file exists to pin.

        The consequence, which is the real sharp edge, is that a companion list
        must be kept in *original* positions: here ``s2`` (index 1) belongs to the
        surviving hook, and ``s1`` is simply unused.

        Pinned so a future reader who "fixes" the numbering breaks loudly instead
        of quietly repointing secrets at the wrong destination.
        """
        hooks = _hooks(**{_URLS: f"not-a-url,{_B}", _SECRETS: "s1,s2"})
        assert hooks == [("webhook-2", "s2", "json", "info")], (
            "a dropped URL must not renumber the survivors; doing so would "
            "repoint a positional companion list at the wrong destination"
        )

    def test_a_trailing_malformed_url_keeps_the_first_hook_numbering(self) -> None:
        """Dropping the *last* URL leaves the earlier numbers untouched."""
        hooks = _hooks(**{_URLS: f"{_A},not-a-url", _SECRETS: "s1,s2"})
        assert hooks == [("webhook-1", "s1", "json", "info")]


class TestMalformedUrlsAreDroppedNotFatal:
    def test_one_bad_url_does_not_disable_the_others(self) -> None:
        assert len(_hooks(**{_URLS: f"{_A},ftp://x/,{_B}"})) == 2

    def test_all_malformed_yields_no_hooks_rather_than_raising(self) -> None:
        assert _hooks(**{_URLS: "not-a-url,ftp://x/,file:///c:/x"}) == []

    def test_a_non_http_scheme_is_rejected(self) -> None:
        """Only http/https are accepted, per ``_valid_url``."""
        for bad in ("ftp://x/h", "file:///c:/w.ini", "gopher://127.0.0.1:11211/_stats"):
            assert _hooks(**{_URLS: bad}) == [], f"{bad} should not be a valid URL"
