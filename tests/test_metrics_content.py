"""Content-level tests for ValidSim's Prometheus exposition and log wiring.

Where :mod:`tests.test_observability` checks that the scrape surface *exists*,
this module checks that it is *correct*:

* ``validsim_runs_total`` / ``validsim_approvals_total`` / ``validsim_blocks_total``
  are derived from the *injected* store, so a store seeded with a known
  APPROVE/BLOCK mix must scrape back exactly those counts;
* ``validsim_composite_score`` reports the newest run's score (newest by
  ``created_at``, not by insertion order);
* ``validsim_build_info`` is a constant ``1`` carrying the version label;
* the in-process HTTP counter is bumped once per request and bucketed by status
  class by the ASGI observability middleware in :mod:`validsim.api.main`;
* the body is well-formed Prometheus text exposition served as ``text/plain``;
* :func:`validsim.logging.configure_logging` is idempotent (a second call never
  duplicates handlers).

No source is modified: runs are seeded straight into a
:class:`~validsim.store.memory.ValidationStore` that is injected via
``create_app(store)``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim import __version__
from validsim.api.main import create_app
from validsim.api.metrics import (
    HTTP_METRIC_NAME,
    PROMETHEUS_CONTENT_TYPE,
    STATUS_CLASSES,
    Metrics,
    render_metrics,
)
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
import validsim.logging as _logging_module
from validsim.logging import configure_logging
from validsim.store.memory import StoredRun, ValidationStore

#: Env vars ``create_app`` reads at build time; cleared before every test so
#: each case controls its own configuration deterministically.
_CONFIG_ENV = (
    "VALIDSIM_API_KEY",
    "VALIDSIM_CORS_ORIGINS",
    "VALIDSIM_RATE_LIMIT",
    "VALIDSIM_RATE_WINDOW_SECONDS",
    "VALIDSIM_JOB_QUEUE",
    "VALIDSIM_STORE",
    "VALIDSIM_LOG_LEVEL",
)

#: Declared metric names, mapped to the Prometheus type the exposition must
#: advertise for each.
#:
#: The run/approval/block series are gauges, not counters: runs are deletable,
#: so their counts decrease when a run is removed. Declaring a non-monotonic
#: series as a counter breaks ``rate()`` and can make alerts fire backwards.
#: Their ``_total`` suffix is kept for backward compatibility with existing
#: dashboards; the ``gauge`` type is the load-bearing part.
_EXPECTED_TYPES = {
    "validsim_runs_total": "gauge",
    "validsim_approvals_total": "gauge",
    "validsim_blocks_total": "gauge",
    "validsim_composite_score": "gauge",
    "validsim_build_info": "gauge",
    HTTP_METRIC_NAME: "counter",
}

#: Attribute markers :mod:`validsim.logging` uses to recognise its own handler
#: and configured state. Read defensively so an internal rename shows up as a
#: failed assertion rather than an import error.
_HANDLER_MARKER = getattr(_logging_module, "_HANDLER_MARKER", "_validsim_json_handler")
_CONFIGURED_MARKER = getattr(
    _logging_module, "_CONFIGURED_MARKER", "_validsim_logging_configured"
)

#: One sample line: ``name{labels}<whitespace>value`` (labels optional).
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"[ \t]+(?P<value>-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|NaN|\+Inf|-Inf)$"
)
#: ``key="value"`` pairs inside a label set, honouring escaped quotes.
_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #
def _make_run(
    run_id: str,
    decision: str,
    composite: float,
    created_at: str,
    checkpoint_id: str = "ckpt-seed",
) -> StoredRun:
    """Build a minimal but complete :class:`StoredRun` with a chosen verdict.

    Only ``scorecard.deploy_decision`` and ``scorecard.composite_score`` matter
    to the scrape surface (see :func:`validsim.api.metrics._summary`); the rest
    of the artifacts are filled with plausible constants so the record is a
    genuine store payload rather than a stub.
    """
    scorecard = Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id="pick-place",
        composite_score=composite,
        success_rate=round(composite / 100.0, 4),
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id="pick-place",
        created_at=created_at,
        scorecard=scorecard,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


def _seed(store: ValidationStore, specs: list[tuple[str, float, str]]) -> list[StoredRun]:
    """Save one run per ``(decision, composite, created_at)`` spec."""
    runs = [
        _make_run(f"vrun-{i:07d}", decision, composite, created_at)
        for i, (decision, composite, created_at) in enumerate(specs)
    ]
    for run in runs:
        store.save(run)
    return runs


#: A 4-run fixture mix: three approvals, one block; newest (``...00:03``) at 72.5.
_MIXED_SPECS: list[tuple[str, float, str]] = [
    ("APPROVE", 91.0, "2026-01-01T00:00:00+00:00"),
    ("APPROVE", 88.25, "2026-01-01T00:01:00+00:00"),
    ("BLOCK", 40.5, "2026-01-01T00:02:00+00:00"),
    ("APPROVE", 72.5, "2026-01-01T00:03:00+00:00"),
]


# --------------------------------------------------------------------------- #
# Exposition parsing helpers
# --------------------------------------------------------------------------- #
def _parse_labels(raw: str) -> dict[str, str]:
    """Return the label set of a sample line as a dict."""
    return {m.group("key"): m.group("value") for m in _LABEL_RE.finditer(raw)}


def _samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Parse every sample line, asserting each is syntactically valid."""
    parsed: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        assert match is not None, f"unparseable exposition line: {line!r}"
        labels = _parse_labels(match.group("labels") or "")
        parsed.append((match.group("name"), labels, float(match.group("value"))))
    return parsed


