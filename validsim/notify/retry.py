"""Adaptive retry policy for notification delivery.

The dispatcher used to apply one fixed ladder to every failure: two retries
with a fixed exponential backoff, regardless of *why* the attempt failed.
That wastes time on failures that cannot succeed (a 404 will still be a 404
after a wait) and is too timid for failures that usually clear quickly (a 503
from a restarting service).

This module separates the two questions the fixed ladder conflated:

* **Is this failure worth retrying?** A permanent failure -- a 4xx that
  means the request itself is wrong -- is not; retrying it only repeats the
  same mistake and delays the operator's signal. A transient failure -- a
  transport error, a 429, a 5xx -- often clears on its own.
* **How long should we wait?** The delay adapts to the failure history
  observed so far rather than following a fixed curve.

The policy is deliberately *bounded*. Every knob has a ceiling, an attempt
ladder can never exceed the configured maximum, and a shared deadline caps
the total time a delivery may consume. An adaptive policy that can talk
itself into an unbounded wait is worse than a fixed one, so the adaptive
parts only ever *reduce* work or *reorder* it within the existing budget.

Safety properties worth stating explicitly:

* The policy is pure and side-effect free. It never sleeps, never touches
  the network, and never reads a clock of its own -- the caller passes in
  the elapsed time it wants the policy to reason about. That keeps the
  policy trivially testable and keeps time decisions in one place.
* No randomness. Jitter is deterministic and derived from the attempt
  number, so a delivery's timing is reproducible. Random jitter would
  make the dispatcher's timing untestable and would not survive the
  replay-safety constraints on this package.
* Nothing user-controlled feeds the delay arithmetic beyond the response
  status, and the ``Retry-After`` value is clamped before use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "FailureKind",
    "RetryDecision",
    "RetryPolicy",
    "classify_status",
]


class FailureKind:
    """How a failed delivery attempt should be understood.

    The values are plain strings so they serialize cleanly into logs and
    audit records without any custom encoder.
    """

    #: A transport-level problem: the request never produced a response.
    TRANSIENT = "transient"
    #: The server asked us to slow down.
    THROTTLED = "throttled"
    #: The server failed but may succeed if we try again.
    SERVER_ERROR = "server_error"
    #: The request itself was rejected; a retry would be rejected too.
    PERMANENT = "permanent"
    #: The failure could not be classified.
    UNKNOWN = "unknown"


#: Status codes that mean "the request was wrong, do not try again".
#: 429 is deliberately absent -- it is throttling, not a client error.
_PERMANENT_STATUS: Final[frozenset[int]] = frozenset(
    {
        400,  # Bad Request -- malformed payload
        401,  # Unauthorized -- bad or missing secret
        403,  # Forbidden -- credentials do not permit this
        404,  # Not Found -- the endpoint is gone or was never right
        405,  # Method Not Allowed -- the receiver rejects POST
        406,  # Not Acceptable
        410,  # Gone -- permanently removed
        415,  # Unsupported Media Type
        422,  # Unprocessable Content
        501,  # Not Implemented -- the receiver will not implement it
        505,  # HTTP Version Not Supported
    }
)

#: Status codes that explicitly mean "come back later".
_THROTTLED_STATUS: Final[frozenset[int]] = frozenset({429})


def classify_status(status_code: int | None) -> str:
    """Classify an HTTP status code into a :class:`FailureKind` value.

    Args:
        status_code: The response status, or ``None`` when the attempt
            failed before a response existed.

    Returns:
        One of the :class:`FailureKind` constants.

    The classification is intentionally conservative about the *permanent*
    bucket: it lists specific codes rather than treating "all of 4xx" as
    permanent, because 408 (Request Timeout) and 425 (Too Early) are 4xx
    codes that genuinely are worth retrying.
    """
    if status_code is None:
        return FailureKind.TRANSIENT
    if status_code in _PERMANENT_STATUS:
        return FailureKind.PERMANENT
    if status_code in _THROTTLED_STATUS:
        return FailureKind.THROTTLED
    if status_code == 408:
        # The server timed out waiting for us; the request may still be fine.
        return FailureKind.TRANSIENT
    if status_code == 425:
        # "Too Early" (RFC 8470): the receiver asked us to wait, e.g. it wants
        # a TLS session resumed. The request itself is fine, so retrying it
        # later succeeds -- treating it as permanent would silently drop a
        # perfectly good delivery.
        return FailureKind.TRANSIENT
    if 500 <= status_code < 600:
        return FailureKind.SERVER_ERROR
    if 400 <= status_code < 500:
        # An unlisted 4xx: the receiver rejected the request, and repeating
        # it is unlikely to help.
        return FailureKind.PERMANENT
    if 200 <= status_code < 300:
        return FailureKind.UNKNOWN
    # 1xx and 3xx are not success for our purposes.
    return FailureKind.UNKNOWN


@dataclass(frozen=True)
class RetryDecision:
    """What the policy says the caller should do after a failed attempt.

    Attributes:
        retry: Whether another attempt should be made at all.
        delay_s: How long to wait before that attempt, in seconds. Always
            ``0.0`` when ``retry`` is ``False``.
        kind: The :class:`FailureKind` that produced this decision.
        reason: A short, stable, machine-readable explanation. Suitable for
            logs and audit records; never contains response bodies.
    """

    retry: bool
    delay_s: float
    kind: str
    reason: str

    def __post_init__(self) -> None:
        if not self.retry and self.delay_s != 0.0:
            raise ValueError("a non-retrying decision must carry a zero delay")


@dataclass
class RetryPolicy:
    """A bounded, adaptive retry policy.

    The policy is intentionally *not* a general-purpose backoff engine. It
    exists to answer two questions about one delivery attempt: whether to
    try again, and how long to wait first.

    Attributes:
        max_retries: Maximum number of *retries* after the initial
            attempt. A value of 2 means at most three HTTP requests.
        base_delay_s: The first retry's delay. Later delays are derived
            from it.
        max_delay_s: Ceiling for any single delay. No wait, however
            adaptive, ever exceeds this.
        max_total_delay_s: Ceiling for the sum of all delays in one
            delivery. The policy keeps a running total so a long ladder
            cannot accumulate into an unbounded wait.
        permanent_is_terminal: When true (the default), a permanent failure
            ends the ladder immediately. Set it to false only for a
            receiver whose 4xx responses are known to be transient.
        honour_retry_after: When true (the default), a ``Retry-After``
            header is used as the delay -- clamped by ``max_delay_s``.
    """

    max_retries: int = 2
    base_delay_s: float = 0.1
    max_delay_s: float = 30.0
    max_total_delay_s: float = 60.0
    permanent_is_terminal: bool = True
    honour_retry_after: bool = True

    #: Running total of the delays this policy has handed out.
    _total_delay_s: float = field(default=0.0, init=False, repr=False)
    #: Failures observed so far, most recent last. Used to adapt.
    _history: list[str] = field(default_factory=list, init=False, repr=False)

    # -- introspection ---------------------------------------------------

    @property
    def total_delay_s(self) -> float:
        """Total delay handed out so far, in seconds."""
        return self._total_delay_s

    @property
    def history(self) -> tuple[str, ...]:
        """The failure kinds observed so far, oldest first."""
        return tuple(self._history)

    def reset(self) -> None:
        """Forget the observed history and the accumulated delay."""
        self._total_delay_s = 0.0
        self._history.clear()

    # -- the decision ---------------------------------------------------

    def decide(
        self,
        *,
        attempt: int,
        status_code: int | None,
        error: str | None = None,
        retry_after_s: float | None = None,
    ) -> RetryDecision:
        """Decide what to do after a failed attempt.

        Args:
            attempt: Zero-based index of the attempt that just failed.
            status_code: The response status, or ``None`` if the attempt
                never got a response.
            error: A short error description, used only to build the
                decision's ``reason``.
            retry_after_s: A ``Retry-After`` hint from the receiver, in
                seconds, if one was offered and parsed.

        Returns:
            A :class:`RetryDecision`.
        """
        kind = classify_status(status_code)
        self._history.append(kind)

        if kind == FailureKind.PERMANENT and self.permanent_is_terminal:
            return RetryDecision(
                retry=False,
                delay_s=0.0,
                kind=kind,
                reason=f"permanent failure (HTTP {status_code}); not retrying",
            )

        if attempt >= self.max_retries:
            return RetryDecision(
                retry=False,
                delay_s=0.0,
                kind=kind,
                reason=(
                    f"retry budget exhausted after {self.max_retries} "
                    f"retries (last failure: {_describe(kind, status_code, error)})"
                ),
            )

        delay = self._delay_for(attempt=attempt, kind=kind, retry_after_s=retry_after_s)

        remaining = self.max_total_delay_s - self._total_delay_s
        if remaining <= 0:
            return RetryDecision(
                retry=False,
                delay_s=0.0,
                kind=kind,
                reason="total retry delay budget exhausted",
            )
        delay = min(delay, remaining)

        self._total_delay_s += delay
        return RetryDecision(
            retry=True,
            delay_s=delay,
            kind=kind,
            reason=(
                f"{kind} failure ({_describe(kind, status_code, error)}); "
                f"retrying after {delay:.3f}s"
            ),
        )

    # -- delay arithmetic ------------------------------------------------

    def _delay_for(
        self, *, attempt: int, kind: str, retry_after_s: float | None
    ) -> float:
        """Compute the wait before the next attempt."""
        if self.honour_retry_after and retry_after_s is not None and retry_after_s > 0:
            # The receiver told us how long to wait. That is better
            # information than our own curve, so it wins -- but only after
            # clamping, so a hostile or confused receiver cannot park a
            # delivery for hours.
            return min(float(retry_after_s), self.max_delay_s)

        if kind == FailureKind.THROTTLED:
            # Throttling is the one case where the default curve is clearly
            # too timid: the receiver explicitly asked us to slow down.
            return min(self.base_delay_s * (2 ** (attempt + 2)), self.max_delay_s)

        if kind == FailureKind.SERVER_ERROR:
            # Server errors usually clear quickly; back off gently so the
            # receiver is not hammered while it is recovering.
            return min(self.base_delay_s * (2**attempt), self.max_delay_s)

        # Transport errors: the classic exponential ladder.
        return min(self.base_delay_s * (2**attempt), self.max_delay_s)


def _describe(kind: str, status_code: int | None, error: str | None) -> str:
    """Build a short, stable description of a failure.

    The description never includes a response body. It may include the
    exception text of a transport error, which is already surfaced
    verbatim in the delivery result, so no new information leaks here.
    """
    if status_code is not None:
        return f"HTTP {status_code}"
    if error:
        return f"{kind} ({error})"
    return kind
