import json
import xml.etree.ElementTree as ET
from pathlib import Path

cache_path = Path(".validsim") / "scorecards.json"
out_path = Path("validsim-nightly-junit.xml")
if not cache_path.exists():
    print("no scorecard cache — the run step failed before caching; skipping JUnit wrap")
    raise SystemExit(0)

runs = json.loads(cache_path.read_text(encoding="utf-8"))
if not runs:
    print("scorecard cache is empty; skipping JUnit wrap")
    raise SystemExit(0)

# Same ordering as the CLI's --latest: newest by created_at.
sc = max(runs.values(), key=lambda entry: str(entry.get("created_at", "")))
blocked = float(sc["composite_score"]) < float(sc["threshold"])

suites = ET.Element(
    "testsuites",
    name="validsim-nightly-adversarial-sweep",
    tests="1",
    failures="1" if blocked else "0",
)
suite = ET.SubElement(
    suites,
    "testsuite",
    name="nightly-adversarial-sweep",
    tests="1",
    failures="1" if blocked else "0",
)
case = ET.SubElement(
    suite, "testcase", name=f"gate:{sc['run_id']}", classname="validsim.cli.gate"
)
if blocked:
    failure = ET.SubElement(
        case,
        "failure",
        message=(
            f"composite {sc['composite_score']} below threshold {sc['threshold']}"
        ),
    )
    failure.text = f"deploy_decision={sc['deploy_decision']}"
ET.indent(suites, space="  ")
ET.ElementTree(suites).write(out_path, encoding="utf-8", xml_declaration=True)
print(f"wrote {out_path} (run {sc['run_id']}, decision {sc['deploy_decision']})")
