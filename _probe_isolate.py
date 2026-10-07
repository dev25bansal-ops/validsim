"""Isolate sim-layer blast radius: run only tests that exercise MockIsaacBackend."""
import subprocess, sys

FILES = [
    "tests/test_runner.py",
    "tests/test_isaac_worker.py",
    "tests/test_isaac_batching.py",
    "tests/test_shadow.py",
    "tests/test_sim_factory.py",
    "tests/test_sim_contract_audit_agent.py",
    "tests/test_safety.py",
    "tests/test_pipeline.py",
    "tests/test_scorecard.py",
]
out = subprocess.run(
    [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-q", *FILES],
    capture_output=True, text=True,
)
print(out.stdout[-4000:])
