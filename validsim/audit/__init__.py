"""Audit trail: canonical records, hash chaining, and export.

The audit trail is the compounding asset this product is built on. Insurers
adopt evidence they can trust is **immutable and retained**, so a record
that can be silently rewritten -- or deleted without a trace -- undermines
the very claim the scorecard makes.

This module provides the *export* half of that story:

* :func:`build_audit_records` turns stored runs into canonical audit
  records, each one hash-chained to its predecessor.
* :func:`export_audit_log` renders those records as CSV, JSON, or JSON
  Lines for auditors, regulators, and insurance evidence packages.
* :func:`verify_audit_chain` re-derives and checks the chain, so a reader
  can *verify* the log rather than trust it.

**What this module does not do, deliberately.** It does not remove
``DELETE``. The product decision recorded in ``docs/ISSUE_CATALOG.md`` §7 is
that operator deletion survives, and that *every deletion is recorded in a
separate durable ledger*. Turning ``DELETE`` into a *retraction* -- with an
actor, a reason, and a ledger entry -- is a separate change that needs
project-scoped identity to land first, because today's deployment has a
single shared ``VALIDSIM_API_KEY`` and therefore no actor identity to
record. Shipping a retraction path before identity exists would produce a
ledger full of unattributable events, which is worse than no ledger.

So this module ships the *readable, verifiable, hash-chained* side of the
audit trail, and leaves the write-side retraction decision where §7 puts
it. See ``docs/ISSUE_CATALOG.md`` §7 item 6 and the vault's "Technical Moat
Features" §12.

**Why the hashing is careful.** A chain is only worth something if the
preimage is byte-stable, so :func:`canonical_json` sorts keys, fixes float
formatting, and drops ``NaN``/``Infinity`` (which are not valid JSON and
hash differently across runtimes). The schema version is pinned *into* the
preimage, so a future field addition cannot silently re-hash history.
"""

from __future__ import annotations

from validsim.audit.export import (
    AUDIT_SCHEMA_VERSION,
    AuditRecord,
    build_audit_records,
    export_audit_log,
    verify_audit_chain,
)
from validsim.audit.records import (
    canonical_json,
    chain_hash,
    record_digest,
    record_preimage,
)

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditRecord",
    "build_audit_records",
    "canonical_json",
    "chain_hash",
    "export_audit_log",
    "record_digest",
    "record_preimage",
    "verify_audit_chain",
]
