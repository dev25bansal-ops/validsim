"""Async validation job queue backed by memory or Redis.

:class:`JobQueue` is the thread-safe in-memory default; :class:`RedisJobQueue`
subclasses it and persists the same records to Redis so multiple processes (the
API and a future GPU worker) can share one queue. The two backends expose an
identical interface (enqueue/get/list/update_status/__len__/close) so callers
are backend-agnostic.

Redis is treated exactly like the optional PostgreSQL store: the driver is
imported lazily, never at module level, so importing this module always
succeeds and a missing driver surfaces as an actionable ``RuntimeError``
("pip install redis") at first use. The client connection is also opened
lazily, so :func:`create_job_queue` returns a configured queue without
touching the network.

Redis serialization rules:

* Every job is a single JSON string under a plain, parameter-free key
  (``validsim:jobs:<run_id>``) — no URL/query parameters are ever baked into
  keys, and job payloads never travel through Redis hashes or pickles.
* An insertion-order index is kept as a Redis list (``validsim:jobs:index``)
  of run ids, preserving FIFO semantics for :meth:`RedisJobQueue.list`.

Claim leases:

* :meth:`JobQueue.claim_next` stamps each claim with a deadline and increments
  a fencing epoch, so a job whose worker died is reclaimable instead of
  stranded in ``running`` forever.
* :meth:`JobQueue.renew_lease` is a compare-and-set on that epoch: a worker
  that stalled past its lease cannot renew or complete a job another worker
  has since claimed. :class:`~validsim.jobs.worker.JobWorker` heartbeats while
  it executes.
* :meth:`JobQueue.reap_expired` returns expired claims to ``queued``. The
  pipeline is deterministic and the result upsert is idempotent, so reclaiming
  is safe whereas leaving a job stranded is not.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar

from validsim.jobs.models import TERMINAL_STATUSES, JobRecord, JobSpec, JobStatus
from validsim.logging import get_logger

__all__ = [
    "JobQueue",
    "QueueContentionError",
    "QueueFullError",
    "RedisJobQueue",
    "create_job_queue",
]

_logger = get_logger("jobs.queue")

#: Env var selecting the backend ("memory" | "redis"); defaults to memory.
_QUEUE_ENV = "VALIDSIM_JOB_QUEUE"
#: Env var supplying the Redis URL (e.g. ``redis://localhost:6379/0``).
_REDIS_URL_ENV = "VALIDSIM_REDIS_URL"
#: Env var overriding the default queue depth cap (a positive integer).
_MAX_DEPTH_ENV = "VALIDSIM_JOB_QUEUE_MAX_DEPTH"
#: Env var overriding how long a worker's claim on a job stays valid.
_LEASE_ENV = "VALIDSIM_JOB_LEASE_SECONDS"
#: Env var overriding how many times a failing job is retried.
_MAX_ATTEMPTS_ENV = "VALIDSIM_JOB_MAX_ATTEMPTS"
#: Env var overriding the first retry delay, in seconds.
_RETRY_BACKOFF_ENV = "VALIDSIM_JOB_RETRY_BACKOFF_SECONDS"

#: Default maximum number of jobs the queue holds before rejecting new work.
#: Bounds the otherwise-unbounded growth flagged by audit H3. Overridable
#: per-instance via the ``max_depth`` argument or process-wide via
#: ``VALIDSIM_JOB_QUEUE_MAX_DEPTH``.
_DEFAULT_MAX_DEPTH = 1000

#: Default number of times a job may be attempted before it is dead-lettered.
#:
#: Without a bound the retry policy was inverted: a worker exception was terminal
#: on the very first attempt (a transient blip became a permanent failure), while
#: a *dead worker* had its job requeued forever with no bound and no signal (a
#: job that could never succeed looped until someone restarted the process).
#: Three attempts absorbs the realistic transient faults — a briefly unreachable
#: store, a racing deploy, an OOM-killed sibling — without letting a poison
#: checkpoint churn. Overridable per-instance via ``max_attempts`` or
#: process-wide via ``VALIDSIM_JOB_MAX_ATTEMPTS``.
_DEFAULT_MAX_ATTEMPTS = 3

#: Default first retry delay, in seconds, doubling on each further attempt.
#:
#: A retry with no delay turns a persistently failing backend into a hot spin:
#: the worker re-claims, re-runs, re-fails and repeats as fast as the machine
#: allows, burning the whole attempt budget in milliseconds and drowning every
#: other job in the queue. Overridable per-instance via ``retry_backoff`` or
#: process-wide via ``VALIDSIM_JOB_RETRY_BACKOFF_SECONDS``.
_DEFAULT_RETRY_BACKOFF_SECONDS = 5.0

#: Ceiling on the doubling backoff, so ``max_attempts`` cannot grow it without
#: bound. Long enough to be a real pause, short enough that a queue-wide
#: incident does not leave work cooling down for hours.
_MAX_RETRY_BACKOFF_SECONDS = 300.0

#: How many times the reaper may take a job back from a worker that died holding
#: it, before the job is dead-lettered instead.
#:
#: This is a *separate* budget from :data:`_DEFAULT_MAX_ATTEMPTS` on purpose. A
#: worker that dies mid-job never increments ``attempt`` — it is gone before it
#: can report anything — so bounding only attempts leaves the most common
#: failure mode (OOM-killed GPU process, unmounted volume) completely unbounded
#: and unobservable. Overridable per-instance via ``max_reclaims`` or
#: process-wide via ``VALIDSIM_JOB_MAX_RECLAIMS``.
_DEFAULT_MAX_RECLAIMS = 3
_MAX_RECLAIMS_ENV = "VALIDSIM_JOB_MAX_RECLAIMS"

#: Module-level mirror of :attr:`JobQueue.max_reclaims`, so a caller that has
#: not built a queue (a test asserting the budget is a real bound, a diagnostic
#: script) can still read the shipped default without instantiating one.
_MAX_RECLAIMS = _DEFAULT_MAX_RECLAIMS

#: Namespace prefix for all Redis keys owned by this queue.
_KEY_PREFIX = "validsim:jobs"

#: Bounded retries for a claim attempt that finds the payload missing or
#: unparseable. ``LPOP`` has already removed the id from the ready list, so an
#: unconditional drop would lose the job, while an unconditional requeue would
#: spin a polling worker hot forever. Three tries, then the entry is abandoned.
_CORRUPT_CLAIM_ATTEMPTS = 3
_CORRUPT_CLAIM_TTL_SECONDS = 300

#: Suffix of the Redis key holding the per-job claim-attempt counter for a
#: payload that was missing (id present, record absent) or unreadable bytes.
_CLAIM_ATTEMPT_SUFFIX = ":claim-attempts"

#: Default lifetime of a worker's claim, in seconds. Deliberately far longer
#: than any realistic run: the worker heartbeats at a third of this interval,
#: so the deadline only fires for a worker that actually died, and a merely slow
#: job is renewed rather than stolen. Overridable per-instance via
#: ``lease_seconds`` or process-wide via :data:`_LEASE_ENV`.
_DEFAULT_LEASE_SECONDS = 3600

#: Pop, validate, stamp the lease, and update the oldest ready job in one server
#: operation. ARGV: payload-key prefix (1), claim timestamp (2), max corrupt-claim
#: attempts (3), corrupt-claim TTL seconds (4), lease deadline (5), max attempts
#: (6), claim-attempt key suffix (7), max reclaims (8).
#:
#: ``LPOP`` has already removed the id by the time the payload is read, so every
#: failure branch below is a *choice* and each one is bounded:
#:
#: * **payload absent, or not readable bytes / not a JSON table** — requeued up
#:   to ``ARGV[3]`` times, then dropped. An unconditional requeue would spin a
#:   polling worker forever on a transiently missing key; an unconditional drop
#:   would lose a real job. There is no record to mark here, so a dead-letter
#:   state is unavailable, but the attempt is published on
#:   ``validsim:jobs:claim-failed`` so it stays observable.
#: * **payload is a table but this build cannot decode it** — quarantined
#:   immediately. This is the case the bounded requeue handles badly: the JSON is
#:   valid, so requeueing just makes every subsequent poll fail the same way, and
#:   a worker that somehow received it would crash on a missing attribute and
#:   misreport the damage as a *job* failure. Marking it ``dead`` takes it out of
#:   the ready list (no spin), out of the worker's hands (no crash) and keeps it
#:   visible for inspection.
#: * **record already claimed/finished** — dropped as a stale duplicate entry.
#: * **budget spent** — dead-lettered rather than started, so a job that has
#:   burned through its retries cannot be picked up again.
#:
#: A record left ``running`` with a dead lease is reclaimed by
#: :data:`_REDIS_REAP_EXPIRED_SCRIPT`, which owns the ``reclaim_count`` budget
#: and the matching dead-letter transition.

#: How many ready-list heads a single ``claim_next`` call may examine before
#: reporting no work. The in-memory backend scans every record, so a cooling or
#: already-settled entry never stops it finding a claimable job behind it; this
#: bound is what keeps the Redis equivalent from becoming an O(n) walk on a list
#: where nothing is ready. 64 is far above any real backlog and still bounds a
#: single server-side operation.
#:
#: Exceeding it loses nothing: every id examined was either claimed or already
#: re-queued/settled by the script, so the next call simply carries on.
_CLAIM_SCAN_BUDGET = 64
_REDIS_CLAIM_NEXT_SCRIPT = """
local max_claim_attempts = tonumber(ARGV[3])