def _series(text: str, name: str) -> list[tuple[dict[str, str], float]]:
    """All ``(labels, value)`` pairs published under ``name``."""
    return [(labels, value) for metric, labels, value in _samples(text) if metric == name]


def _value(text: str, name: str, **labels: str) -> float:
    """Value of the single series ``name`` with exactly ``labels``."""
    wanted = {
        (key, _escape_label_text(value)) for key, value in labels.items()
    }
    matches = [value for got, value in _series(text, name) if set(got.items()) == wanted]
    assert len(matches) == 1, (
        f"expected exactly one {name}{sorted(labels.items())} series, got {matches!r}"
    )
    return matches[0]


def _escape_label_text(value: str) -> str:
    """Mirror :func:`validsim.api.metrics._escape_label` for label matching."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _declared_types(text: str) -> dict[str, str]:
    """Map each metric name to the type advertised by its ``# TYPE`` line."""
    types: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            _, _, rest = line.partition("# TYPE ")
            name, _, kind = rest.partition(" ")
            types[name] = kind
    return types


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise deployment env so defaults are reproducible per test."""
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _restore_validsim_logger():
    """Snapshot the ``validsim`` logger so logging tests cannot leak state."""
    logger = logging.getLogger("validsim")
    handlers = list(logger.handlers)
    level, propagate = logger.level, logger.propagate
    configured = getattr(logger, _CONFIGURED_MARKER, None)
    try:
        yield
    finally:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        if configured is None:
            try:
                delattr(logger, _CONFIGURED_MARKER)
            except AttributeError:  # pragma: no cover - marker absent
                pass
        else:
            setattr(logger, _CONFIGURED_MARKER, configured)


@pytest.fixture()
def store() -> ValidationStore:
    """A fresh, empty in-memory store (the scrape's only data source)."""
    return ValidationStore()


@pytest.fixture()
def seeded_store(store: ValidationStore) -> ValidationStore:
    """Store holding the 3 APPROVE + 1 BLOCK mix in :data:`_MIXED_SPECS`."""
    _seed(store, _MIXED_SPECS)
    return store


@pytest.fixture()
def client(seeded_store: ValidationStore) -> TestClient:
    """TestClient over an app whose injected store carries the known mix."""
    return TestClient(create_app(seeded_store))


# --------------------------------------------------------------------------- #
# Run / approval / block counters
# --------------------------------------------------------------------------- #
class TestRunCounters:
    def test_counts_reflect_seeded_store_when_rendered(self, seeded_store: ValidationStore) -> None:
        text = render_metrics(seeded_store, Metrics())
        assert _value(text, "validsim_runs_total") == 4
        assert _value(text, "validsim_approvals_total") == 3
        assert _value(text, "validsim_blocks_total") == 1

    def test_counts_are_exact_in_raw_exposition_text(
        self, seeded_store: ValidationStore
    ) -> None:
        text = render_metrics(seeded_store, Metrics())
        assert "\nvalidsim_runs_total 4\n" in text
        assert "\nvalidsim_approvals_total 3\n" in text
        assert "\nvalidsim_blocks_total 1\n" in text

    def test_counts_served_over_http_match_the_injected_store(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        text = response.text
        assert _value(text, "validsim_runs_total") == 4
        assert _value(text, "validsim_approvals_total") == 3
        assert _value(text, "validsim_blocks_total") == 1

    def test_all_approve_and_all_block_mixes(self, store: ValidationStore) -> None:
        _seed(
            store,
            [
                ("APPROVE", 95.0, "2026-01-01T00:00:00+00:00"),
                ("APPROVE", 86.0, "2026-01-01T00:01:00+00:00"),
                ("APPROVE", 99.99, "2026-01-01T00:02:00+00:00"),
            ],
        )
        text = render_metrics(store, Metrics())
        assert _value(text, "validsim_approvals_total") == 3
        assert _value(text, "validsim_blocks_total") == 0

        empty = ValidationStore()
        _seed(empty, [("BLOCK", 10.0, "2026-01-01T00:00:00+00:00")])
        blocked = render_metrics(empty, Metrics())
        assert _value(blocked, "validsim_approvals_total") == 0
        assert _value(blocked, "validsim_blocks_total") == 1

    def test_blocks_plus_approvals_always_equals_runs(self, store: ValidationStore) -> None:
        _seed(
            store,
            [
                ("BLOCK", 12.5, "2026-01-01T00:00:00+00:00"),
                ("APPROVE", 90.0, "2026-01-01T00:01:00+00:00"),
                ("BLOCK", 84.99, "2026-01-01T00:02:00+00:00"),
                ("APPROVE", 85.0, "2026-01-01T00:03:00+00:00"),
                ("BLOCK", 0.0, "2026-01-01T00:04:00+00:00"),
            ],
        )
        text = render_metrics(store, Metrics())
        runs = _value(text, "validsim_runs_total")
        assert runs == 5
        assert _value(text, "validsim_approvals_total") + _value(
            text, "validsim_blocks_total"
        ) == runs

    def test_empty_store_is_zero_valued_not_absent(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        assert "validsim_runs_total 0" in text
        assert "validsim_approvals_total 0" in text
        assert "validsim_blocks_total 0" in text
        assert _value(text, "validsim_runs_total") == 0

    def test_counts_track_store_mutation_between_scrapes(self, store: ValidationStore) -> None:
        client = TestClient(create_app(store))
        first = client.get("/metrics").text
        assert _value(first, "validsim_runs_total") == 0
        assert _value(first, "validsim_blocks_total") == 0

        store.save(_make_run("vrun-0000000", "BLOCK", 33.0, "2026-01-01T00:00:00+00:00"))
        store.save(_make_run("vrun-0000001", "APPROVE", 87.0, "2026-01-01T00:01:00+00:00"))

        second = client.get("/metrics").text
        assert _value(second, "validsim_runs_total") == 2
        assert _value(second, "validsim_approvals_total") == 1
        assert _value(second, "validsim_blocks_total") == 1

        assert store.delete("vrun-0000000") is True
        third = client.get("/metrics").text
        assert _value(third, "validsim_runs_total") == 1
        assert _value(third, "validsim_blocks_total") == 0

    def test_run_series_are_gauges_because_deletion_lowers_them(
        self, store: ValidationStore
    ) -> None:
        """A ``counter`` must be monotonic; these series demonstrably are not.

        Runs are deletable, so ``validsim_runs_total`` falls from 2 to 1 after
        a delete. Advertising that as a ``counter`` makes ``rate()`` return a
        negative value on the next scrape and lets alerts such as
        ``increase(...[5m]) < 0`` fire backwards. The value behaviour asserted
        above is correct and intentional -- it is the *declared type* that must
        be a gauge.
        """
        store.save(_make_run("vrun-0000000", "BLOCK", 10.0, "2026-01-01T00:00:00+00:00"))
        store.save(_make_run("vrun-0000001", "APPROVE", 90.0, "2026-01-01T00:01:00+00:00"))
        client = TestClient(create_app(store))
        before = client.get("/metrics").text
        assert _declared_types(before)["validsim_runs_total"] == "gauge"
        assert _value(before, "validsim_runs_total") == 2

        assert store.delete("vrun-0000000") is True
        after = client.get("/metrics").text
        assert _value(after, "validsim_runs_total") == 1
        # Still a gauge after the decrease -- the type must not flip back.
        assert _declared_types(after)["validsim_runs_total"] == "gauge"


# --------------------------------------------------------------------------- #
# Composite score gauge
# --------------------------------------------------------------------------- #
class TestCompositeScoreGauge:
    def test_gauge_reports_the_latest_run_score(self, seeded_store: ValidationStore) -> None:
        text = render_metrics(seeded_store, Metrics())
        # Newest created_at in the mix is the 72.5 approval.
        assert _value(text, "validsim_composite_score") == 72.5
        assert "validsim_composite_score 72.5" in text

    def test_gauge_follows_created_at_not_insertion_order(self, store: ValidationStore) -> None:
        # Inserted newest-first: history() sorts oldest-first, so the gauge
        # must pick the record with the greatest timestamp (55.5), not the
        # last one saved (11.0).
        _seed(
            store,
            [
                ("APPROVE", 55.5, "2026-03-01T00:00:00+00:00"),
                ("BLOCK", 11.0, "2026-02-01T00:00:00+00:00"),
            ],
        )
        text = render_metrics(store, Metrics())
        assert _value(text, "validsim_composite_score") == 55.5

    def test_gauge_updates_after_a_newer_run_arrives(
        self, client: TestClient, seeded_store: ValidationStore
    ) -> None:
        assert _value(client.get("/metrics").text, "validsim_composite_score") == 72.5
        seeded_store.save(
            _make_run("vrun-latest1", "BLOCK", 6.75, "2026-01-01T12:00:00+00:00")
        )
        body = client.get("/metrics").text
        assert _value(body, "validsim_composite_score") == 6.75
        assert _value(body, "validsim_runs_total") == 5

    def test_integer_scores_are_rendered_without_a_trailing_decimal(
        self, store: ValidationStore
    ) -> None:
        _seed(store, [("APPROVE", 90.0, "2026-01-01T00:00:00+00:00")])
        assert "validsim_composite_score 90\n" in render_metrics(store, Metrics())

    def test_gauge_is_zero_on_an_empty_store(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        assert _value(text, "validsim_composite_score") == 0.0
        assert "validsim_composite_score 0" in text


# --------------------------------------------------------------------------- #
# Build info
# --------------------------------------------------------------------------- #
class TestBuildInfo:
    def test_build_info_carries_the_version_label(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        series = _series(text, "validsim_build_info")
        assert len(series) == 1
        labels, value = series[0]
        assert labels == {"version": __version__}
        assert value == 1

    def test_build_info_sample_line_is_verbatim(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        assert f'validsim_build_info{{version="{__version__}"}} 1' in text

    def test_build_info_matches_the_api_version_reported_by_health(
        self, client: TestClient
    ) -> None:
        health = client.get("/api/v1/health").json()
        body = client.get("/metrics").text
        assert _value(body, "validsim_build_info", version=health["version"]) == 1
        assert health["version"] == __version__


# --------------------------------------------------------------------------- #
# HTTP request counter (middleware-driven)
# --------------------------------------------------------------------------- #
class TestHTTPRequestCounter:
    def test_counter_increments_per_request_by_status_class(self, client: TestClient) -> None:
        for _ in range(3):
            assert client.get("/api/v1/health").status_code == 200
        for _ in range(2):
            assert client.get("/api/v1/validations/vrun-ffffffff").status_code == 404

        counts = client.get("/metrics").text
        # The scrape renders the counters *before* its own increment lands.
        assert _value(counts, HTTP_METRIC_NAME, **{"class": "2xx"}) == 3
        assert _value(counts, HTTP_METRIC_NAME, **{"class": "4xx"}) == 2
        assert _value(counts, HTTP_METRIC_NAME, **{"class": "3xx"}) == 0
        assert _value(counts, HTTP_METRIC_NAME, **{"class": "5xx"}) == 0

        second = client.get("/metrics").text
        assert _value(second, HTTP_METRIC_NAME, **{"class": "2xx"}) == 4

    def test_server_errors_are_counted_in_the_5xx_class(self) -> None:
        app: FastAPI = create_app(ValidationStore())

        @app.get("/boom")
        def boom() -> Any:  # pragma: no cover - always raises
            raise RuntimeError("intentional failure for 5xx coverage")

        # raise_server_exceptions=False keeps the 500 inside the ASGI stack so
        # the observability middleware still observes (and counts) the status.
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/boom").status_code == 500

        body = client.get("/metrics").text
        assert _value(body, HTTP_METRIC_NAME, **{"class": "5xx"}) == 1
        assert _value(body, HTTP_METRIC_NAME, **{"class": "2xx"}) == 0

    def test_each_status_class_series_is_always_present(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        labels = [labels for labels, _ in _series(text, HTTP_METRIC_NAME)]
        assert [labels.get("class") for labels in labels] == list(STATUS_CLASSES)
        assert all(value == 0 for _, value in _series(text, HTTP_METRIC_NAME))

    def test_render_reflects_directly_observed_statuses(self) -> None:
        metrics = Metrics()
        for status in (200, 201, 204):
            metrics.observe_status(status)
        metrics.observe_status(404)
        metrics.observe_status(503)

        text = render_metrics(ValidationStore(), metrics)
        assert _value(text, HTTP_METRIC_NAME, **{"class": "2xx"}) == 3
        assert _value(text, HTTP_METRIC_NAME, **{"class": "4xx"}) == 1
        assert _value(text, HTTP_METRIC_NAME, **{"class": "5xx"}) == 1
        assert _value(text, HTTP_METRIC_NAME, **{"class": "3xx"}) == 0

    def test_informational_statuses_are_ignored(self) -> None:
        metrics = Metrics()
        for status in (100, 101, 199, 600, 0, -1):
            metrics.observe_status(status)
        snapshot = metrics.snapshot_http()
        assert sorted(snapshot) == sorted(STATUS_CLASSES)
        assert sum(snapshot.values()) == 0

    def test_counter_is_scoped_to_one_app_instance(self) -> None:
        first = TestClient(create_app(ValidationStore()))
        second = TestClient(create_app(ValidationStore()))
        first.get("/api/v1/health")
        first.get("/api/v1/health")
        second.get("/api/v1/health")

        assert _value(first.get("/metrics").text, HTTP_METRIC_NAME, **{"class": "2xx"}) == 2
        assert _value(second.get("/metrics").text, HTTP_METRIC_NAME, **{"class": "2xx"}) == 1


# --------------------------------------------------------------------------- #
# Exposition format / transport
# --------------------------------------------------------------------------- #
class TestExpositionFormat:
    def test_metrics_content_type_is_prometheus_text(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        content_type = response.headers["content-type"]
        assert content_type.startswith("text/plain")
        assert "version=" in content_type
        assert content_type == PROMETHEUS_CONTENT_TYPE

    def test_prefixed_scrape_shares_the_content_type(self, client: TestClient) -> None:
        prefixed = client.get("/api/v1/metrics")
        assert prefixed.status_code == 200
        assert prefixed.headers["content-type"] == PROMETHEUS_CONTENT_TYPE

        root = client.get("/metrics")
        # Both mounts render the same store-derived gauges; only the in-process
        # HTTP counter advances, by the extra scrape made in between.
        for name in (
            "validsim_runs_total",
            "validsim_approvals_total",
            "validsim_blocks_total",
            "validsim_composite_score",
        ):
            assert _value(prefixed.text, name) == _value(root.text, name)
        build_line = f'validsim_build_info{{version="{__version__}"}} 1'
        assert build_line in prefixed.text and build_line in root.text
        assert _value(root.text, HTTP_METRIC_NAME, **{"class": "2xx"}) == _value(
            prefixed.text, HTTP_METRIC_NAME, **{"class": "2xx"}
        ) + 1

    def test_every_metric_declares_help_and_type(self, client: TestClient) -> None:
        text = client.get("/metrics").text
        types = _declared_types(text)
        assert types == _EXPECTED_TYPES
        lines = text.splitlines()
        for name in _EXPECTED_TYPES:
            assert f"# HELP {name} " in text, f"missing HELP for {name}"
            type_index = next(
                i for i, line in enumerate(lines) if line.startswith(f"# TYPE {name}")
            )
            sample_index = next(
                i for i, line in enumerate(lines)
                if line.startswith(name + " ") or line.startswith(name + "{")
            )
            assert type_index < sample_index, f"TYPE for {name} must precede samples"

    def test_no_duplicate_series_are_emitted(self, seeded_store: ValidationStore) -> None:
        text = render_metrics(seeded_store, Metrics())
        keys = [
            (name, tuple(sorted(labels.items()))) for name, labels, _ in _samples(text)
        ]
        assert len(keys) == len(set(keys))

    def test_body_is_newline_terminated_with_no_blank_lines(
        self, seeded_store: ValidationStore
    ) -> None:
        text = render_metrics(seeded_store, Metrics())
        assert text.endswith("\n")
        assert "" not in text[:-1].split("\n")

    def test_scrape_is_stable_and_repeatable(self, client: TestClient) -> None:
        first = client.get("/metrics").text
        second = client.get("/metrics").text
        # Only the HTTP counter series may move between two scrapes.
        assert _value(first, "validsim_runs_total") == _value(second, "validsim_runs_total")
        assert _value(first, "validsim_composite_score") == _value(
            second, "validsim_composite_score"
        )
        assert _value(second, HTTP_METRIC_NAME, **{"class": "2xx"}) == _value(
            first, HTTP_METRIC_NAME, **{"class": "2xx"}
        ) + 1

    def test_empty_store_still_exposes_every_metric(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        assert set(_declared_types(text)) == set(_EXPECTED_TYPES)
        names = {name for name, _, _ in _samples(text)}
        assert names == set(_EXPECTED_TYPES)


# --------------------------------------------------------------------------- #
# Logging idempotency
# --------------------------------------------------------------------------- #
class TestConfigureLoggingIdempotent:
    def _json_handlers(self) -> list[logging.Handler]:
        logger = logging.getLogger("validsim")
        return [h for h in logger.handlers if getattr(h, _HANDLER_MARKER, False)]

    def test_second_call_does_not_duplicate_handlers(self) -> None:
        logger = configure_logging()
        assert logger.name == "validsim"
        before = list(logger.handlers)
        json_handlers = self._json_handlers()
        assert len(json_handlers) == 1
        original = json_handlers[0]

        again = configure_logging()
        configure_logging()

        assert again is logger
        assert list(logger.handlers) == before
        # Still exactly one ValidSim JSON handler, and it is the same instance.
        assert self._json_handlers() == [original]
        assert getattr(logger, _CONFIGURED_MARKER, False) is True

    def test_repeated_create_app_keeps_one_handler(self) -> None:
        configure_logging()
        count = len(logging.getLogger("validsim").handlers)
        for _ in range(3):
            create_app(ValidationStore())
        assert len(logging.getLogger("validsim").handlers) == count
        assert len(self._json_handlers()) == 1

    def test_force_rebuilds_without_leaving_extra_handlers(self) -> None:
        configure_logging()
        total_before = len(logging.getLogger("validsim").handlers)
        original = self._json_handlers()[0]

        configure_logging(force=True)

        handlers = self._json_handlers()
        assert handlers and handlers[0] is not original
        assert len(handlers) == 1
        assert len(logging.getLogger("validsim").handlers) == total_before

    def test_configured_logger_emits_a_single_json_line(self) -> None:
        import io
        import json

        stream = io.StringIO()
        configure_logging(level="INFO", stream=stream, force=True)
        logger = configure_logging(stream=stream)
        logger.info("one line only", extra={"request_id": "req-1"})
        stream.flush()

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        # Exactly one handler -> exactly one emitted record.
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["message"] == "one line only"
        assert payload["request_id"] == "req-1"
        assert payload["logger"] == "validsim"
        assert logger.propagate is False
