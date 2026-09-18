"""In-memory, thread-safe validation result store.

The MVP keeps completed runs in a process-local dictionary guarded by a
:class:`threading.Lock`. The interface (save/get/list/history) is deliberately
narrow so it can be backed by Postgres or an object store later without
touching callers.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult

__all__ = ["StoredRun", "ValidationStore"]


@dataclass(frozen=True)
class StoredRun:
    """A completed validation run with all engine artifacts.

    Attributes:
        run_id: Store-assigned id, format ``"vrun-<uuid8>"``.
        checkpoint_id: Checkpoint that was validated.
        task_id: Task that was executed.
        created_at: ISO-8601 UTC timestamp (mirrors the scorecard).
        scorecard: Final composite verdict.
        evaluation: Aggregate success/failure metrics.
        safety: Safety metrics for the run.
        episodes: Raw per-episode results (used by failure inspection).
        baseline_run_id: Baseline used at creation time, if any.
        regression: Baseline comparison report, if performed.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    created_at: str
    scorecard: Scorecard
    evaluation: EvaluationResult
    safety: SafetyResult
    episodes: list[EpisodeResult] = field(default_factory=list)
    baseline_run_id: str | None = None
    regression: RegressionReport | None = None

    def summary(self) -> dict[str, Any]:
        """Compact JSON-friendly run summary for list/detail endpoints."""
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "baseline_run_id": self.baseline_run_id,
            "composite_score": self.scorecard.composite_score,
            "deploy_decision": self.scorecard.deploy_decision,
            "episode_count": self.scorecard.episode_count,
        }


class ValidationStore:
    """Thread-safe dict-based store of :class:`StoredRun` records."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._runs: dict[str, StoredRun] = {}
        self._lock = threading.Lock()

    @staticmethod
    def new_run_id() -> str:
        """Generate a fresh run id like ``"vrun-1a2b3c4d"``."""
        return f"vrun-{uuid.uuid4().hex[:8]}"

    def save(self, run: StoredRun) -> StoredRun:
        """Persist ``run`` (keyed by ``run.run_id``) and return it.

        Re-saving an existing id overwrites the previous record.
        """
        with self._lock:
            self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> StoredRun | None:
        """Return the run for ``run_id`` or ``None`` if unknown."""
        with self._lock:
            return self._runs.get(run_id)

    def list_for_checkpoint(self, checkpoint_id: str) -> list[StoredRun]:
        """All runs for a checkpoint, oldest first."""
        with self._lock:
            matches = [r for r in self._runs.values() if r.checkpoint_id == checkpoint_id]
        return sorted(matches, key=lambda r: r.created_at)

    def history(self) -> list[StoredRun]:
        """Every stored run, oldest first."""
        with self._lock:
            runs = list(self._runs.values())
        return sorted(runs, key=lambda r: r.created_at)

    def __len__(self) -> int:
        """Number of stored runs."""
        with self._lock:
            return len(self._runs)

    def close(self) -> None:
        """Release backend resources (no-op for the in-memory store).

        Present so callers can treat every :func:`create_store` backend
        uniformly; the SQLite backend overrides this to close its connection.
        """