-- Days from 1970-01-01 for a proleptic-Gregorian date (Howard Hinnant's
-- days_from_civil). Redis embeds Lua 5.1, which has no // operator, so the
-- flooring is done through math.floor.
local function days_from_civil(y, m, d)
  if m < 1 or m > 12 or d < 1 or d > 31 then
    return nil
  end
  y = y - (m <= 2 and 1 or 0)
  local era = math.floor((y >= 0 and y or y - 399) / 400)
  local yoe = y - era * 400
  local doy = math.floor((153 * (m + (m > 2 and -3 or 9)) + 2) / 5) + d - 1
  local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
  return era * 146097 + doe - 719468
end
-- Offset-aware ISO-8601 -> epoch seconds, or nil when the shape is unusable.
local function to_epoch(ts)
  if type(ts) ~= 'string' then
    return nil
  end
  local y, mo, d, h, mi, s, sign, oh, om = string.match(
    ts, '^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d):(%d%d)([+-])(%d%d):(%d%d)$')
  if not y then
    return nil
  end
  local days = days_from_civil(tonumber(y), tonumber(mo), tonumber(d))
  if not days then
    return nil
  end
  local offset = tonumber(oh) * 3600 + tonumber(om) * 60
  local secs = days * 86400 + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
  if sign == '-' then
    -- The text names a local time, so the instant is that much *later* in UTC.
    return secs + offset
  end
  return secs - offset
end

-- Try to claim one id. Returns the updated record on success, or nil when the
-- id is not claimable *this pass* -- in which case it has already been dealt
-- with (re-queued, settled terminally, or dropped as a stale entry) and the
-- caller should move on to the next candidate rather than give up.
local function attempt_claim(job_id)
  local job_key = ARGV[1] .. job_id
  local claim_attempts_key = job_key .. ARGV[7]
  local payload = redis.call('GET', job_key)

  -- Bounded requeue of an id whose payload cannot be used, then give up.
  local function requeue_or_drop(reason)
    local attempts = tonumber(redis.call('GET', claim_attempts_key) or '0')
    if attempts < max_claim_attempts then
      redis.call('INCR', claim_attempts_key)
      redis.call('EXPIRE', claim_attempts_key, tonumber(ARGV[4]))
      redis.call('RPUSH', KEYS[1], job_id)
    else
      redis.call('DEL', claim_attempts_key)
    end
    redis.call('PUBLISH', 'validsim:jobs:claim-failed', job_id .. ' ' .. reason)
  end

  if not payload then
    requeue_or_drop('missing-payload')
    return nil
  end
  local ok, record = pcall(cjson.decode, payload)
  if ok and type(record) ~= 'table' then
    -- A valid JSON scalar decodes without error but is not a record;
    -- indexing it would abort this script after LPOP already lost the id.
    ok = false
  end
  if not ok then
    requeue_or_drop('unparseable-payload')
    return nil
  end
  if record.status == 'dead' or record.status == 'done' or record.status == 'failed' then
    -- Already terminal: drop the stale list entry, it is not lost work.
    return nil
  end
  if record.status ~= 'queued' then
    -- Already claimed: a duplicate ready entry, not lost work either.
    return nil
  end

  -- A requeued job is pushed back onto the ready list the moment its attempt
  -- fails, but its backoff has not necessarily elapsed yet. Claiming it early
  -- would hot-spin a failing backend and burn the whole attempt budget in
  -- milliseconds, which is the exact failure the backoff exists to prevent --
  -- the in-memory backend skips a cooling job, and this is the same rule.
  -- The id goes back on the *tail*, so every job queued before it is still
  -- claimable in this pass and ordering stays fair.
  local now_secs = to_epoch(ARGV[2])
  if now_secs and type(record.retry_after) == 'string' then
    local ready_at = to_epoch(record.retry_after)
    if ready_at and ready_at > now_secs then
      redis.call('RPUSH', KEYS[1], job_id)
      return nil
    end
  end

  -- Retry budgets, checked at claim time so a job that has spent either one is
  -- settled here rather than handed to a worker that is bound to fail again.
  local max_attempts = tonumber(ARGV[6])
  local max_reclaims = tonumber(ARGV[8])
  local function dead_letter(reason)
    record.status = 'dead'
    record.finished_at = ARGV[2]
    record.lease_expires_at = cjson.null
    record.retry_after = cjson.null
    record.error = reason
    redis.call('SET', job_key, cjson.encode(record))
    redis.call('PUBLISH', 'validsim:jobs:dead-letter', job_id .. ' ' .. reason)
  end
  -- Does this payload actually satisfy JobRecord.from_dict on the client? A table
  -- with a JSON-valid but structurally wrong spec (a record written by a
  -- different release, a hand-edited key, a truncated spec) decodes fine here and
  -- would then raise KeyError inside the worker -- where the failure is blamed on
  -- the *job*, while this poisonous entry stays in the ready list to fail every
  -- later claim too. Checking the contract server-side quarantines it once.
  local spec = record.spec
  if type(spec) ~= 'table' or type(spec.run_id) ~= 'string'
      or type(spec.checkpoint_id) ~= 'string' or type(spec.task_id) ~= 'string' then
    dead_letter('unreadable job record: spec is missing required fields')
    return nil
  end
  if max_attempts and max_attempts > 0
      and (tonumber(record.attempt) or 0) >= max_attempts then
    dead_letter('attempt budget exhausted before claim')
    return nil
  end
  if max_reclaims and max_reclaims > 0
      and (tonumber(record.reclaim_count) or 0) > max_reclaims then
    dead_letter('reclaim budget exhausted before claim')
    return nil
  end

  record.status = 'running'
  if not record.started_at or record.started_at == cjson.null then
    record.started_at = ARGV[2]
  end
  -- Every claim starts an attempt; the reaper's reclaim is counted separately
  -- because a worker that dies never gets to increment this itself.
  record.attempt = (tonumber(record.attempt) or 0) + 1
  -- Fencing token: a worker whose lease was already reclaimed carries a stale
  -- epoch, so it can neither renew nor complete the job a new worker now holds.
  record.lease_epoch = (tonumber(record.lease_epoch) or 0) + 1
  record.lease_expires_at = ARGV[5]
  -- The retry backoff has elapsed by definition (the job is claimable), so clear
  -- it rather than leaving a stale instant on a running record.
  record.retry_after = cjson.null
  local updated = cjson.encode(record)
  redis.call('SET', job_key, updated)
  return updated
end

-- How many heads to examine in one call. The in-memory backend scans every
-- record and returns the first claimable one, so a cooling job at the head is
-- skipped rather than obeyed. Popping exactly one id and reporting "no work"
-- whenever that single id turned out to be cooling made this queue strictly
-- weaker: with a ready list of [cooling, cooling, fresh] it answered nil on the
-- first two polls while a claimable job sat one position behind, and a poller
-- that stops on nil simply stalled. The budget stops a pathological all-cooling
-- list from turning one call into an unbounded walk, and is compared before each
-- pop so a list shorter than the budget cannot spin.
local scan_budget = tonumber(ARGV[9]) or 64
for _scan = 1, scan_budget do
  local job_id = redis.call('LPOP', KEYS[1])
  if not job_id then
    return nil
  end
  local updated = attempt_claim(job_id)
  if updated then
    return updated
  end
end
-- Every head examined was unclaimable. None is republished here: each rejection
-- already re-queued its own id, or settled it terminally, so doing it again
-- would duplicate entries on the ready list.
return nil
"""

#: Extend a still-running worker's lease, but only while its fencing epoch
#: matches. ARGV: expected epoch, new deadline.
_REDIS_RENEW_LEASE_SCRIPT = """
local payload = redis.call('GET', KEYS[1])
if not payload then
  return nil
end
local ok, record = pcall(cjson.decode, payload)
if ok and type(record) ~= 'table' then
  -- A valid JSON scalar decodes without error but is not a record;
  -- indexing it would abort this script after LPOP already lost the id.
  ok = false
end
if not ok then
  return nil
end
if record.status ~= 'running' then
  return nil
end
if (tonumber(record.lease_epoch) or 0) ~= tonumber(ARGV[1]) then
  return nil
end
record.lease_expires_at = ARGV[2]
local updated = cjson.encode(record)
redis.call('SET', KEYS[1], updated)
return updated
"""

#: Return every job whose running lease has expired to the ready list, so a
#: dead worker's claim is recovered instead of stranding the job forever. The
#: queue depth cap is rechecked here because a reclaimed job occupies capacity
#: again, and the insertion index is never touched so FIFO listing still works.
#: ARGV: payload-key prefix, current UTC timestamp, max depth, and the same
#: instant as an epoch-seconds integer.
#:
#: Expiry is decided on the *instant*, never on the text. Lua has no date type,
#: so the obvious ``expires <= now`` compares ISO-8601 strings, and that is only
#: equivalent to chronological order while every timestamp shares one UTC
#: offset. One mixed-offset writer breaks it in both directions at once: a
#: deadline written ``T09:00:00-05:00`` reads earlier than ``T12:00:00+00:00`` and
#: so reaps a lease that is still live for hours, while ``T19:00:00+09:00``
#: (already two hours past) never reaps and strands the job in ``running``
#: forever. :func:`_utc_now` is the only writer today so the offsets happen to
#: agree, but nothing enforces that -- a local-time writer, a payload replayed
#: from a differently-configured process, or a hand-edited record trips it.
#:
#: Both sides are therefore reduced to epoch-seconds integers first, so the
#: comparison is arithmetic and cannot depend on how either string is spelled.
#: Python supplies the authoritative ``now`` in integer form (ARGV[4]) and the
#: script only has to parse each record's own deadline, using
#: :func:`_utc_now` on that same seam. A missing, non-numeric or ``cjson.null``
#: deadline counts as expired, matching the in-memory :func:`_is_expired`: a
#: ``running`` record with no usable lease predates the lease feature and must not
#: stay unreclaimable.
#:
#: Two details in the parser are load-bearing. ``string.match`` (rather than
#: ``string.find``) anchors the whole pattern, so ``10-01-01T00:00:00Z`` cannot
#: be read as a day, and the parse runs inside ``pcall`` so one malformed
#: record reclaims instead of aborting the script -- which would leave every
#: *later* job in the index stranded in ``running``.
#:
#: A job that is reclaimed more times than the reclaim budget allows is
#: *dead-lettered* rather than requeued, and the transition is published on
#: ``validsim:jobs:dead-letter``. That publish is the only signal the failure
#: mode had: a worker that dies mid-job never gets to record anything itself, so
#: without it a job that could never finish looked exactly like one that was
#: merely slow.
_REDIS_REAP_EXPIRED_SCRIPT = """
local prefix = ARGV[1]
local now = ARGV[2]
local max_depth = tonumber(ARGV[3])
local now_epoch = tonumber(ARGV[4])
local max_reclaims = tonumber(ARGV[5])
-- Days from 1970-01-01 for a proleptic-Gregorian date (Howard Hinnant's
-- days_from_civil). Redis embeds Lua 5.1, which has no ``//`` operator, so the
-- flooring is done through math.floor.
local function days_from_civil(y, m, d)
  if m < 1 or m > 12 or d < 1 or d > 31 then
    return nil
  end
  y = y - (m <= 2 and 1 or 0)
  local era = math.floor((y >= 0 and y or y - 399) / 400)
  local yoe = y - era * 400
  local doy = math.floor((153 * (m + (m > 2 and -3 or 9)) + 2) / 5) + d - 1
  local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
  return era * 146097 + doe - 719468
