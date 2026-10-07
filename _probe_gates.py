"""Measure the numeric invariants the new scenario model must not break."""
import random
from validsim.sim.runner import _checkpoint_offset, FAILURE_MODES

print("ckpt-v41 offset:", _checkpoint_offset("ckpt-v41"))
print("ckpt-v41 p at full,diff=1.0:", 0.9 - 0.20 + _checkpoint_offset("ckpt-v41") - 0.5)
print("ckpt-v41 p at full,diff=0.0:", 0.9 - 0.20 + _checkpoint_offset("ckpt-v41"))

# The audit test: easy(d=0) - hard(d=1) must be EXACTLY 0.5, so the clamp must
# not bite on either side.
off = _checkpoint_offset("ckpt-v41")
easy = 0.9 - 0.20 + off - 0.0
hard = 0.9 - 0.20 + off - 0.5
print(f"\nunclamped easy={easy:.4f} hard={hard:.4f}")
print(f"headroom before hard clamps at 0.02: stress < {hard - 0.02:.4f}")

# Shadow gate: 20 nominal + 12 adversarial (all difficulty=1.0, all categories
# but always the human_proximity params dict) vs a deaf worker at p=0.70.
def mock_rate(episodes=20, scen=12, stress=0.0, seed=42, level="full", ckpt="ckpt-v41"):
    m = random.Random(seed)
    o = _checkpoint_offset(ckpt)
    p_nom = min(0.99, max(0.02, 0.9 - {"none": 0.0, "partial": 0.10, "full": 0.20}[level] + o))
    p_adv = min(0.99, max(0.02, p_nom - 1.0 * 0.5 - stress))
    ok = sum(1 for i in range(episodes) if m.random() < p_nom)
    ok += sum(1 for i in range(scen) if m.random() < p_adv)
    return ok / (episodes + scen)

print("\nmock rate with stress=0.00 :", round(mock_rate(stress=0.0), 4))
for s in (0.02, 0.05, 0.10, 0.15, 0.20):
    print(f"mock rate with stress={s:.2f} :", round(mock_rate(stress=s), 4),
          " delta vs 0.70 =", round(0.70 - mock_rate(stress=s), 4))
