"""Temporary probe 6: does tied-timestamp ordering actually diverge today?"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

tmp = Path(tempfile.mkdtemp())
T = "2026-01-01T00:00:00+00:00"


def run(rid):
    sc = Scorecard(
        run_id=rid, checkpoint_id="ckpt-1", task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=80.0, robustness_score=100.0,
        regression_delta=None, confidence_interval=None, deploy_decision="APPROVE",
        threshold=85.0, created_at=T, episode_count=2, failure_taxonomy={},
    )
    return StoredRun(
        run_id=rid, checkpoint_id="ckpt-1", task_id="t", created_at=T, scorecard=sc,
        evaluation=EvaluationResult(2, 1, 0.5), safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


# 1) plain inserts, all tied
mem, sq = ValidationStore(), SqliteValidationStore(tmp / "a.db")
for rid in ("a", "b", "c", "d", "e", "f"):
    mem.save(run(rid)); sq.save(run(rid))
print("insert tie   mem:", [r.run_id for r in mem.history()])
print("insert tie   sq :", [r.run_id for r in sq.history()])

# 2) delete middle + reinsert
mem2, sq2 = ValidationStore(), SqliteValidationStore(tmp / "b.db")
for rid in ("a", "b", "c", "d", "e", "f"):
    mem2.save(run(rid)); sq2.save(run(rid))
for st in (mem2, sq2):
    st.delete("c"); st.save(run("c"))
print("reinsert tie mem:", [r.run_id for r in mem2.history()])
print("reinsert tie sq :", [r.run_id for r in sq2.history()])

# 3) reverse insertion order, tied
mem3, sq3 = ValidationStore(), SqliteValidationStore(tmp / "c.db")
for rid in ("f", "e", "d", "c", "b", "a"):
    mem3.save(run(rid)); sq3.save(run(rid))
print("reverse tie mem:", [r.run_id for r in mem3.history()])
print("reverse tie sq :", [r.run_id for r in sq3.history()])

# 4) EXPLAIN the ORDER BY to show there is no tiebreaker
for sql, in sq3._conn.execute("EXPLAIN QUERY PLAN SELECT * FROM validations ORDER BY created_at ASC"):
    print("PLAN:", sql)
print("indexes:", [r["name"] for r in sq3._conn.execute("PRAGMA index_list(validations)")])