end
-- Offset-aware ISO-8601 -> epoch seconds, or nil when the shape is unusable.
local function to_epoch(ts)
  if type(ts) ~= 'string' then
    return nil
  end
  local y, mo, d, h, mi, s, sign, oh, om = string.match(
    ts, '^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d):(%d%d)([+-])(%d%d):(%d%d)$')
  if not y then
    return nil
  end
  local days = days_from_civil(tonumber(y), tonumber(mo), tonumber(d))
  if not days then
    return nil
  end
  local offset = tonumber(oh) * 3600 + tonumber(om) * 60
  local secs = days * 86400 + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
  if sign == '-' then
    -- The text names a local time, so the instant is that much *later* in UTC.
    return secs + offset
  end
  return secs - offset
end
local now_secs = tonumber(ARGV[4])
if not now_secs then
  -- Fail closed, never open: an unusable `now` reclaims nothing rather than
  -- handing every running job to a second worker at once.
  return {}
end
local job_ids = redis.call('LRANGE', KEYS[1], 0, -1)
-- One flat reply, with dead-lettered ids tagged by a prefix. A nested table
-- would be more natural, but the Redis protocol only converts the *top* level
-- of a Lua reply: nested tables come back as their stringified form, so the
-- caller would have to parse them out of a blob. A flat, self-describing array
-- survives every RESP version unchanged.
local reaped = {}
for i, job_id in ipairs(job_ids) do
  local payload = redis.call('GET', prefix .. job_id)
  if payload then
    local ok, record = pcall(cjson.decode, payload)
    if ok and type(record) == 'table' and record.status == 'running' then
      local parsed, expires = pcall(to_epoch, record.lease_expires_at)
      if not parsed or not expires or expires <= now_secs then
        local reclaims = (tonumber(record.reclaim_count) or 0) + 1
        if max_reclaims and max_reclaims > 0 and reclaims > max_reclaims then
          -- Budget spent: the worker holding this job keeps dying. Requeueing
          -- again would loop forever, so settle it terminally and say so.
          record.status = 'dead'
          record.finished_at = now
          record.lease_expires_at = cjson.null
          record.reclaim_count = reclaims
          record.error = 'reclaim budget exhausted after '
            .. tostring(reclaims - 1) .. ' abandoned claims'
          redis.call('SET', prefix .. job_id, cjson.encode(record))
          redis.call('PUBLISH', 'validsim:jobs:dead-letter',
            job_id .. ' reclaim-budget-exhausted')
          table.insert(reaped, '!' .. job_id)
        elseif redis.call('LLEN', KEYS[2]) < max_depth then
          record.status = 'queued'
          record.lease_expires_at = cjson.null
          record.reclaim_count = reclaims
          record.retry_after = cjson.null
          redis.call('SET', prefix .. job_id, cjson.encode(record))
          redis.call('RPUSH', KEYS[2], job_id)
          table.insert(reaped, job_id)
        end
      end
    end
  end
end
return reaped
"""

#: Fenced terminal write: complete a running job only while its fencing epoch
#: still matches, so a worker whose lease was already reclaimed cannot
#: overwrite the claim of the worker that took over. ARGV: expected epoch,
#: target status, finished-at timestamp, error-or-result payload, job id.
_REDIS_FINISH_SCRIPT = """
local payload = redis.call('GET', KEYS[1])
if not payload then
  return nil
end
local ok, record = pcall(cjson.decode, payload)
if ok and type(record) ~= 'table' then
  ok = false
end
if not ok then
  return nil
end
if record.status ~= 'running' then
  return nil
end
if (tonumber(record.lease_epoch) or 0) ~= tonumber(ARGV[1]) then
  return nil
end
record.status = ARGV[2]
record.finished_at = ARGV[3]
record.lease_expires_at = cjson.null
record.retry_after = cjson.null
if ARGV[2] == 'failed' or ARGV[2] == 'dead' then
  -- Both carry a reason rather than a result pointer. Kept apart from `done`
  -- because a dead-lettered job produced no artifact at all.
  record.error = ARGV[4]
  record.result = cjson.null
else
  record.result = ARGV[4]
end
if ARGV[2] == 'dead' then
  redis.call('PUBLISH', 'validsim:jobs:dead-letter', ARGV[5] .. ' ' .. ARGV[4])
end
local updated = cjson.encode(record)
redis.call('SET', KEYS[1], updated)
return updated
"""

#: Compare-and-set for an unfenced status write: commit the new payload only
#: while the stored bytes still match what the caller read, otherwise hand the
#: caller back the record that beat it.
#:
#: The fenced and queued paths are atomic because the server does the read, the
#: decision and the write in one script. An unfenced transition cannot use
#: either of them -- :data:`_REDIS_FINISH_SCRIPT` insists the job is `running`
#: and that the epoch matches, but a caller with no claim has neither -- yet a
#: client-side GET-then-SET loses whichever write arrives second, and because
#: the whole record is rewritten from the stale read, it rewinds fields the
#: winner had just advanced. A reclaimed job's `lease_epoch` goes back to the
#: dead owner's value, so the new owner's fenced completion is then rejected by
#: its own token and its result is never recorded.
#:
#: Byte equality is enough to detect the interference, and it keeps the
#: lifecycle rules in one place: the caller still runs :func:`_apply_status`,
#: so this backend and the in-memory one cannot drift into different
#: interpretations of a transition.
#:
#: ARGV: the payload the caller read, the payload it wants to store.
#: Returns the stored payload on success, the competing payload on a lost race,
#: nil when the job is gone.
_REDIS_CAS_SET_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return nil
end
if current ~= ARGV[1] then
  return current
end
redis.call('SET', KEYS[1], ARGV[2])
return ARGV[2]
"""

#: Attempts an unfenced status write gets to out-pace other processes before it
#: gives up. Each retry re-reads and re-applies the transition, so a loss is a
#: symptom of sustained contention rather than a one-off coincidence; failing
#: loudly at that point beats rewriting a record two writers are fighting over.
_UNFENCED_WRITE_ATTEMPTS = 8

#: Atomically reject duplicates/full queues and commit the payload, insertion
#: index, and ready list. An existing key wins over the capacity check.
#:
#: The capacity check reads the *ready* list (KEYS[3]) — the unclaimed backlog —
#: not the insertion index (KEYS[2]). The index retains every job id ever
#: enqueued and is never popped, so checking it made the cap count all-time
#: history: once ``max_depth`` lifetime jobs had passed, enqueue rejected
#: forever even with an empty backlog, dead-locking a long-lived queue. The
#: reaper (:data:`_REDIS_REAP_EXPIRED_SCRIPT`) already measures capacity against
#: the ready list, so this also makes the two consistent.
_REDIS_ENQUEUE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return -1
end
if redis.call('LLEN', KEYS[3]) >= tonumber(ARGV[2]) then
  return -2
end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('RPUSH', KEYS[2], ARGV[3])
redis.call('RPUSH', KEYS[3], ARGV[3])
return 1
"""


#: Fenced return-to-queue: hand a running job back to queued with a retry
#: backoff, *and* put its id back on the ready list in the same server-side
#: operation. Setting the status alone is not enough on this backend: the ready
#: list is the only thing the claim script reads, so a job marked queued but
#: never re-listed is invisible forever -- it can never be claimed again, never
#: reaped (the reaper only considers running jobs) and never dead-lettered,
#: while the API keeps reporting it as pending work.
#:
#: Doing the write in Lua also makes the requeue atomic across processes, which
#: a client-side GET-then-SET cannot be: a router-side transition racing a
#: worker's fenced completion would otherwise silently lose one of the two.
#:
#: The epoch check is the same fencing the terminal write uses, so a worker
#: whose lease was already reclaimed cannot push the new owner's job back into
#: the ready list and start a third concurrent run.
#:
#: ARGV: expected lease epoch, job id, retry-after instant.
#: Return a job to queued and put its id back on the ready list in one
#: server-side operation. Setting the status alone is not enough on this
#: backend: the ready list is the only thing the claim script reads, so a job
#: marked queued but never re-listed is invisible forever -- it can never be
#: claimed again, never reaped (the reaper only considers running jobs) and
#: never dead-lettered, while the API keeps reporting it as pending work.
#:
#: Every route to a claimable job goes through this one script -- the worker
#: retry and any caller of update_status(..., QUEUED) -- so the two cannot
#: drift apart again, which is how the retry path was able to strand work
#: while the status field still read queued.
#:
#: Lua is also what makes the write atomic across processes, which a
#: client-side GET-then-SET cannot be: a router-side transition racing a
#: fenced completion would otherwise silently lose one of the two writes.
#: And it is why a queued transition must not go through the terminal-write
#: script, which stamps finished_at and discards retry_after whatever status
#: it is handed.
#:
#: When ARGV[1] names an epoch the transition is a compare-and-set against it,
#: so a worker whose lease was already reclaimed cannot push the job of the
#: new owner back onto the ready list and start a third concurrent run. An
#: empty ARGV[1] is the unfenced path, for a caller that holds no claim.
#:
#: ARGV: expected lease epoch (empty when unfenced), job id, retry-after
#: instant (empty to clear the backoff).
_REDIS_SET_QUEUED_SCRIPT = """
local payload = redis.call('GET', KEYS[1])
if not payload then
  return nil
