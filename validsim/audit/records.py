"""Canonical serialization and hash primitives for the audit trail.

A hash chain is only as trustworthy as its preimage. If the bytes that get
hashed depend on dict iteration order, float repr, or the Python version,
then the same logical record hashes differently on different machines and
the chain breaks for reasons that have nothing to do with tampering.

So this module is deliberately narrow: it produces **one** canonical byte
string per record and hashes that. Everything else in the audit package
builds on these two functions.

**The rules**

1. **Sorted keys.** Every mapping is serialized with its keys sorted, so
   insertion order cannot change the bytes.
2. **Fixed float formatting.** Floats are rendered with
   :func:`repr`, which is the shortest string that round-trips exactly in
   Python 3. This is stable across platforms for IEEE-754 doubles.
3. **No ``NaN`` / ``Infinity``.** These are not valid JSON and their
   repr differs between languages. A non-finite float is an *error*, not
   something to encode: it means the scorecard upstream produced a value
   that cannot be faithfully audited.
4. **The schema version is in the preimage.** Adding a field later cannot
   change the hash of an old record, and a reader can tell which rules
   produced a given digest.
5. **Strings are escaped, not stripped.** The canonical form is JSON, so
   it is unambiguous and machine-checkable.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

#: The schema version mixed into every record preimage.
#:
#: Bump this when the *meaning* of a field changes. Adding a new optional
#: field is also a bump: the preimage changes, so old digests must not be
#: recomputed against the new rules.
SCHEMA_VERSION = "audit-record-v1"

#: The hash function used for the chain. SHA-256 is used for every link.
_HASH_ALGORITHM = "sha256"


def _canonical_value(value: Any) -> Any:
    """Convert *value* into a JSON-safe, order-stable structure.

    Args:
        value: Any value that may appear in an audit record.

    Returns:
        A structure that :func:`json.dumps` can serialize deterministically.

    Raises:
        ValueError: If a float is not finite, or a value cannot be
            represented in JSON at all.
    """
    if value is None or isinstance(value, (str, bool, int)):
        # ``bool`` is a subclass of ``int``; both are emitted as-is. ``int``
        # is emitted verbatim (no float coercion) so a run id or episode
        # count stays an integer in the JSON.
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"non-finite float {value!r} cannot be represented in an audit record"
            )
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"audit record keys must be strings, got {key!r}")
            out[key] = _canonical_value(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # A set has no inherent order, so it cannot be canonicalized
        # without imposing one. Sorting is the only stable choice, and it
        # requires comparable members.
        return sorted(_canonical_value(item) for item in value)
    raise ValueError(f"unsupported value in audit record: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    """Serialize *payload* to its canonical JSON form.

    The result is byte-stable: the same logical payload always produces the
    same string, on any platform, in any Python 3.

    Args:
        payload: The value to serialize.

    Returns:
        A canonical JSON string with sorted keys and no incidental
        whitespace.

    Raises:
        ValueError: If the payload contains a non-finite float or a value
            with no JSON representation.
    """
    return json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def record_preimage(record: dict[str, Any]) -> str:
    """Return the exact bytes that are hashed for *record*.

    The preimage is the canonical JSON of the record **without** its own
    ``chain_hash`` field, plus the schema version. Excluding the digest
    itself is what makes the chain computable: a record's hash is derived
    from its content and its predecessor's hash, never from itself.

    Args:
        record: The audit record. May contain a ``chain_hash`` key, which
            is stripped before hashing.

    Returns:
        The canonical preimage string.
    """
    body = {key: value for key, value in record.items() if key != "chain_hash"}
    body["_schema"] = SCHEMA_VERSION
    return canonical_json(body)


def record_digest(record: dict[str, Any]) -> str:
    """Return the content digest of *record*, ignoring any existing chain link.

    This is the hash of the record's own content, combined with the
    ``previous_hash`` it carries. It is what the next record chains to.

    Args:
        record: The audit record.

    Returns:
        A hex SHA-256 digest.
    """
    return hashlib.new(_HASH_ALGORITHM, record_preimage(record).encode("utf-8")).hexdigest()


def chain_hash(previous_hash: str | None, record: dict[str, Any]) -> str:
    """Return the chain link for *record* following *previous_hash*.

    The link mixes the predecessor's digest with this record's content
    digest, so any edit to an earlier record invalidates every later link.
    That is the property that makes a gap in the log *detectable* rather
    than merely unlikely.

    Args:
        previous_hash: The preceding record's ``chain_hash``, or ``None``
            for the first record in a chain.
        record: The audit record being linked.

    Returns:
        A hex SHA-256 chain link.
    """
    body = {key: value for key, value in record.items() if key != "chain_hash"}
    body["_schema"] = SCHEMA_VERSION
    body["_previous"] = previous_hash or ""
    canonical = canonical_json(body)
    return hashlib.new(_HASH_ALGORITHM, canonical.encode("utf-8")).hexdigest()
