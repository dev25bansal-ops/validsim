"""Temporary probe batch 2: sharper divergence candidates."""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

tmp = Path(tempfile.mkdtemp())


def mk(run_id, *, ckpt="ckpt-1", created_at="2026-01-01T00:00:00+00:00",
       card_run_id=None, card_ckpt=None, card_created=None, episodes=None,
       taxonomy=None):
    sc = Scorecard(
        run_id=card_run_id or run_id, checkpoint_id=card_ckpt or ckpt, task_id="t",
        composite_score=90.0, success_rate=0.9, safety_score=80.0,
        robustness_score=100.0, regression_delta=None, confidence_interval=None,
        deploy_decision="APPROVE", threshold=85.0,
        created_at=card_created or created_at, episode_count=1,
        failure_taxonomy=dict(taxonomy or {}),
    )
    return StoredRun(
        run_id=run_id, checkpoint_id=ckpt, task_id="t", created_at=created_at,
        scorecard=sc, evaluation=EvaluationResult(1, 1, 1.0),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0), episodes=list(episodes or []),
    )


def probe(label, fn):
    try:
        print(f"[{label}] -> {fn()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] !! {type(exc).__name__}: {exc}")


# --- A. save() under key K where scorecard.run_id != K ---------------------
weird = mk("KEY-1", ckpt="ckpt-1", card_run_id="OTHER-ID")
mem = ValidationStore(); sq = SqliteValidationStore(tmp / "a.db")
mem.save(weird); sq.save(weird)
probe("memory: get('KEY-1').run_id", lambda: mem.get("KEY-1").run_id)
probe("sqlite: get('KEY-1').run_id", lambda: sq.get("KEY-1").run_id)
probe("memory: count", lambda: mem.count())
probe("sqlite: count", lambda: sq.count())

# --- B. close() then save() -----------------------------------------------
mem2 = ValidationStore(); mem2.close()
probe("memory: save after close", lambda: mem2.save(mk("z")) is not None)
sq2 = SqliteValidationStore(tmp / "b.db"); sq2.close()
probe("sqlite: save after close", lambda: sq2.save(mk("z")) is not None)

# --- C. unicode / long ids -------------------------------------------------
for label, rid, ck in (
    ("unicode", "vrun-ünïcødé-🚀-运行", "ckpt-✓"),
    ("long", "v" + "x" * 4000, "c" * 4000),
    ("empty", "", ""),
):
    mem3 = ValidationStore(); sq3 = SqliteValidationStore(tmp / f"c-{label}.db")
    mem3.save(mk(rid, ckpt=ck)); sq3.save(mk(rid, ckpt=ck))
    probe(f"{label}: memory get hit", lambda: mem3.get(rid) is not None)
    probe(f"{label}: sqlite  get hit", lambda: sq3.get(rid) is not None)
    probe(f"{label}: memory list_for_checkpoint", lambda: len(mem3.list_for_checkpoint(ck)))
    probe(f"{label}: sqlite  list_for_checkpoint", lambda: len(sq3.list_for_checkpoint(ck)))
    probe(f"{label}: memory delete", lambda: mem3.delete(rid))
    probe(f"{label}: sqlite  delete", lambda: sq3.delete(rid))

# --- D. very long taxonomy -------------------------------------------------
big = {f"mode-{i:05d}-é": i for i in range(3000)}
mem4 = ValidationStore(); sq4 = SqliteValidationStore(tmp / "d.db")
mem4.save(mk("big", taxonomy=big)); sq4.save(mk("big", taxonomy=big))
probe("memory: big taxonomy size", lambda: len(mem4.get("big").scorecard.failure_taxonomy))
probe("sqlite:  big taxonomy size", lambda: len(sq4.get("big").scorecard.failure_taxonomy))
probe("sqlite:  big taxonomy equal", lambda: sq4.get("big").scorecard.failure_taxonomy == big)

# --- E. tuple-typed taxonomy values / non-dict JSON-safe values ------------
mem5 = ValidationStore(); sq5 = SqliteValidationStore(tmp / "e.db")
odd = mk("odd", taxonomy={"a": 1, "b": 0, "é": 10**12})
mem5.save(odd); sq5.save(odd)
probe("memory: odd taxonomy", lambda: mem5.get("odd").scorecard.failure_taxonomy)
probe("sqlite:  odd taxonomy", lambda: sq5.get("odd").scorecard.failure_taxonomy)

# --- F. float / NaN composite score ---------------------------------------
import math
mem6 = ValidationStore(); sq6 = SqliteValidationStore(tmp / "f.db")
nan = mk("nan")
object.__setattr__(nan.scorecard, "composite_score", float("nan"))
mem6.save(nan); sq6.save(nan)
probe("memory: nan composite", lambda: mem6.get("nan").scorecard.composite_score)
probe("sqlite:  nan composite", lambda: sq6.get("nan").scorecard.composite_score)
probe("memory: nan == nan (frozen dataclass eq)", lambda: mem6.get("nan") == mem6.get("nan"))
probe("sqlite:  nan == nan (frozen dataclass eq)", lambda: sq6.get("nan") == sq6.get("nan"))

# --- G. big int episode_count / large ints ---------------------------------
mem7 = ValidationStore(); sq7 = SqliteValidationStore(tmp / "g.db")
big_int = mk("bi")
object.__setattr__(big_int.scorecard, "episode_count", 2**53 + 1)
object.__setattr__(big_int.evaluation, "total_episodes", 2**53 + 1)
mem7.save(big_int); sq7.save(big_int)
probe("memory: big int episode_count", lambda: mem7.get("bi").scorecard.episode_count)
probe("sqlite:  big int episode_count", lambda: sq7.get("bi").scorecard.episode_count)

# --- H. concurrent delete + read on sqlite ---------------------------------
sq8 = SqliteValidationStore(tmp / "h.db")
for i in range(200):
    sq8.save(mk(f"c{i:04d}"))
errs: list[Exception] = []


def hammer(idx: int) -> None:
    try:
        for i in range(200):
            sq8.get(f"c{(i + idx) % 200:04d}")
            sq8.history()
            if i % 7 == 0:
                sq8.save(mk(f"c{(i + idx) % 200:04d}"))
    except Exception as exc:  # noqa: BLE001
        errs.append(exc)


ts = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
for t in ts:
    t.start()
for t in ts:
    t.join()
probe("sqlite: concurrent read/write errors", lambda: errs[:1] or "none")
probe("sqlite: count after churn", lambda: sq8.count())
