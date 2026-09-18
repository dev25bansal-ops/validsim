import json, sys

sc = json.load(open(sys.argv[1], encoding="utf-8"))
approved = sc.get("deploy_decision") == "APPROVE"
badge = "✅ APPROVE" if approved else "❌ BLOCK"
lines = [
    f"## 🤖 ValidSim Scorecard — `{sc.get('run_id', '?')}`",
    "",
    f"**Decision: {badge}** — composite **{sc.get('composite_score')}** "
    f"vs threshold **{sc.get('threshold')}**",
    "",
    "| Checkpoint | Task | Episodes | Created (UTC) |",
    "|---|---|---|---|",
    f"| `{sc.get('checkpoint_id')}` | `{sc.get('task_id')}` "
    f"| {sc.get('episode_count')} | {sc.get('created_at')} |",
    "",
    "| Metric | Value |",
    "|---|---|",
    f"| Success rate | {float(sc.get('success_rate', 0)) * 100:.1f}% |",
    f"| Safety score | {sc.get('safety_score')} |",
    f"| Robustness score | {sc.get('robustness_score')} |",
]
ci = sc.get("confidence_interval")
if ci:
    lines.append(f"| Success rate 95% CI | [{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%] |")
if sc.get("regression_delta") is not None:
    lines.append(f"| Regression delta (success rate) | {sc['regression_delta']} |")
tax = sc.get("failure_taxonomy") or {}
if tax:
    top = ", ".join(
        f"`{k}` × {v}" for k, v in sorted(tax.items(), key=lambda kv: -kv[1])[:5]
    )
    lines += ["", f"**Top failure modes:** {top}"]
lines += ["", "<sub>ValidSim self-hosted MVP (local mock pipeline) — "
                "cloud backend via API key coming.</sub>"]
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")