end
local ok, record = pcall(cjson.decode, payload)
if not ok or type(record) ~= 'table' then
  return nil
end
if ARGV[1] ~= '' then
  if record.status ~= 'running'
      or (tonumber(record.lease_epoch) or 0) ~= tonumber(ARGV[1]) then
    return nil
  end
end
record.status = 'queued'
record.lease_expires_at = cjson.null
if ARGV[3] == '' then
  record.retry_after = cjson.null
else
  record.retry_after = ARGV[3]
end
local updated = cjson.encode(record)
redis.call('SET', KEYS[1], updated)
redis.call('RPUSH', KEYS[2], ARGV[2])
return updated
"""



class QueueFullError(Exception):
    """Raised by :meth:`JobQueue.enqueue` when the queue is at ``max_depth``.

    A distinct exception type so callers (e.g. the HTTP router) can map a full
    queue to a specific response (such as ``503 Service Unavailable``) without
    confusing it with the ``ValueError`` raised for a duplicate ``run_id``.
    """


class QueueContentionError(Exception):
    """Raised when an unfenced status write never gets to commit.

    Another process changed the record on every one of the
    :data:`_UNFENCED_WRITE_ATTEMPTS` tries, so the transition could not be
    applied without rewinding that writer's fields. Reporting it beats the old
    behaviour of writing the stale record anyway.
    """


def _utc_now() -> str:
    """Current UTC time as a second-precision ISO-8601 string.

    Every queue timestamp is minted here, which is what keeps the stored strings
    on one offset. That is a convenience, not a correctness requirement: the
    Redis reaper decides expiry on epoch seconds
    (:data:`_REDIS_REAP_EXPIRED_SCRIPT`), so a record written in any other
    offset is still compared correctly, and the in-memory reaper parses instants
    too (:func:`_is_expired`). A non-UTC writer is therefore no longer a
    correctness landmine -- but keeping one writer is still the simpler habit.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch_seconds(timestamp: str) -> int:
    """Seconds since the Unix epoch for an ISO-8601 ``timestamp``.

    The offset-aware counterpart of :func:`_utc_now`, used to hand the Redis
    reaper an integer it can compare arithmetically. Fractional seconds are
    truncated, matching the second-precision strings the queue stores.

    Args:
        timestamp: An ISO-8601 string, with or without a UTC offset.

    Returns:
        Whole seconds since 1970-01-01T00:00:00Z. A naive string (no offset) is
        read as UTC, which is how every string the queue itself writes is
        spelled.

    Raises:
        ValueError: If ``timestamp`` is not ISO-8601. Callers that must not fail
            use :func:`_is_expired`, which folds this into a boolean.
    """
    moment = datetime.fromisoformat(timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _lease_deadline(base: str, seconds: int) -> str:
    """Return the ISO-8601 UTC deadline ``seconds`` after ``base``.

    ``base`` is an existing timestamp rather than a fresh clock read so every
    deadline stays on the same :func:`_utc_now` seam the rest of the queue
    uses, keeping claim and renewal consistent within one instant.
    """
    return _plus_seconds(base, seconds)


def _plus_seconds(base: str, seconds: float) -> str:
    """Return ``base`` shifted forward by ``seconds``, as ISO-8601.

    The one place an instant is advanced, shared by lease deadlines and by the
    retry backoff so both are computed the same way from the same seam.
    """
    moment = datetime.fromisoformat(base) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds")


def _is_expired(lease_expires_at: str | None, now: str) -> bool:
    """Whether a lease deadline has passed.

    An absent or unparseable deadline counts as expired: a ``running`` record
    carrying no lease predates this feature and must not stay unreclaimable.
    Comparison is on parsed instants, not strings, so the two timestamps need
    not share a textual offset.
    """
    if not lease_expires_at:
        return True
    try:
        return datetime.fromisoformat(lease_expires_at) <= datetime.fromisoformat(now)
    except ValueError:
        return True


def _resolve_lease_seconds(explicit: int | None) -> int:
    """Resolve the effective lease lifetime for a freshly built queue.

    Precedence mirrors :func:`_resolve_max_depth`: an explicit ``lease_seconds``
    argument wins, then :data:`_LEASE_ENV`, then :data:`_DEFAULT_LEASE_SECONDS`.

    Args:
        explicit: Caller-supplied lifetime, or ``None`` to defer to env/default.

    Returns:
        A positive integer number of seconds.

    Raises:
        ValueError: If the resolved value is not a positive integer.
    """
    return _resolve_positive_int(
        explicit, _LEASE_ENV, _DEFAULT_LEASE_SECONDS, "lease_seconds"
    )


def _resolve_positive_int(explicit: Any, env_var: str, default: int, label: str) -> int:
    """Resolve a positive-integer setting from an argument, env var, or default.

    Shared by the depth cap, the lease lifetime, the attempt budget and the
    reclaim budget so all four reject the same shapes the same way: a missing or
    blank value falls back, anything non-numeric or non-positive is an error
    rather than a silent default (a mistyped ``VALIDSIM_JOB_MAX_ATTEMPTS=0``
    that quietly meant "three" would be worse than refusing to start).

    Args:
        explicit: Caller-supplied value, or ``None`` to consult ``env_var``.
        env_var: Environment variable consulted when ``explicit`` is ``None``.
        default: Value used when neither supplies one.
        label: Human-readable setting name used in error messages.

    Returns:
        A positive integer.

    Raises:
        ValueError: If the resolved value is not a positive integer.
    """
    if explicit is None:
        raw = (os.environ.get(env_var) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{env_var} must be a positive integer, got {raw!r}"
            ) from exc
    else:
        try:
            value = int(explicit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{label} must be a positive integer, got {explicit!r}"
            ) from exc
    if value < 1:
        raise ValueError(f"{label} must be a positive integer, got {value!r}")
    return value


def _resolve_retry_backoff(explicit: float | None) -> float:
    """Resolve the first retry delay in seconds.

    Unlike the integer settings this one accepts ``0``: a deployment that wants
    immediate retries (tests, a deliberate fast-fail on a poison job) should not
    have to lie about a positive value. Negative delays are rejected, since they
    would place the retry instant in the past and make the backoff meaningless.
    """
    if explicit is None:
        raw = (os.environ.get(_RETRY_BACKOFF_ENV) or "").strip()
        if not raw:
            return _DEFAULT_RETRY_BACKOFF_SECONDS
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"{_RETRY_BACKOFF_ENV} must be a non-negative number, got {raw!r}"
            ) from exc
    else:
        try:
            value = float(explicit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"retry_backoff must be a non-negative number, got {explicit!r}"
            ) from exc
    if value < 0 or value != value:  # the second clause rejects NaN
        raise ValueError(f"retry_backoff must be a non-negative number, got {value!r}")
    return value


def _shifted_now(offset_seconds: float) -> str:
    """:func:`_utc_now` moved forward by ``offset_seconds``.

    The test seam for retry backoff: a job's cooldown is a real duration, so
    asserting that it is *not* claimable would otherwise mean sleeping through
    it. The offset is per-queue, so moving it never disturbs the timestamps
    another queue mints.
    """
    if not offset_seconds:
        return _utc_now()
    return (
        datetime.fromisoformat(_utc_now()) + timedelta(seconds=offset_seconds)
    ).isoformat(timespec="seconds")


def _retry_delay_seconds(attempt: int, base: float) -> float:
    """Exponential backoff for the attempt about to be retried.

    ``attempt`` is the number of attempts already started, so the first retry
    (after one failure) waits ``base`` seconds, the second ``2 * base``, and so
    on, capped at :data:`_MAX_RETRY_BACKOFF_SECONDS` so a large
    ``max_attempts`` cannot schedule work hours into the future.
    """
    if base <= 0:
        return 0.0
    exponent = max(0, int(attempt) - 1)
    return min(base * (2**exponent), _MAX_RETRY_BACKOFF_SECONDS)


def _report_dead_letter(job_id: str, reason: str, *, attempt: int, reclaims: int) -> None:
    """Emit the structured signal that a job will never run again.

    This is the whole point of the dead-letter state: a job that silently loops
    and a job that is quietly slow look identical from outside the process, so a
    terminal outcome has to be *announced* somewhere a metric or alert can read
    it. The log record carries ``validsim_job_dead_lettered`` as a structured
    field (rather than only prose) so it can be scraped without parsing the
    message, and the job id is in both the message and the field, because a
    dead-letter count is useless without knowing *which* jobs died.

    Args:
        job_id: The abandoned job's id.
        reason: Why it was abandoned (attempt budget, reclaim budget, ...).
        attempt: Attempts the job consumed.
        reclaims: Times the reaper took it back from a dead worker.
    """
    _logger.warning(
        "job %s dead-lettered: %s (attempts=%d, reclaims=%d)",
        job_id,
        reason,
        attempt,
        reclaims,
        extra={
            "validsim_job_dead_lettered": True,
            "validsim_job_id": job_id,
            "validsim_job_reason": reason,
            "validsim_job_attempts": attempt,
            "validsim_job_reclaims": reclaims,
        },
    )


