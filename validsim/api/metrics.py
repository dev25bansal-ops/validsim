"""Prometheus metrics endpoint for the ValidSim API.

The scrape surface is derived from the *injected* validation store plus a tiny
process-local HTTP request counter, so a scrape costs no extra I/O beyond the
single cheap read the store already offers (for the default in-memory backend
that is a pure in-memory pass; no per-run fetch, no second query). Exposed
metrics:

* ``validsim_runs_total`` (counter) — number of validation runs recorded.
* ``validsim_approvals_total`` (counter) — runs whose verdict was ``APPROVE``.
* ``validsim_blocks_total`` (counter) — runs whose verdict was ``BLOCK``.
* ``validsim_composite_score`` (gauge) — composite score of the latest run
  (``0`` when the store is empty).
* ``validsim_build_info`` (gauge) — constant ``1`` labelled with the version.
* ``validsim_http_requests_total`` (counter) — requests by status class, kept
  in-process by :class:`Metrics` and bumped from the ASGI middleware.

Output is Prometheus *text exposition format* served as ``text/plain``.
"""

from __future__ import annotations

import threading
from typing import Iterable

from fastapi import APIRouter, Request
from fastapi.responses import Response

from validsim import __version__
from validsim.store.memory import ValidationStore

__all__ = [
    "Metrics",
    "metrics_router",
    "render_metrics",
    "HTTP_METRIC_NAME",
    "STATUS_CLASSES",
    "PROMETHEUS_CONTENT_TYPE",
]

#: Content type required by the Prometheus text exposition format.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Base name of the in-process HTTP request counter.
HTTP_METRIC_NAME = "validsim_http_requests_total"
#: Status classes tracked by :class:`Metrics` (1xx is intentionally omitted).
STATUS_CLASSES: tuple[str, ...] = ("2xx", "3xx", "4xx", "5xx")


def _status_class(status_code: int) -> str | None:
    """Map an HTTP status code to its Prometheus class label (``"2xx"`` ...).

    Returns ``None`` for codes outside 200-599 (e.g. 1xx informational), which
    are not counted so the counter's label set stays bounded.
    """
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return None


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    """Process-local counters for HTTP requests bucketed by status class.

    Intentionally minimal: a single ``threading.Lock`` guards a fixed dict of
    integer counters, so ``observe_status`` is O(1) and adds no measurable cost
    to the request path. Run/approval/block/composite metrics are *not* stored
    here — they are derived from the injected store at scrape time.
    """

    def __init__(self) -> None:
        """Create a counter with every status class initialised to zero."""
        self._lock = threading.Lock()
        self._http_requests: dict[str, int] = {cls: 0 for cls in STATUS_CLASSES}

    def observe_status(self, status_code: int) -> None:
        """Increment the request counter for ``status_code``'s class.

        Unrecognised codes (outside 200-599) are ignored rather than raising, so
        a stray informational response never breaks the logging middleware.
        """
        cls = _status_class(status_code)
        if cls is None:
            return
        with self._lock:
            self._http_requests[cls] += 1

    def snapshot_http(self) -> dict[str, int]:
        """Return a consistent copy of the per-status-class request counts."""
        with self._lock:
            return dict(self._http_requests)


def _summary(store: ValidationStore) -> dict[str, float]:
    """Derive run/approval/block/composite values from ``store`` in one pass.

    A single :meth:`~ValidationStore.history` call (oldest-first) yields every
    value, so a scrape performs exactly one cheap store read — no ``get()`` per
    run, no second query. ``composite`` is the latest run's score, ``0.0`` when
    the store is empty.
    """
    runs = list(store.history())
    total = len(runs)
    approvals = sum(1 for r in runs if r.scorecard.deploy_decision == "APPROVE")
    latest_composite = runs[-1].scorecard.composite_score if runs else 0.0
    return {
        "runs": float(total),
        "approvals": float(approvals),
        "blocks": float(total - approvals),
        "composite": float(latest_composite),
    }


def _fmt(value: float) -> str:
    """Format a numeric sample value, dropping a trailing ``.0`` for integers."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def render_metrics(store: ValidationStore, metrics: Metrics) -> str:
    """Render the full Prometheus text exposition for the current state.

    Args:
        store: Injected validation store the run/approval/block/composite
            gauges are derived from.
        metrics: In-process HTTP request counters.

    Returns:
        The metrics body as a ``\\n``-terminated text/plain string. Every
        declared metric name is always present (zero-valued when there is no
        data), so a scrape is stable and alertable.
    """
    summary = _summary(store)
    http_counts = metrics.snapshot_http()

    lines: list[str] = []

    def emit(metric: str, kind: str, help_text: str, sample: str) -> None:
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} {kind}")
        lines.append(sample)

    emit(
        "validsim_runs_total",
        "counter",
        "Total number of validation runs recorded.",
        f"validsim_runs_total {_fmt(summary['runs'])}",
    )
    emit(
        "validsim_approvals_total",
        "counter",
        "Total number of runs approved for deployment.",
        f"validsim_approvals_total {_fmt(summary['approvals'])}",
    )
    emit(
        "validsim_blocks_total",
        "counter",
        "Total number of runs blocked from deployment.",
        f"validsim_blocks_total {_fmt(summary['blocks'])}",
    )
    emit(
        "validsim_composite_score",
        "gauge",
        "Composite score of the most recent validation run.",
        f"validsim_composite_score {_fmt(summary['composite'])}",
    )
    emit(
        "validsim_build_info",
        "gauge",
        "Build information (constant 1 labelled with the version).",
        f'validsim_build_info{{version="{_escape_label(__version__)}"}} 1',
    )

    lines.append(f"# HELP {HTTP_METRIC_NAME} Total HTTP requests by status class.")
    lines.append(f"# TYPE {HTTP_METRIC_NAME} counter")
    classes: Iterable[str] = STATUS_CLASSES
    for cls in classes:
        count = http_counts.get(cls, 0)
        lines.append(f'{HTTP_METRIC_NAME}{{class="{cls}"}} {count}')

    return "\n".join(lines) + "\n"


metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get("/metrics", include_in_schema=True)
def get_metrics(request: Request) -> Response:
    """Serve Prometheus text metrics derived from the injected store.

    Reads the store and counters from ``app.state`` (the same instances
    :func:`~validsim.api.main.create_app` wired), so the endpoint performs no
    construction and no extra I/O beyond the single store pass.
    """
    store: ValidationStore = request.app.state.store
    metrics: Metrics | None = getattr(request.app.state, "metrics", None)
    if metrics is None:  # defensive: a bare app without wiring still scrapes.
        metrics = Metrics()
    body = render_metrics(store, metrics)
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
