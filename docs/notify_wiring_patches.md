# notify-wiring — patches OWNER must apply (outside `validsim/notify/`)

Three edits complete this work. Each is a copy-paste block; each has a RED
test already sitting in `tests/test_notify_wiring_agent.py`, marked
`xfail(strict=False)` and tagged `PENDING OWNER`. **Delete the marker when you
apply the patch** — pytest will then report `XPASS`, which is the signal that
the wiring landed and the test is doing real work.

Do all three, then run (no `-q` — `pytest.ini` already sets it, and a second
one stacks to `-qq` and hides the pass/fail summary):
    python -m pytest tests/test_notify_wiring_agent.py

---

## 1. `validsim/engine/pipeline.py` — dispatch on run completion

Unlocks RED test `TestReachability::test_notify_modules_are_loaded_by_the_shipped_entrypoints`.

Add the import (keep the existing alphabetical grouping; it belongs after
`from validsim.engine.evaluation import evaluate`'s block — put it with the
other `validsim.*` imports):

```python
from validsim.notify.dispatcher import notify_run_completion
```

Then, in `run_and_score`, change the `return store.save(...)` tail into an
assignment plus a dispatch. Note the dispatch runs **after** `store.save`
returns, so a webhook problem can never cost a run its persisted result:

```python
    stored = store.save(
        StoredRun(
            run_id=run_id,
            checkpoint_id=checkpoint_id,
            task_id=task.task_id,
            created_at=scorecard.created_at,
            scorecard=scorecard,
            evaluation=evaluation,
            safety=safety,
            episodes=episodes,
            baseline_run_id=baseline_run_id,
            regression=regression,
        )
    )
    # Run-completion notification. Inert unless VALIDSIM_NOTIFY_ENABLED and
    # VALIDSIM_WEBHOOK_URLS are both set, and never raises, so the default
    # path is unchanged and a broken webhook cannot fail a finished run.
    notify_run_completion(scorecard)
    return stored
```

Update the `run_and_score` docstring's `Returns:` to note the notification
side-effect, and the module docstring's step list to add step 7 ("optionally
notify configured webhooks").

**Why after `save`:** dispatching first would mean a run whose webhook hangs
had not yet been persisted, turning a notification outage into data loss.

---

## 2. `validsim/api/main.py` — surface effective config on `/api/v1/health`

Unlocks RED tests `TestHealthSurface::*` (2 tests).

Add the import with the other `validsim.*` imports:

```python
from validsim.notify.config import notify_config_from_env
```

Then add one key to the `health()` return dict (the route is at
`validsim/api/main.py:705`):

```python
            "notify": notify_config_from_env().summary(),
```

and extend the `health()` docstring bullet list with:

```
        * ``notify`` — effective notification config: ``{"enabled",
          "hook_count", "live", "hooks"}``. See
          :func:`validsim.notify.config.NotifyConfig.summary` for why hook
          URLs and secrets are withheld.
```

**Do not cache this on `app.state`.** The rest of the health payload is
boot-frozen, but the notify config is read per call on purpose: the whole
point of this surface is that an operator can confirm a change they *just*
made took effect, and the live-mode flag is now resolved per dispatch
precisely so it is not boot-frozen. Caching would reintroduce the exact
silent-no-op bug being fixed.

The call is cheap (dict lookups plus a `urlparse` per configured hook, and
zero hooks in the default case) so the route stays I/O-free and safe to back
the container healthcheck.

---

## 3. `validsim/project_config.py` — classify the new env vars — **LANDED**

**Status: applied and verified.** team-lead ruled this one is in my folder (it
is not optional — the drift guard hard-fails without it). Done, with four RED
tests added in `tests/test_notify_wiring_agent.py::TestEnvVarClassification`
that assert the *behaviour* (a value under the key is actually masked), not
merely the classification. Verified: my five keys no longer appear in the
drift-guard failure list.

What was applied:

In `INFRA_KEYS` (alphabetical, next to the existing `VALIDSIM_WEBHOOKS_LIVE`):

```python
        "VALIDSIM_NOTIFY_ENABLED",
        "VALIDSIM_WEBHOOK_FORMATS",
        "VALIDSIM_WEBHOOK_MIN_SEVERITY",
        "VALIDSIM_WEBHOOK_SECRETS",
        "VALIDSIM_WEBHOOK_URLS",
```

In `SECRET_KEYS` — `VALIDSIM_WEBHOOK_SECRETS` only:

```python
        "VALIDSIM_WEBHOOK_SECRETS",
```

**Why it must be a secret:** it holds an HMAC-SHA256 shared secret. It is a
bare credential with no `user:pass@` section, so routing it through
`redact_value` (as `INFRA_KEYS`-only keys do) emits it **in plaintext** in
`validsim config show` and the support bundle. Only `mask_secret` replaces it
wholesale. `test_secret_keys_are_a_subset_of_infra_keys` also requires the
`INFRA_KEYS` entry.

`VALIDSIM_WEBHOOK_URLS` stays **out** of `SECRET_KEYS` on purpose: it is
classified `infra` (correct — a committed file must never pin a destination)
but the notify health summary withholds URLs entirely, because Slack and most
providers carry the secret token inside the URL *path*, so masking the value
would be pointless and printing it would leak it.

---

## About the `[notify]` table in `project_config.py`

You asked whether to wire it or say why not. **Why not, and it is deliberate:**

`_KNOWN_TOP_LEVEL` accepts a `notify` table (line 153), but v1 config files are
**policy-only** and `resolve_config` *rejects* any env-named key outright
(`test_infra_key_in_a_config_file_is_rejected_not_ignored`). A webhook URL is
deployment infrastructure, not a team's validation policy — it names a host
and usually embeds a secret token in its path. Putting it in a file committed
to git is the exact failure the file's own docstring warns about
("a committed DSN in git"). `INFRA_KEYS` puts these vars in the environment
half for that same reason.

So the `[notify]` table stays a **rejected** section for now: `_KNOWN_TOP_LEVEL`
lists it so the error message names it as a known section rather than an
unknown one, but writing a real config with it fails loudly instead of
appearing to work. Accepting it and ignoring it would be the worst outcome —
a committed file that looks wired and is not. If a future release wants
`[notify]`, the honest version is a non-secret *routing policy only*
(`min_severity`, `format`) with URLs still required from the environment.