def _resolve_max_depth(explicit: int | None) -> int:
    """Resolve the effective queue depth cap for a freshly built queue.

    Precedence: an explicit ``max_depth`` argument wins; otherwise the
    ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` environment variable is used when set;
    otherwise :data:`_DEFAULT_MAX_DEPTH` applies.

    Args:
        explicit: Caller-supplied cap, or ``None`` to defer to env/default.

    Returns:
        A positive integer depth cap.

    Raises:
        ValueError: If the resolved value is not a positive integer (an
            explicit non-numeric argument, or a malformed env var).
    """
    return _resolve_positive_int(explicit, _MAX_DEPTH_ENV, _DEFAULT_MAX_DEPTH, "max_depth")


def _import_redis() -> Any:
    """Import and return the redis-py driver module.

    The call is deliberately lazy (never at module import), mirroring the
    psycopg pattern used by the PostgreSQL store.

    Raises:
        RuntimeError: If the driver is not installed, with an actionable
            install hint. Importing this module never triggers the failure.
    """
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError(
            "The Redis job queue requires the redis-py driver, which is not "
            "installed. Install it with: pip install redis"
        ) from exc
    return redis


def _apply_status(
    record: JobRecord,
    status: JobStatus,
    error: str | None,
    result: str | None,
) -> JobRecord:
    """Return a new record reflecting the transition to ``status``.

    Timestamps follow lifecycle rules: ``started_at`` is set the first time a
    job becomes running, and ``finished_at`` is set on any terminal state.
    ``error``/``result`` are recorded only for failed-or-dead / done
    respectively. Any terminal transition releases the lease and clears the
    backoff, so a finished job can never be mistaken for abandoned work by the
    reaper nor be picked up again.
    """
    now = _utc_now()
    changes: dict[str, Any] = {"status": status}
    if status is JobStatus.RUNNING and record.started_at is None:
        changes["started_at"] = now
    if status in TERMINAL_STATUSES:
        changes["finished_at"] = now
        changes["lease_expires_at"] = None
        changes["retry_after"] = None
    if status in (JobStatus.FAILED, JobStatus.DEAD):
        changes["error"] = error
    if status is JobStatus.DONE:
        changes["result"] = result
    return dataclasses.replace(record, **changes)


