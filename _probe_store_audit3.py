"""Temporary probe batch 3: NaN / big-int / float precision through JSON."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.postgres import _run_from_row as pg_row
from validsim.store.sqlite import SqliteValidationStore, _run_from_row as lite_row

tmp = Path(tempfile.mkdtemp())


def mk(run_id, *, sc_overrides=None):
    base = dict(
        run_id=run_id, checkpoint_id="ckpt-1", task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=80.0, robustness_score=100.0,
        regression_delta=None, confidence_interval=None, deploy_decision="APPROVE",
        threshold=85.0, created_at="2026-01-01T00:00:00+00:00", episode_count=1,
        failure_taxonomy={},
    )
    base.update(sc_overrides or {})
    sc = Scorecard(**base)
    return StoredRun(
        run_id=run_id, checkpoint_id=sc.checkpoint_id, task_id=sc.task_id,
        created_at=sc.created_at, scorecard=sc,
        evaluation=EvaluationResult(1, 1, 1.0),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


def probe(label, fn):
    try:
        print(f"[{label}] -> {fn()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] !! {type(exc).__name__}: {exc}")


# --- NaN through the JSON scorecard path -----------------------------------
nan = mk("nan", sc_overrides={"composite_score": float("nan")})
sq = SqliteValidationStore(tmp / "nan.db")
probe("sqlite: save NaN composite", lambda: sq.save(nan) is not None)
probe("sqlite: raw json has NaN token", lambda: "NaN" in nan.scorecard.to_json())
got = sq.get("nan")
probe("sqlite: get NaN composite is nan", lambda: math.isnan(got.scorecard.composite_score))
probe("sqlite: get() == original (dataclass eq)", lambda: sq.get("nan") == nan)
mem = ValidationStore(); mem.save(nan)
probe("memory: get() == original (dataclass eq)", lambda: mem.get("nan") == nan)
probe("memory-vs-sqlite equal", lambda: mem.get("nan") == sq.get("nan"))
# postgres reconstruction from the same JSONB dict
probe("postgres: reconstruct == sqlite reconstruct",
      lambda: pg_row((nan.scorecard.to_dict(), None, None, None, None, None)) == sq.get("nan"))

# --- Infinity --------------------------------------------------------------
inf = mk("inf", sc_overrides={"composite_score": float("inf")})
sq2 = SqliteValidationStore(tmp / "inf.db")
probe("sqlite: save inf composite", lambda: sq2.save(inf) is not None)
probe("sqlite: get inf", lambda: sq2.get("inf").scorecard.composite_score)

# --- big ints (2**53+1) through JSON ---------------------------------------
bi = mk("bi", sc_overrides={"episode_count": 2**53 + 1})
sq3 = SqliteValidationStore(tmp / "bi.db")
sq3.save(bi)
probe("sqlite: big episode_count", lambda: sq3.get("bi").scorecard.episode_count)
mem3 = ValidationStore(); mem3.save(bi)
probe("memory: big episode_count", lambda: mem3.get("bi").scorecard.episode_count)
probe("memory == sqlite", lambda: mem3.get("bi") == sq3.get("bi"))

# --- float repr precision --------------------------------------------------
fr = mk("fr", sc_overrides={"composite_score": 0.1 + 0.2, "success_rate": 1 / 3})
sq4 = SqliteValidationStore(tmp / "fr.db")
sq4.save(fr)
mem4 = ValidationStore(); mem4.save(fr)
probe("sqlite: float repr", lambda: sq4.get("fr").scorecard.composite_score)
probe("memory: float repr", lambda: mem4.get("fr").scorecard.composite_score)
probe("memory == sqlite", lambda: mem4.get("fr") == sq4.get("fr"))
probe("scorecard == stored scorecard (memory)", lambda: mem4.get("fr").scorecard == fr.scorecard)
probe("scorecard == stored scorecard (sqlite)", lambda: sq4.get("fr").scorecard == fr.scorecard)

# --- -0.0 ------------------------------------------------------------------
nz = mk("nz", sc_overrides={"composite_score": -0.0})
sq5 = SqliteValidationStore(tmp / "nz.db")
sq5.save(nz)
probe("sqlite: -0.0 preserved", lambda: math.copysign(1.0, sq5.get("nz").scorecard.composite_score))

# --- taxonomy with a None value / non-int count ---------------------------
sq6 = SqliteValidationStore(tmp / "tn.db")
odd = mk("tn", sc_overrides={"failure_taxonomy": {"a": None}})
probe("sqlite: save taxonomy with None", lambda: sq6.save(odd) is not None)
probe("sqlite: taxonomy None round-trip", lambda: sq6.get("tn").scorecard.failure_taxonomy)

# --- very long block_reasons / confidence_interval -------------------------
sq7 = SqliteValidationStore(tmp / "br.db")
br = mk("br", sc_overrides={"block_reasons": tuple(f"reason-{i}-é" for i in range(2000))})
probe("sqlite: save 2000 block_reasons", lambda: sq7.save(br) is not None)
probe("sqlite: block_reasons count", lambda: len(sq7.get("br").scorecard.block_reasons))
mem7 = ValidationStore(); mem7.save(br)
probe("memory: block_reasons is tuple", lambda: isinstance(mem7.get("br").scorecard.block_reasons, tuple))
probe("sqlite:  block_reasons is tuple", lambda: isinstance(sq7.get("br").scorecard.block_reasons, tuple))

# --- confidence_interval tuple type ---------------------------------------
ci = mk("ci", sc_overrides={"confidence_interval": (0.123456789012345, 0.987654321098765)})
sq8 = SqliteValidationStore(tmp / "ci.db")
sq8.save(ci)
mem8 = ValidationStore(); mem8.save(ci)
probe("memory: ci is tuple", lambda: isinstance(mem8.get("ci").scorecard.confidence_interval, tuple))
probe("sqlite:  ci is tuple", lambda: isinstance(sq8.get("ci").scorecard.confidence_interval, tuple))
probe("memory == sqlite on ci", lambda: mem8.get("ci") == sq8.get("ci"))
probe("memory: scorecard eq", lambda: mem8.get("ci").scorecard == ci.scorecard)
probe("sqlite: scorecard eq", lambda: sq8.get("ci").scorecard == ci.scorecard)
