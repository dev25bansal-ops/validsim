"""Temporary probe batch 4: tie ordering after re-save, cross-instance locking."""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

tmp = Path(tempfile.mkdtemp())
SAME_TS = "2026-01-01T00:00:00+00:00"


def mk(run_id, *, ckpt="ckpt-1", created_at=SAME_TS):
    sc = Scorecard(
        run_id=run_id, checkpoint_id=ckpt, task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=80.0, robustness_score=100.0,
        regression_delta=None, confidence_interval=None, deploy_decision="APPROVE",
        threshold=85.0, created_at=created_at, episode_count=1, failure_taxonomy={},
    )
    return StoredRun(
        run_id=run_id, checkpoint_id=ckpt, task_id=sc.task_id, created_at=created_at,
        scorecard=sc, evaluation=EvaluationResult(1, 1, 1.0),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


def probe(label, fn):
    try:
        print(f"[{label}] -> {fn()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] !! {type(exc).__name__}: {exc}")


# --- TIE ORDER after re-save (all same created_at) -------------------------
mem = ValidationStore()
sq = SqliteValidationStore(tmp / "tie.db")
for rid in ("a", "b", "c", "d"):
    mem.save(mk(rid))
    sq.save(mk(rid))
probe("memory: order after inserts", lambda: [r.run_id for r in mem.history()])
probe("sqlite:  order after inserts", lambda: [r.run_id for r in sq.history()])
# re-save 'a' -- dict keeps its slot; INSERT OR REPLACE gives a new rowid
mem.save(mk("a"))
sq.save(mk("a"))
probe("memory: order after re-save 'a'", lambda: [r.run_id for r in mem.history()])
probe("sqlite:  order after re-save 'a'", lambda: [r.run_id for r in sq.history()])
probe("memory: list_for_checkpoint after re-save",
      lambda: [r.run_id for r in mem.list_for_checkpoint("ckpt-1")])
probe("sqlite:  list_for_checkpoint after re-save",
      lambda: [r.run_id for r in sq.list_for_checkpoint("ckpt-1")])

# --- TIE ORDER after delete + re-insert ------------------------------------
mem2 = ValidationStore()
sq2 = SqliteValidationStore(tmp / "tie2.db")
for rid in ("a", "b", "c"):
    mem2.save(mk(rid))
    sq2.save(mk(rid))
mem2.delete("b")
sq2.delete("b")
mem2.save(mk("b"))
sq2.save(mk("b"))
probe("memory: order after delete+reinsert 'b'", lambda: [r.run_id for r in mem2.history()])
probe("sqlite:  order after reinsert 'b'", lambda: [r.run_id for r in sq2.history()])

# --- CROSS-INSTANCE concurrency on the same sqlite file --------------------
path = tmp / "shared.db"
s1 = SqliteValidationStore(path)
s2 = SqliteValidationStore(path)
errs: list[str] = []
lock = threading.Lock()


def w(store, prefix, n=150):
    for i in range(n):
        try:
            store.save(mk(f"{prefix}-{i:05d}"))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errs.append(f"{prefix}: {type(exc).__name__}: {exc}")


ts = [threading.Thread(target=w, args=(s1, "s1")), threading.Thread(target=w, args=(s2, "s2"))]
for t in ts:
    t.start()
for t in ts:
    t.join()
probe("cross-instance: error count", lambda: len(errs))
probe("cross-instance: first error", lambda: errs[0] if errs else "none")
probe("cross-instance: expected rows 300", lambda: s1.count())