class JobQueue:
    """Thread-safe in-memory job queue (the default backend).

    Jobs are keyed by their spec's ``run_id``; :meth:`list` returns them in
    insertion (FIFO) order. The number of jobs held is bounded by
    ``max_depth`` (default :data:`_DEFAULT_MAX_DEPTH`, env-overridable via
    ``VALIDSIM_JOB_QUEUE_MAX_DEPTH``): once the queue is at capacity,
    :meth:`enqueue` raises :class:`QueueFullError` instead of growing without
    limit (audit H3). The insertion-order dictionary is also the ready order,
    so :meth:`claim_next` can transition the first queued job while holding the
    same lock used by every other state operation.
    """

    def __init__(
        self,
        *,
        max_depth: int | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
        max_reclaims: int | None = None,
        retry_backoff: float | None = None,
    ) -> None:
        """Create an empty queue.

        Args:
            max_depth: Maximum number of jobs the queue will hold before
                rejecting new work. ``None`` (the default) defers to the
                ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` env var, then to
                :data:`_DEFAULT_MAX_DEPTH`. Must resolve to a positive integer.
            lease_seconds: How long a claim stays valid before :meth:`reap_expired`
                may reclaim the job. ``None`` defers to ``VALIDSIM_JOB_LEASE_SECONDS``,
                then to :data:`_DEFAULT_LEASE_SECONDS`. Must resolve to a positive
                integer.
            max_attempts: How many times a job may be attempted before it is
                dead-lettered. ``None`` defers to ``VALIDSIM_JOB_MAX_ATTEMPTS``,
                then to :data:`_DEFAULT_MAX_ATTEMPTS`. Must be a positive integer.
            max_reclaims: How many times :meth:`reap_expired` may take a job back
                from a worker that died holding it before it is dead-lettered.
                ``None`` defers to :data:`_MAX_RECLAIMS_ENV`, then to
                :data:`_DEFAULT_MAX_RECLAIMS`. Must be a positive integer.
            retry_backoff: First retry delay in seconds, doubling per attempt.
                ``None`` defers to :data:`_RETRY_BACKOFF_ENV`, then to
                :data:`_DEFAULT_RETRY_BACKOFF_SECONDS`. Must be non-negative.

        Raises:
            ValueError: If any resolved value is out of range.
        """
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._max_depth = _resolve_max_depth(max_depth)
        self._lease_seconds = _resolve_lease_seconds(lease_seconds)
        self._max_attempts = _resolve_positive_int(
            max_attempts, _MAX_ATTEMPTS_ENV, _DEFAULT_MAX_ATTEMPTS, "max_attempts"
        )
        self._max_reclaims = _resolve_positive_int(
            max_reclaims, _MAX_RECLAIMS_ENV, _DEFAULT_MAX_RECLAIMS, "max_reclaims"
        )
        self._retry_backoff = _resolve_retry_backoff(retry_backoff)
        #: Test-only clock offset (seconds) added to every :func:`_utc_now` read,
        #: so backoff expiry can be exercised without sleeping. Production code
        #: never sets it; :meth:`advance_clock` is the supported way to move it.
        self._clock_offset_seconds = 0

    @property
    def max_depth(self) -> int:
        """Maximum number of jobs this queue will hold before rejecting work."""
        return self._max_depth

    @property
    def lease_seconds(self) -> int:
        """Lifetime in seconds of a worker's claim before the job is reclaimable."""
        return self._lease_seconds

    @property
    def max_attempts(self) -> int:
        """How many times a job is attempted before it is dead-lettered."""
        return self._max_attempts

    @property
    def max_reclaims(self) -> int:
        """How many abandoned claims a job tolerates before it is dead-lettered."""
        return self._max_reclaims

    @property
    def retry_backoff(self) -> float:
        """First retry delay in seconds (doubles on each further attempt)."""
        return self._retry_backoff

    def advance_clock(self, seconds: float) -> float:
        """Shift this queue's clock forward by ``seconds`` (test seam).

        Retry backoff is measured against wall time, so testing that a job is
        *not* claimable during its cooldown would otherwise mean sleeping for
        the whole backoff. Advancing the queue's own clock keeps those tests
        instant while leaving the underlying :func:`_utc_now` seam untouched for
        every other queue.

        Args:
            seconds: Non-negative offset to add to every subsequent clock read.

        Returns:
            The new total offset.

        Raises:
            ValueError: If ``seconds`` is negative — the seam only moves forward,
                so a test cannot accidentally walk the clock backwards past a
                deadline it has already asserted on.
        """
        if seconds < 0:
            raise ValueError(f"clock only moves forward, got {seconds!r}")
        self._clock_offset_seconds += float(seconds)
        return self._clock_offset_seconds

    @staticmethod
    def new_run_id() -> str:
        """Generate a fresh job/run id like ``"vrun-1a2b3c4d"``."""
        return f"vrun-{uuid.uuid4().hex[:8]}"

    def enqueue(self, spec: JobSpec) -> JobRecord:
        """Add ``spec`` as a queued job and return its record.

        The ``max_depth`` cap bounds **pending work** (queued + running), not
        retained history: once the cap is reached, the queue rejects new work
        forever unless an operator deletes records by hand. Counting terminal
        ``done``/``failed`` records meant a queue that had processed every job
        it was ever given still refused further work, so a long-lived queue
        dead-locked itself after ``max_depth`` lifetime jobs. Pending work is
        the only quantity a capacity limit is meant to bound.

        Raises:
            ValueError: If a job with ``spec.run_id`` already exists.
            QueueFullError: If pending work already equals ``max_depth``.
        """
        with self._lock:
            if spec.run_id in self._jobs:
                raise ValueError(f"job {spec.run_id} already exists")
            pending = sum(
                1
                for record in self._jobs.values()
                if record.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            )
            if pending >= self._max_depth:
                raise QueueFullError(
                    f"job queue is full ({pending}/{self._max_depth}); "
                    "cannot enqueue more jobs"
                )
            record = JobRecord(
                spec=spec,
                status=JobStatus.QUEUED,
                created_at=_utc_now(),
            )
            self._jobs[spec.run_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job record for ``job_id`` or ``None`` if unknown."""
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobRecord]:
        """All jobs in insertion (FIFO) order."""
        with self._lock:
            return list(self._jobs.values())

    def claim_next(self) -> JobRecord | None:
        """Atomically claim the oldest claimable job, or return ``None``.

        "Claimable" means ``queued``, past any retry backoff
        (:attr:`JobRecord.retry_after`), and still inside both retry budgets.
        A job that has spent either budget is dead-lettered here rather than
        handed to a worker that is bound to fail again. A job still cooling down
        is *skipped, not blocking*: the scan continues to the next candidate, so
        one backing-off job never stalls the whole queue behind it.

        Every job settled to ``dead`` here is reported through
        :func:`_report_dead_letter`, *including* on a call that goes on to claim
        a healthy job: reaching a terminal state silently is precisely the
        failure mode the dead-letter signal exists to expose.

        The queued-status check, the ``running`` transition, and the lease stamp
        all happen under the same lock used by every other state operation, so
        concurrent callers cannot both return the same record. The claim
        increments :attr:`JobRecord.lease_epoch` (the fencing token that stops a
        worker which lost its lease from renewing or completing the job) and
        :attr:`JobRecord.attempt` (the retry counter).

        Returns:
            The claimed record, or ``None`` when nothing is claimable. A record
            that cannot be interpreted as a :class:`JobRecord` is skipped (and
            dropped) rather than returned: a claim must never yield something a
            worker cannot read, because the resulting ``AttributeError`` would be
            misreported as a job failure while the poisonous entry stayed queued
            to fail every later claim as well.
        """
        dead_letters: list[tuple[str, int, int]] = []
        claimed: JobRecord | None = None
        with self._lock:
            now = self._now()
            for job_id, record in list(self._jobs.items()):
                if not isinstance(record, JobRecord):
                    # Unreadable entry: drop it so it stops blocking the queue.
                    del self._jobs[job_id]
                    continue
                if record.status is not JobStatus.QUEUED:
                    continue
                if record.retry_after is not None and not _is_expired(
                    record.retry_after, now
                ):
                    continue  # still cooling down; try the next candidate
                exhausted = self._budget_exhausted(record)
                if exhausted is not None:
                    reason, detail = exhausted
                    dead_letters.append(
                        (record.job_id, record.attempt, record.reclaim_count)
                    )
                    self._jobs[record.job_id] = _apply_status(
                        record,
                        JobStatus.DEAD,
                        error=f"{reason}: {detail}",
                        result=None,
                    )
                    continue
                self._jobs[record.job_id] = dataclasses.replace(
                    _apply_status(record, JobStatus.RUNNING, error=None, result=None),
                    lease_expires_at=_lease_deadline(now, self._lease_seconds),
                    lease_epoch=record.lease_epoch + 1,
                    attempt=record.attempt + 1,
                    retry_after=None,
                )
                claimed = self._jobs[record.job_id]
                break
        # Reported after the lock is released, and -- critically -- reached on the
        # claim path too. This loop used to sit after a `return` that left the
        # `with` block from *inside* it, so a call which settled a spent job and
        # then claimed a healthy one returned before reporting anything: the job
        # reached its terminal `dead` state in complete silence. That is the exact
        # poison-job case the dead-letter signal exists to make visible, and it is
        # the *common* shape, not a corner one -- a worker polling a backlog
        # routinely dead-letters an abandoned job on the same pass that hands it
        # the next unit of work.
        for job_id, attempt, reclaims in dead_letters:
            _report_dead_letter(
                job_id,
                "retry-budget-exhausted",
                attempt=attempt,
                reclaims=reclaims,
            )
        return claimed

    def _budget_exhausted(self, record: JobRecord) -> tuple[str, str] | None:
        """Which retry budget ``record`` has spent, or ``None`` if it still has one.

        Returns a ``(reason, detail)`` pair so the caller can build an ``error``
        message naming the actual limit, rather than a generic "gave up".
        """
        if record.attempt >= self._max_attempts:
            return (
                "attempt budget exhausted",
                f"{record.attempt} attempts without success "
                f"(max_attempts={self._max_attempts})",
            )
        if record.reclaim_count > self._max_reclaims:
            return (
                "reclaim budget exhausted",
                f"the worker holding this job died {record.reclaim_count} times "
                f"(max_reclaims={self._max_reclaims})",
            )
        return None

    def _now(self) -> str:
        """This queue's notion of "now", including any test clock offset."""
        return _shifted_now(self._clock_offset_seconds)

    def now(self) -> str:
        """Current time on this queue's clock, as an ISO-8601 UTC string.

        The single seam every queue-side timestamp goes through. It is public so
        a collaborator writing a timestamp *into* a record — the worker stamping
        ``retry_after`` — reads the same clock the queue will later compare that
        value against. A worker using its own ``datetime.now()`` would put the
        two on different bases, and the queue's :func:`_is_expired` would judge
        the backoff against a reference the record was never measured from.
        """
        return self._now()

    def retry_delay_for(self, job_id: str) -> float | None:
        """Seconds remaining before ``job_id`` may be claimed again.

        Returns ``None`` when the job is unknown, not queued, or has no backoff
        pending, so a caller can treat ``0.0``/``None`` as "claimable now".

        Args:
            job_id: The job whose cooldown to inspect.

        Returns:
            Remaining cooldown in seconds (never negative), or ``None``.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.retry_after is None:
                return None
            try:
                remaining = (
                    datetime.fromisoformat(record.retry_after)
                    - datetime.fromisoformat(self._now())
                ).total_seconds()
            except ValueError:
                return 0.0
        return max(0.0, remaining)

    def renew_lease(self, job_id: str, lease_epoch: int) -> JobRecord | None:
        """Extend the lease on a running job, or return ``None``.

        The renewal succeeds only while the record is still ``running`` *and*
        carries ``lease_epoch``. A worker that stalled long enough for its lease
        to be reclaimed therefore fails here instead of resurrecting work that
        another worker now owns.

        Args:
            job_id: The job whose lease is being extended.
            lease_epoch: The fencing token from the claim being renewed.

        Returns:
            The updated record, or ``None`` if the job is unknown, no longer
            running, or is held under a different epoch.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            if record.status is not JobStatus.RUNNING:
                return None
            if record.lease_epoch != lease_epoch:
                return None
            renewed = dataclasses.replace(
                record,
                lease_expires_at=_lease_deadline(_utc_now(), self._lease_seconds),
            )
            self._jobs[job_id] = renewed
            return renewed

    def reap_expired(self) -> list[str]:
        """Return jobs whose lease expired to the ready state.

        A job is reclaimed only when it is ``running`` and its lease deadline
        has passed; queued and terminal jobs are never touched. Reclaimed jobs
        go back to ``queued`` rather than ``failed`` because the pipeline is
        deterministic and the persisted result upsert is idempotent, so the
        work is recoverable. The scan and the requeue run under the same lock
        as every other state operation.

        A job reclaimed more times than :attr:`max_reclaims` is **dead-lettered**
        instead of requeued, and reported through :func:`_report_dead_letter`.
        Without that bound a worker that dies on every attempt (OOM-killed GPU
        process, unmounted volume) cycles ``queued -> running -> dead -> queued``
        forever: the job never reaches a terminal state, never frees capacity,
        and looks identical from outside the process to one that is merely slow.

        Returns:
            The ids of the jobs that were reclaimed, in insertion order. Jobs
            that were dead-lettered instead are *not* included — they are
            terminal, not reclaimed — but they are logged.
        """
        dead_letters: list[tuple[str, int, int]] = []
        with self._lock:
            now = self._now()
            reaped: list[str] = []
            for record in self._jobs.values():
                if record.status is not JobStatus.RUNNING:
                    continue
                if not _is_expired(record.lease_expires_at, now):
                    continue
                reclaims = record.reclaim_count + 1
                if reclaims > self._max_reclaims:
                    dead_letters.append(
                        (record.job_id, record.attempt, reclaims)
                    )
                    self._jobs[record.job_id] = _apply_status(
                        record,
                        JobStatus.DEAD,
                        error=(
                            "reclaim budget exhausted: the worker holding this job "
                            f"died {reclaims - 1} times "
                            f"(max_reclaims={self._max_reclaims})"
                        ),
                        result=None,
                    )
                    self._jobs[record.job_id] = dataclasses.replace(
                        self._jobs[record.job_id], reclaim_count=reclaims
                    )
                    continue
                self._jobs[record.job_id] = dataclasses.replace(
                    record,
                    status=JobStatus.QUEUED,
                    lease_expires_at=None,
                    reclaim_count=reclaims,
                    retry_after=None,
                )
                reaped.append(record.job_id)
        for job_id, attempt, reclaims in dead_letters:
            _report_dead_letter(
                job_id,
                "reclaim-budget-exhausted",
                attempt=attempt,
                reclaims=reclaims,
            )
        return reaped

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: str | None = None,
        lease_epoch: int | None = None,
        retry_after: str | None = None,
    ) -> JobRecord | None:
        """Transition ``job_id`` to ``status``, returning the updated record.

        Args:
            job_id: The job to transition.
            status: The target status.
            error: Error message, recorded when status is failed or dead.
            result: Result pointer, recorded only when status is done.
            lease_epoch: Optional fencing token. When supplied the transition
                applies only while the job is still running under that exact
                epoch, and None is returned otherwise. This is what stops a
                worker whose lease was already reclaimed from overwriting the
                claim of the worker that took over. Callers holding no lease
                (the HTTP router) omit it and get the unfenced behaviour.
            retry_after: ISO-8601 instant before which the job must not be
                claimed again. Set by the worker when it requeues a failed
                attempt; ignored for any other target status, since a terminal
                job is never re-claimed.

        Returns:
            The updated record, or None when a supplied lease_epoch no longer
            matches.

        Raises:
            KeyError: If ``job_id`` is unknown.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if lease_epoch is not None and (
                record.status is not JobStatus.RUNNING
                or record.lease_epoch != lease_epoch
            ):
                return None
            updated = _apply_status(record, status, error, result)
            if status is JobStatus.QUEUED and retry_after is not None:
                updated = dataclasses.replace(updated, retry_after=retry_after)
            self._jobs[job_id] = updated
            return updated

    def requeue_for_retry(
        self, job_id: str, lease_epoch: int, error: str, retry_after: str
    ) -> JobRecord | None:
        """Return a running job to ``queued`` with a backoff, fenced on its epoch.

        This is the retry path taken when :meth:`~validsim.jobs.worker.JobWorker.
        run_once` raises and the job still has attempts left. It is fenced exactly
        like a terminal write, so a worker whose lease was already reclaimed
        cannot push the *new* owner's job back into the ready list — which would
        otherwise start a third concurrent run of the same work.

        Args:
            job_id: The job being retried.
            lease_epoch: The fencing token from the claim that failed.
            error: Why the attempt failed (kept on the record for diagnosis).
            retry_after: ISO-8601 instant before which the job is not claimable.

        Returns:
            The updated record, or ``None`` when the lease was already lost.
        """
        return self.update_status(
            job_id,
            JobStatus.QUEUED,
            error=error,
            lease_epoch=lease_epoch,
            retry_after=retry_after,
        )

    def __len__(self) -> int:
        """Number of queued jobs."""
        with self._lock:
            return len(self._jobs)

    def close(self) -> None:
        """Release backend resources (no-op for the in-memory backend).

        Present so callers can treat every :func:`create_job_queue` backend
        uniformly; the Redis backend overrides this to close its client.
        """


class RedisJobQueue(JobQueue):
    """Redis-backed queue satisfying the :class:`JobQueue` interface.

    Subclasses the in-memory queue purely to advertise interface compatibility
    (and reuse :meth:`new_run_id`); every state-touching method is overridden
    to read/write Redis. The client connection is opened lazily on first use
    under a :class:`threading.Lock` (redis-py clients are not safe for
    concurrent use by multiple threads). Multi-process ordering and atomicity
    are enforced by Redis transactions/Lua rather than that process-local lock.
    """

    _key_prefix: ClassVar[str] = _KEY_PREFIX

    def __init__(
        self,
        url: str | None = None,
        *,
        max_depth: int | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
        max_reclaims: int | None = None,
        retry_backoff: float | None = None,
        _client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Configure the queue without opening a connection.

        Args:
            url: Redis URL (``redis://...``); defaults to the
                ``VALIDSIM_REDIS_URL`` environment variable.
            max_depth: Maximum number of jobs the queue will hold before
                rejecting new work. ``None`` defers to the
                ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` env var, then to
                :data:`_DEFAULT_MAX_DEPTH`. Must resolve to a positive integer.
            lease_seconds: How long a claim stays valid before
                :meth:`reap_expired` may reclaim the job. ``None`` defers to
                ``VALIDSIM_JOB_LEASE_SECONDS``, then to
                :data:`_DEFAULT_LEASE_SECONDS`. Must resolve to a positive integer.
            max_attempts: Attempts allowed before a job is dead-lettered.
                ``None`` defers to ``VALIDSIM_JOB_MAX_ATTEMPTS``, then to
                :data:`_DEFAULT_MAX_ATTEMPTS`.
            max_reclaims: Abandoned claims tolerated before a job is
                dead-lettered. ``None`` defers to
                :data:`_MAX_RECLAIMS_ENV`, then to
                :data:`_DEFAULT_MAX_RECLAIMS`.
            retry_backoff: First retry delay in seconds, doubling per attempt.
                ``None`` defers to :data:`_RETRY_BACKOFF_ENV`, then to
                :data:`_DEFAULT_RETRY_BACKOFF_SECONDS`.
            _client_factory: Test seam — a zero-argument callable returning a
                redis-client-like object. When omitted, the queue opens a real
                ``redis.Redis.from_url(url, decode_responses=True)`` client on
                first use.

        Raises:
            ValueError: When no URL is available (argument or env var) and the
                redis-py driver is installed. With the driver missing,
                construction still succeeds and the actionable missing-driver
                ``RuntimeError`` surfaces at first use instead. Also raised if
                any resolved limit is out of range.
        """
        self._lock = threading.Lock()
        self._client: Any | None = None
        self._client_factory = _client_factory
        self._max_depth = _resolve_max_depth(max_depth)
        self._lease_seconds = _resolve_lease_seconds(lease_seconds)
        self._max_attempts = _resolve_positive_int(
            max_attempts, _MAX_ATTEMPTS_ENV, _DEFAULT_MAX_ATTEMPTS, "max_attempts"
        )
        self._max_reclaims = _resolve_positive_int(
            max_reclaims, _MAX_RECLAIMS_ENV, _DEFAULT_MAX_RECLAIMS, "max_reclaims"
        )
        self._retry_backoff = _resolve_retry_backoff(retry_backoff)
        self._clock_offset_seconds = 0

        resolved = (url or "").strip() or (os.environ.get(_REDIS_URL_ENV) or "").strip()
        if not resolved and _client_factory is None:
            # Fail fast only when the driver is present; otherwise the more
            # actionable error (missing driver) surfaces at first use.
            try:
                _import_redis()
            except RuntimeError:
                pass
            else:
                raise ValueError(
                    "No Redis URL configured: pass url= or set the "
                    f"{_REDIS_URL_ENV} environment variable."
                )
        self._url: str | None = resolved or None

    # -- introspection ------------------------------------------------------

    @property
    def url(self) -> str | None:
        """Resolved Redis URL (``None`` when a client factory was given)."""
        return self._url

    def _job_key(self, job_id: str) -> str:
        """Parameter-free Redis key holding one job's JSON payload."""
        return f"{self._key_prefix}:{job_id}"

    def _index_key(self) -> str:
        """Parameter-free Redis key holding the insertion-order index list."""
        return f"{self._key_prefix}:index"

    def _ready_key(self) -> str:
        """Parameter-free Redis list of not-yet-claimed job ids."""
        return f"{self._key_prefix}:ready"

    # -- connection lifecycle ------------------------------------------------

    def _default_client(self) -> Any:
        """Open the real redis-py client on first use (requires the driver)."""
        redis = _import_redis()
        if not self._url:
            raise ValueError(
                "No Redis URL configured: pass url= or set the "
                f"{_REDIS_URL_ENV} environment variable."
            )
        return redis.Redis.from_url(self._url, decode_responses=True)

    def _ensure_client(self) -> Any:
        """Return the live client, opening it on demand; caller holds the lock."""
        if self._client is None:
            factory = self._client_factory or self._default_client
            self._client = factory()
        return self._client

    # -- JobQueue interface ---------------------------------------------------

    def enqueue(self, spec: JobSpec) -> JobRecord:
        """Atomically store a new queued job and return its record.

        Redis executes duplicate/capacity checks and all three writes (payload,
        insertion index, ready list) in one Lua invocation. This keeps queue
        depth authoritative and prevents an orphaned payload if the process
        stops between commands.

        Raises:
            ValueError: If a job with ``spec.run_id`` already exists.
            QueueFullError: If the queue already holds ``max_depth`` jobs.
        """
        key = self._job_key(spec.run_id)
        record = JobRecord(
            spec=spec,
            status=JobStatus.QUEUED,
            created_at=_utc_now(),
        )
        payload = json.dumps(record.to_dict())
        with self._lock:
            client = self._ensure_client()
            result = int(
                client.eval(
                    _REDIS_ENQUEUE_SCRIPT,
                    3,
                    key,
                    self._index_key(),
                    self._ready_key(),
                    payload,
                    self._max_depth,
                    spec.run_id,
                )
            )
        if result == -1:
            raise ValueError(f"job {spec.run_id} already exists")
        if result == -2:
            depth = int(client.llen(self._index_key()))
            raise QueueFullError(
                f"job queue is full ({depth}/{self._max_depth}); "
                "cannot enqueue more jobs"
            )
        return record

    def get(self, job_id: str) -> JobRecord | None:
        """Return the deserialized job record or ``None`` if unknown."""
        key = self._job_key(job_id)
        with self._lock:
            raw = self._ensure_client().get(key)
        return JobRecord.from_dict(json.loads(raw)) if raw is not None else None

    def list(self) -> list[JobRecord]:
        """All jobs in insertion (FIFO) order, following the index list."""
        with self._lock:
            client = self._ensure_client()
            job_ids = client.lrange(self._index_key(), 0, -1)
            records: list[JobRecord] = []
            for job_id in job_ids:
                raw = client.get(self._job_key(job_id))
                if raw is not None:
                    records.append(JobRecord.from_dict(json.loads(raw)))
        return records

    def claim_next(self) -> JobRecord | None:
        """Atomically claim the oldest claimable Redis job, or return ``None``.

        The ready-list pop, the queued-to-running payload update, the lease
        stamp, the fencing-epoch increment and the attempt increment run inside
        one Lua server operation, so concurrent workers cannot both win the same
        job even when they use different queue instances/processes.

        The same script enforces both retry budgets and quarantines a payload
        this build cannot decode — see :data:`_REDIS_CLAIM_NEXT_SCRIPT` for why
        each of those is bounded rather than left to the caller.
        """
        now = _utc_now()
        with self._lock:
            client = self._ensure_client()
            raw = client.eval(
                _REDIS_CLAIM_NEXT_SCRIPT,
                1,
                self._ready_key(),
                self._key_prefix + ":",
                now,
                _CORRUPT_CLAIM_ATTEMPTS,
                _CORRUPT_CLAIM_TTL_SECONDS,
                _lease_deadline(now, self._lease_seconds),
                self._max_attempts,
                _CLAIM_ATTEMPT_SUFFIX,
                self._max_reclaims,
                _CLAIM_SCAN_BUDGET,
            )
            if raw is None:
                return None
            return JobRecord.from_dict(json.loads(raw))

    def renew_lease(self, job_id: str, lease_epoch: int) -> JobRecord | None:
        """Extend a running job's lease, or return ``None`` if the epoch is stale.

        The epoch check and the payload write happen in one Lua operation, so a
        worker whose lease was already reclaimed cannot overwrite the new
        worker's claim.
        """
        now = _utc_now()
        with self._lock:
            client = self._ensure_client()
            raw = client.eval(
                _REDIS_RENEW_LEASE_SCRIPT,
                1,
                self._job_key(job_id),
                lease_epoch,
                _lease_deadline(now, self._lease_seconds),
            )
            if raw is None:
                return None
            return JobRecord.from_dict(json.loads(raw))

    def reap_expired(self) -> list[str]:
        """Return every job whose lease expired to the ready list.

        Scans the insertion index for ``running`` records past their deadline
        and requeues them in one Lua operation, so a dead worker's claim is
        recovered without a separate read-modify-write race. ``now`` is read
        from the same :func:`_utc_now` seam the rest of the queue uses and is
        passed both as the ISO-8601 string and as epoch seconds, so the script
        compares instants and never the text of the two timestamps.

        A job reclaimed past :attr:`max_reclaims` is dead-lettered by the script
        and reported here, so a worker that keeps dying produces a terminal
        state and a log line rather than an endless reclaim loop.
        """
        now = _utc_now()
        with self._lock:
            client = self._ensure_client()
            raw = client.eval(
                _REDIS_REAP_EXPIRED_SCRIPT,
                2,
                self._index_key(),
                self._ready_key(),
                self._key_prefix + ":",
                now,
                self._max_depth,
                str(_epoch_seconds(now)),
                self._max_reclaims,
            )
        if not raw:
            return []
        reaped, dead_lettered = self._split_reap_result(raw)
        for job_id in dead_lettered:
            record = self.get(job_id)
            _report_dead_letter(
                job_id,
                "reclaim-budget-exhausted",
                attempt=record.attempt if record else 0,
                reclaims=self._max_reclaims + 1,
            )
        return reaped

    @staticmethod
    def _split_reap_result(raw: Any) -> tuple[list[str], list[str]]:
        """Split the reaper's flat reply into reclaimed and dead-lettered ids.

        The script returns one array whose dead-lettered entries are tagged with
        a leading ``!`` (see :data:`_REDIS_REAP_EXPIRED_SCRIPT` for why a nested
        table is not used). A job id can never start with ``!`` — the router
        restricts them to ``^vrun-[0-9a-f]{8}$`` — so the marker is unambiguous.
        """
        reaped: list[str] = []
        dead: list[str] = []
        for entry in raw or []:
            job_id = str(entry)
            (dead if job_id.startswith("!") else reaped).append(job_id.lstrip("!"))
        return reaped, dead

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: str | None = None,
        lease_epoch: int | None = None,
        retry_after: str | None = None,
    ) -> JobRecord | None:
        """Transition ``job_id`` to ``status``, persisting the updated record.

        Every write on this backend is decided server-side, so a transition
        cannot lose to a concurrent one from another process. With
        ``lease_epoch`` supplied the check and the write share
        :data:`_REDIS_FINISH_SCRIPT`, so a worker whose lease was already
        reclaimed cannot overwrite the new owner's claim. Without one, the write
        is a compare-and-set on the bytes that were read
        (:data:`_REDIS_CAS_SET_SCRIPT`): it commits only if nothing else touched
        the record in between, and retries if it did. A ``dead`` transition is
        reported through :func:`_report_dead_letter`, matching the in-memory
        backend.

        Args:
            job_id: The job to transition.
            status: The target status.
            error: Error message, recorded when status is failed or dead.
            result: Result pointer, recorded only when status is done.
            lease_epoch: Optional fencing token for a held claim. When supplied,
                the transition applies only while the job is running under that
                exact epoch; otherwise None is returned. A caller holding no
                claim omits it.
            retry_after: ISO-8601 instant before which the job must not be
                claimed again. Honoured on the queued path, which is the only
                target that can be claimed again, and which shares
                :data:`_REDIS_SET_QUEUED_SCRIPT` with the fenced worker retry;
                the backoff itself is enforced at claim time, not here.

        Raises:
            KeyError: If ``job_id`` is unknown.
            QueueContentionError: If another process changed the record on every
                attempt of an unfenced write, so the transition was not applied.
        """
        key = self._job_key(job_id)
        with self._lock:
            client = self._ensure_client()
            if status is JobStatus.QUEUED:
                # A queued transition has to re-list the id, so it cannot share
                # the terminal-write path below: that stamps finished_at and
                # clears retry_after for every status it is handed, which is
                # wrong for a job that is pending again rather than finished.
                raw = client.eval(
                    _REDIS_SET_QUEUED_SCRIPT,
                    2,
                    key,
                    self._ready_key(),
                    "" if lease_epoch is None else str(lease_epoch),
                    job_id,
                    "" if retry_after is None else retry_after,
                )
                if raw is None:
                    if lease_epoch is None:
                        raise KeyError(job_id)
                    return None
                return JobRecord.from_dict(json.loads(raw))
            if lease_epoch is not None:
                raw = client.eval(
                    _REDIS_FINISH_SCRIPT,
                    1,
                    key,
                    lease_epoch,
                    status.value,
                    _utc_now(),
                    error if status in (JobStatus.FAILED, JobStatus.DEAD) else result,
                    job_id,
                )
                if raw is None:
                    return None
                updated = JobRecord.from_dict(json.loads(raw))
                if status is JobStatus.DEAD:
                    _report_dead_letter(
                        job_id,
                        "attempt-budget-exhausted",
                        attempt=updated.attempt,
                        reclaims=updated.reclaim_count,
                    )
                return updated
            # Unfenced, non-queued transition: still one server-side decision,
            # but expressed as a compare-and-set because the caller has no epoch
            # to compare against and the target status need not be terminal.
            # Committing only while the stored bytes are the ones this caller
            # read is what stops the write from rewinding a claim another
            # process made in between.
            payload: str | None = None
            for _attempt in range(_UNFENCED_WRITE_ATTEMPTS):
                raw = client.get(key)
                if raw is None:
                    raise KeyError(job_id)
                record = JobRecord.from_dict(json.loads(raw))
                payload = json.dumps(_apply_status(record, status, error, result).to_dict())
                committed = client.eval(_REDIS_CAS_SET_SCRIPT, 1, key, raw, payload)
                if committed == payload:
                    break
                if committed is None:
                    raise KeyError(job_id)
            else:
                raise QueueContentionError(
                    f"{job_id}: another process changed the record on each of the "
                    f"{_UNFENCED_WRITE_ATTEMPTS} unfenced writes, so the transition to "
                    f"{status.value} was not applied"
                )
            updated = JobRecord.from_dict(json.loads(payload))
            if status is JobStatus.DEAD:
                _report_dead_letter(
                    job_id,
                    "attempt-budget-exhausted",
                    attempt=updated.attempt,
                    reclaims=updated.reclaim_count,
                )
            return updated

    def requeue_for_retry(
        self, job_id: str, lease_epoch: int, error: str, retry_after: str
    ) -> JobRecord | None:
        """Return a running job to ``queued`` with a backoff, fenced on its epoch.

        The status transition and the repopulation of the ready list run in one
        server-side operation, and the epoch check makes the transition a
        compare-and-set. A worker whose lease was already reclaimed therefore
        fails here instead of pushing the *new* owner's job back into the ready
        list, which would start a third concurrent run of the same work.

        Re-listing the id is not an optimisation, it is the whole point on this
        backend: ``claim_next`` reads the ready list and nothing else, so a
        record left in ``queued`` without its id on that list is a job that
        has silently stopped existing -- unclaimable, unreapable and
        un-dead-letterable while the API still reports it as pending. The
        backoff itself is enforced at claim time
        (:data:`_REDIS_CLAIM_NEXT_SCRIPT`), which re-lists a cooling job at
        the tail and reports no work, matching how the in-memory backend skips
        it.

        Args:
            job_id: The job being retried.
            lease_epoch: The fencing token from the claim that failed.
            error: Why the attempt failed. Retained for interface parity with
                the in-memory backend, which likewise does not persist it on a
                requeue -- it is recorded only once the job settles as
                failed or dead.
            retry_after: ISO-8601 instant before which the job is not claimable.

        Returns:
            The updated record, or ``None`` when the lease was already lost.
        """
        # One implementation for every route back to queued, so this cannot
        # drift from a caller using update_status(..., QUEUED) again.
        return self.update_status(
            job_id,
            JobStatus.QUEUED,
            error=error,
            lease_epoch=lease_epoch,
            retry_after=retry_after,
        )

    def retry_delay_for(self, job_id: str) -> float | None:
        """Seconds remaining before ``job_id`` may be claimed again.

        Returns ``None`` when the job is unknown, not queued, or has no backoff
        pending, so a caller can treat ``0.0``/``None`` as "claimable now".
        """
        record = self.get(job_id)
        if record is None or record.retry_after is None:
            return None
        try:
            remaining = (
                datetime.fromisoformat(record.retry_after)
                - datetime.fromisoformat(_utc_now())
            ).total_seconds()
        except ValueError:
            return 0.0
        return max(0.0, remaining)

    def __len__(self) -> int:
        """Number of queued jobs (length of the insertion-order index)."""
        with self._lock:
            return int(self._ensure_client().llen(self._index_key()))

    def close(self) -> None:
        """Close the underlying client (a later query reopens it)."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None


def create_job_queue() -> JobQueue:
    """Build a job queue from the environment configuration.

    Reads ``VALIDSIM_JOB_QUEUE`` (case-insensitive):

    * ``"redis"`` — :class:`RedisJobQueue` using ``VALIDSIM_REDIS_URL``.
      Construction is side-effect free (no driver import or connection until
      first use), but it fails fast with ``ValueError`` when no URL is
      configured and the redis-py driver is installed.
    * anything else — the default in-memory :class:`JobQueue`, keeping tests
      and local runs side-effect free.
    """
    backend = os.environ.get(_QUEUE_ENV, "memory").strip().lower()
    if backend == "redis":
        return RedisJobQueue()
    return JobQueue()
