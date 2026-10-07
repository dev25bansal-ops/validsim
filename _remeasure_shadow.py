"""Re-measure the TestShadowGatePower table against the current mock.

Imports the audit module's own helpers so the methodology is identical to the
tests' -- only the mock changed.

Run: cmd /c "cd /d d:\\SIM-TO-REAL && python _remeasure_shadow.py > _shadow_out.txt 2>&1"
"""
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from test_sim_contract_audit_agent import (  # noqa: E402
    ADVERSARIAL_CATEGORIES,
    _gate_pass_rate,
    _scenario,
)

rows = [
    ("deaf p=0.70, no scenarios (ckpt-41)", 0.70, 2000, {}),
    ("deaf p=0.76, no scenarios (ckpt-41)", 0.76, 200, {}),
    ("deaf p=0.90, no scenarios (ckpt-41)", 0.90, 200, {}),
    ("deaf p=0.64, no scenarios (ckpt-41)", 0.64, 200, {}),
    ("deaf p=0.58, no scenarios (ckpt-41)", 0.58, 200, {}),
    ("deaf p=0.70 at ckpt-40", 0.70, 400, {"checkpoint_id": "ckpt-40"}),
    ("deaf p=0.70 at ckpt-43", 0.70, 200, {"checkpoint_id": "ckpt-43"}),
    ("deaf p=1.00 (always success)", 1.00, 200, {}),
    ("deaf p=0.30 (biased)", 0.30, 200, {}),
]
print("=" * 74)
print("no-scenario rows (20 nominal episodes)")
print("=" * 74)
for label, p, trials, kw in rows:
    print(f"  {label:38} rate={_gate_pass_rate(p, trials=trials, **kw):.4f}  (n={trials})")

print()
print("=" * 74)
print("adversarial row: 20 nominal + 12 scenarios at difficulty=1.0")
print("=" * 74)
scenarios = [
    _scenario(sid=f"adv-{i:04d}",
              category=ADVERSARIAL_CATEGORIES[i % len(ADVERSARIAL_CATEGORIES)],
              difficulty=1.0)
    for i in range(12)
]
rate = _gate_pass_rate(0.70, trials=400, scenarios=scenarios)
print(f"  deaf p=0.70, ignores 12 adversarial   rate={rate:.4f}  (n=400)")

print()
print("=" * 74)
print("consecutive-run product (n=600 at ckpt-41)")
print("=" * 74)
per_run = _gate_pass_rate(0.70, trials=600)
print(f"  per_run={per_run:.4f}  ^3={per_run ** 3:.4f}  ^5={per_run ** 5:.4f}")

print()
print("=" * 74)
print("checkpoint-invariance pair")
print("=" * 74)
rates = [_gate_pass_rate(0.70, trials=400, checkpoint_id=c) for c in ("ckpt-40", "ckpt-43")]
print(f"  ckpt-40={rates[0]:.4f}  ckpt-43={rates[1]:.4f}  "
      f"spread={abs(rates[0] - rates[1]):.4f}  min={min(rates):.4f}")
