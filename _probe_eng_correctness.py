"""REPRO: robustness_score and _regression_component are structural constants.

Run:  python _probe_eng_correctness.py
"""
from __future__ import annotations

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import RegressionReport, compare
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import _regression_component, _robustness_score, build_scorecard
from validsim.sim.runner import EpisodeResult

TASK = TaskConfig(
    task_id="t", robot=RobotSpec(name="r"), environment=EnvironmentSpec(name="e"), episodes=10
)


def eps(n_ok: int, n_fail: int, level: str = "full") -> list[EpisodeResult]:
    out = [
        EpisodeResult(episode_id=f"s{i}", task_id="t", seed=i, success=True, duration_s=5.0,
                      randomization_level=level)
        for i in range(n_ok)
    ]
    out += [
        EpisodeResult(episode_id=f"f{i}", task_id="t", seed=900 + i, success=False,
                      failure_mode="collision", duration_s=9.0, randomization_level=level)
        for i in range(n_fail)
    ]
    return out


def card(episodes, regression=None, threshold=85.0):
    return build_scorecard(
        run_id="vrun-cafe1234", checkpoint_id="ckpt-1", task=TASK,
        evaluation=evaluate(episodes), safety=SafetyResult(0.0, 0.0, None, 0.0, 100.0),
        episodes=episodes, regression=regression, threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
    )


print("=" * 72)
print("A. robustness_score: every realistic run is EXACTLY 100.0")
print("=" * 72)
cases = [
    ("all 1000 pass, 1 group", eps(1000, 0)),
    ("all 1000 FAIL, 1 group", eps(0, 1000)),
    ("chaotic 500/500, 1 group", eps(500, 500)),
    ("EMPTY run (0 episodes)", []),
    ("CONTROL: 2 groups, 1.0 vs 0.5", eps(10, 0, "none") + eps(5, 5, "partial")),
    ("CONTROL: 3 groups, wild spread",
     eps(100, 0, "none") + eps(50, 50, "partial") + eps(0, 100, "full")),
]
for label, e in cases:
    print(f"  robustness({label:34s}) = {_robustness_score(e)}")

print()
print("=" * 72)
print("B. A model that FAILS EVERY episode scores robustness == a model that")
print("   SUCCEEDS AT EVERY episode. The number is indistinguishable.")
print("=" * 72)
a = card(eps(0, 1000))
b = card(eps(1000, 0))
print(f"  perfect model : success={b.success_rate:.3f} robustness={b.robustness_score} composite={b.composite_score}")
print(f"  total failure : success={a.success_rate:.3f} robustness={a.robustness_score} composite={a.composite_score}")
print(f"  identical robustness? {a.robustness_score == b.robustness_score}")

print()
print("=" * 72)
print("C. _regression_component(None) == 100.0 == 'perfectly clean'")
print("=" * 72)
clean = RegressionReport(items=[])
print(f"  _regression_component(None)            = {_regression_component(None)}")
print(f"  _regression_component(clean report)    = {_regression_component(clean)}")
print(f"  indistinguishable? {_regression_component(None) == _regression_component(clean)}")

worse = compare(
    evaluate(eps(0, 1000)),          # 0.0% success
    evaluate(eps(1000, 0)),          # baseline 100%
)
print(f"  real 0% vs 100% baseline -> significant={worse.has_regressions} "
      f"worst={worse.worst_severity} component={_regression_component(worse)}")

no_base = card(eps(100, 0))
print(f"  scorecard WITHOUT baseline: regression_delta={no_base.regression_delta} "
      f"composite={no_base.composite_score} decision={no_base.deploy_decision}")
print("  -> a brand-new model with no history reads exactly like a proven one.")

print()
print("=" * 72)
print("D. Reported surface: nothing in the scorecard says 'unmeasured'")
print("=" * 72)
import dataclasses
f = dataclasses.fields(card(eps(900, 100)))
print("  fields:", [x.name for x in f])
print("  any field carrying 'measured'/'unmeasured'/'group'/'baseline' flag?",
      any(k in x.name for x in f for k in ("measured", "group", "baseline")))
