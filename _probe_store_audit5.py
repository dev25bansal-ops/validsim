"""Temporary probe batch 5: re-verify findings against the append-only contract."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

tmp = Path(tempfile.mkdtemp())


def ep(i):
    return EpisodeResult(episode_id=f"ep-{i}", task_id="t", seed=i, success=i % 2 == 0)


def run(run_id, *, ckpt="ckpt-1", created_at="2026-01-01T00:00:00+00:00",
        card_ckpt=None, card_created=None, composite=90.0, episodes=None):
    sc = Scorecard(
        run_id=run_id, checkpoint_id=card_ckpt or ckpt, task_id="t",
        composite_score=composite, success_rate=0.9, safety_score=80.0,
        robustness_score=100.0, regression_delta=None, confidence_interval=(0.8, 0.95),
        deploy_decision="APPROVE", threshold=85.0, created_at=card_created or created_at,
        episode_count=2, failure_taxonomy={"collision": 1},
    )
    return StoredRun(
        run_id=run_id, checkpoint_id=ckpt, task_id="t", created_at=created_at,
        scorecard=sc, evaluation=EvaluationResult(2, 1, 0.5, {}, {"collision": 1}, 2.5),
        safety=SafetyResult(0.5, 0.0, 0.8, 0.0, 75.0),
        episodes=episodes if episodes is not None else [ep(0), ep(1)],
        baseline_run_id="vrun-base1",
        regression=RegressionReport(
            items=[RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.01, True, "critical")]),
    )


def probe(label, fn):
    try:
        print(f"[{label}] -> {fn()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] !! {type(exc).__name__}: {exc}")


# --- save_exists parity ----------------------------------------------------
mem, sq = ValidationStore(), SqliteValidationStore(tmp / "se.db")
probe("save_exists new (memory)", lambda: mem.save_exists(run("a")))
probe("save_exists dup (memory)", lambda: mem.save_exists(run("a")))
probe("save_exists new (sqlite)", lambda: sq.save_exists(run("a")))
probe("save_exists dup (sqlite)", lambda: sq.save_exists(run("a")))
probe("save_exists count mem/sq", lambda: (mem.count(), sq.count()))
probe("save_exists stores caller obj (memory)", lambda: mem.get("a") is not None)
probe("save_exists then get (sqlite)", lambda: sq.get("a") is not None)

# --- append-only: does re-save mutate? ------------------------------------
mem2, sq2 = ValidationStore(), SqliteValidationStore(tmp / "ao.db")
for st in (mem2, sq2):
    st.save(run("x", ckpt="ckpt-A"))
    st.save(run("x", ckpt="ckpt-B"))
probe("append-only: memory keeps first", lambda: mem2.get("x").checkpoint_id)
probe("append-only: sqlite  keeps first", lambda: sq2.get("x").checkpoint_id)
probe("append-only: identical", lambda: mem2.get("x").checkpoint_id == sq2.get("x").checkpoint_id)

# --- div-1 NaN (recheck) ---------------------------------------------------
import math
import sqlite3
mem3, sq3 = ValidationStore(), SqliteValidationStore(tmp / "nan.db")
probe("div1: memory save NaN", lambda: mem3.save(run("n", composite=float("nan"))) is not None)
probe("div1: memory count", lambda: mem3.count())
probe("div1: sqlite save NaN", lambda: sq3.save(run("n", composite=float("nan"))) is not None)
probe("div1: sqlite count", lambda: sq3.count())
probe("div1: memory get present", lambda: mem3.get("n") is not None)
probe("div1: sqlite  get present", lambda: sq3.get("n") is not None)

# --- div-2 attribute collapse (recheck) ------------------------------------
mem4, sq4 = ValidationStore(), SqliteValidationStore(tmp / "d2.db")
r = run("y", ckpt="ckpt-top", card_ckpt="ckpt-card")
mem4.save(r); sq4.save(r)
probe("div2: memory get checkpoint", lambda: mem4.get("y").checkpoint_id)
probe("div2: sqlite  get checkpoint", lambda: sq4.get("y").checkpoint_id)
probe("div2: memory lfc(ckpt-top)", lambda: [x.run_id for x in mem4.list_for_checkpoint("ckpt-top")])
probe("div2: sqlite  lfc(ckpt-top)", lambda: [x.run_id for x in sq4.list_for_checkpoint("ckpt-top")])
probe("div2: sqlite  lfc(ckpt-card)", lambda: [x.run_id for x in sq4.list_for_checkpoint("ckpt-card")])
probe("div2: same input -> same answer", lambda: [x.run_id for x in mem4.list_for_checkpoint("ckpt-top")] == [x.run_id for x in sq4.list_for_checkpoint("ckpt-top")])

# --- div-3 aliasing (recheck) ---------------------------------------------
mem5, sq5 = ValidationStore(), SqliteValidationStore(tmp / "d3.db")
eps = [ep(0)]
r2 = run("z", episodes=eps)
mem5.save(r2); sq5.save(r2)
eps.append(ep(1))
probe("div3: memory episodes", lambda: len(mem5.get("z").episodes))
probe("div3: sqlite  episodes", lambda: len(sq5.get("z").episodes))
probe("div3: equal", lambda: len(mem5.get("z").episodes) == len(sq5.get("z").episodes))
r3 = run("t2")
mem5.save(r3); sq5.save(r3)
r3.scorecard.failure_taxonomy["injected"] = 99
probe("div3b: memory taxonomy", lambda: mem5.get("t2").scorecard.failure_taxonomy)
probe("div3b: sqlite  taxonomy", lambda: sq5.get("t2").scorecard.failure_taxonomy)

# --- div-4 close (recheck) -------------------------------------------------
mem6, sq6 = ValidationStore(), SqliteValidationStore(tmp / "d4.db")
mem6.save(run("q")); sq6.save(run("q"))
mem6.close(); sq6.close()
probe("div4: memory count after close", lambda: mem6.count())
probe("div4: sqlite count after close", lambda: sq6.count())
probe("div4: sqlite save after close", lambda: sq6.save(run("new")) is not None)
probe("div4: memory save after close", lambda: mem6.save(run("new")) is not None)

# --- div-5 tie order after re-save (recheck: should be FIXED now) -----------
mem7, sq7 = ValidationStore(), SqliteValidationStore(tmp / "d5.db")
T = "2026-01-01T00:00:00+00:00"
for rid in ("a", "b", "c", "d"):
    mem7.save(run(rid, created_at=T)); sq7.save(run(rid, created_at=T))
mem7.save(run("a", created_at=T)); sq7.save(run("a", created_at=T))
probe("div5: memory order", lambda: [x.run_id for x in mem7.history()])
probe("div5: sqlite  order", lambda: [x.run_id for x in sq7.history()])
probe("div5: identical", lambda: [x.run_id for x in mem7.history()] == [x.run_id for x in sq7.history()])
