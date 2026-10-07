from __future__ import annotations
import traceback
from types import SimpleNamespace

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
import validsim.notify.dispatcher as d


def card() -> Scorecard:
    return Scorecard(
        run_id="r", checkpoint_id="c", task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=1.0, robustness_score=1.0,
        regression_delta=0.0, confidence_interval=(0.8, 0.9),
        deploy_decision="APPROVE", threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00", episode_count=10,
        failure_taxonomy={},
    )


if __name__ == "__main__":
    d.httpx.post = lambda url, **kw: SimpleNamespace(status_code=200)
    d.time.sleep = lambda *_: None
    dd = WebhookDispatcher(live=True)
    dd.register("x", "http://127.0.0.1:1/h")
    try:
        print("OK", dd.dispatch(card()))
    except Exception:
        traceback.print_exc()
