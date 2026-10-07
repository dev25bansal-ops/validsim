"""Temporary probe: find memory/sqlite store contract divergences."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore


def mk(run_id, *, ckpt="ckpt-1", created_at="2026-01-01T00:00:00+00:00", episodes=None,
       scorecard=None):
    sc = scorecard or Scorecard(
        run_id=run_id, checkpoint_id=ckpt, task_id="t",
        composite_score=90.0, success_rate=0.9, safety_score=80.0,
        robustness_score=100.0, regression_delta=None, confidence_interval=None,
        deploy_decision="APPROVE", threshold=85.0, created_at=created_at,
        episode_count=1, failure_taxonomy={},
    )
    return StoredRun(
        run_id=run_id, checkpoint_id=ckpt, task_id=sc.task_id,
        created_at=created_at, scorecard=sc,
        evaluation=EvaluationResult(1, 1, 1.0), safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
        episodes=list(episodes or []),
    )


def probe(label, fn):
    try:
        print(f"[{label}] -> {fn()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] !! {type(exc).__name__}: {exc}")


tmp = Path(tempfile.mkdtemp())

# --- 1. close() then reuse -------------------------------------------------
mem = ValidationStore()
mem.save(mk("r1"))
mem.close()
probe("memory: use after close -> get", lambda: mem.get("r1") is not None)
probe("memory: use after close -> count", lambda: mem.count())

sq = SqliteValidationStore(tmp / "a.db")
sq.save(mk("r1"))
sq.close()
probe("sqlite: use after close -> get", lambda: sq.get("r1") is not None)
probe("sqlite: use after close -> count", lambda: sq.count())

# --- 2. re-save same id ----------------------------------------------------
sq2 = SqliteValidationStore(tmp / "b.db")
for st, label in ((mem, "memory"), (sq2, "sqlite")):
    if label == "sqlite":
        pass
sq2.save(mk("r2", ckpt="ckpt-A"))
sq2.save(mk("r2", ckpt="ckpt-B"))
probe("sqlite: re-save changed checkpoint_id", lambda: sq2.get("r2").checkpoint_id)
mem2 = ValidationStore()
mem2.save(mk("r2", ckpt="ckpt-A"))
mem2.save(mk("r2", ckpt="ckpt-B"))
probe("memory: re-save changed checkpoint_id", lambda: mem2.get("r2").checkpoint_id)

# --- 3. aliasing: caller mutates episodes after save -----------------------
ep = [EpisodeResult(episode_id="e1", task_id="t", seed=1, success=True)]
r = mk("r3", episodes=ep)
mem3 = ValidationStore()
mem3.save(r)
r.episodes.append(EpisodeResult(episode_id="e2", task_id="t", seed=2, success=False))
probe("memory: episodes after caller mutation", lambda: len(mem3.get("r3").episodes))
sq3 = SqliteValidationStore(tmp / "c.db")
r2 = mk("r4", episodes=ep)
sq3.save(r2)
r2.episodes.append(EpisodeResult(episode_id="e2", task_id="t", seed=2, success=False))
probe("sqlite: episodes after caller mutation", lambda: len(sq3.get("r4").episodes))

# --- 4. StoredRun top-level fields vs scorecard fields ---------------------
weird = mk("r5", ckpt="TOP", created_at="2026-06-01T00:00:00+00:00",
           scorecard=Scorecard(run_id="r5", checkpoint_id="CARD", task_id="t",
                               composite_score=90.0, success_rate=0.9, safety_score=80.0,
                               robustness_score=100.0, regression_delta=None,
                               confidence_interval=None, deploy_decision="APPROVE",
                               threshold=85.0, created_at="2020-01-01T00:00:00+00:00",
                               episode_count=1, failure_taxonomy={}))
mem5 = ValidationStore(); mem5.save(weird)
sq5 = SqliteValidationStore(tmp / "d.db"); sq5.save(weird)
probe("memory: get().checkpoint_id", lambda: mem5.get("r5").checkpoint_id)
probe("sqlite: get().checkpoint_id", lambda: sq5.get("r5").checkpoint_id)
probe("memory: get().created_at", lambda: mem5.get("r5").created_at)
probe("sqlite: get().created_at", lambda: sq5.get("r5").created_at)
probe("memory: list_for_checkpoint('TOP')", lambda: len(mem5.list_for_checkpoint("TOP")))
probe("memory: list_for_checkpoint('CARD')", lambda: len(mem5.list_for_checkpoint("CARD")))
probe("sqlite: list_for_checkpoint('TOP')", lambda: len(sq5.list_for_checkpoint("TOP")))
probe("sqlite: list_for_checkpoint('CARD')", lambda: len(sq5.list_for_checkpoint("CARD")))
probe("memory: history(since 2026)", lambda: [x.run_id for x in mem5.history(since="2026-01-01T00:00:00+00:00")])
probe("sqlite: history(since 2026)", lambda: [x.run_id for x in sq5.history(since="2026-01-01T00:00:00+00:00")])

# --- 5. tie ordering on identical created_at -------------------------------
mem6 = ValidationStore(); sq6 = SqliteValidationStore(tmp / "e.db")
for i in range(6):
    mem6.save(mk(f"t{i}"))
    sq6.save(mk(f"t{i}"))
probe("memory: tie order", lambda: [x.run_id for x in mem6.history()])
probe("sqlite:  tie order", lambda: [x.run_id for x in sq6.history()])
probe("memory: tie order (again)", lambda: [x.run_id for x in mem6.history()])
probe("sqlite:  tie order (again)", lambda: [x.run_id for x in sq6.history()])

# --- 6. object identity ----------------------------------------------------
mem7 = ValidationStore(); mem7.save(mk("x"))
probe("memory: get() is the saved object", lambda: mem7.get("x") is mem7._runs["x"])
sq7 = SqliteValidationStore(tmp / "f.db"); saved = mk("y"); sq7.save(saved)
probe("sqlite: get() is the saved object", lambda: sq7.get("y") is saved)
probe("sqlite: get() == saved (dataclass eq)", lambda: sq7.get("y") == saved)
mem8 = ValidationStore(); mem8.save(saved)
probe("memory: get() == saved (dataclass eq)", lambda: mem8.get("y") == saved)
