"""Throwaway probe: is validsim_runs_total monotonically increasing?"""
from __future__ import annotations

import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from conftest import make_persisted_scorecard, make_stored_run  # noqa: E402

from validsim.api.metrics import Metrics, render_metrics  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402


def line(store: ValidationStore, prefix: str) -> str:
    body = render_metrics(store, Metrics())
    return next(ln for ln in body.split("\n") if ln.startswith(prefix))


store = ValidationStore()
for i in (1, 2, 3):
    store.save(make_stored_run(make_persisted_scorecard(run_id=f"vrun-{i:08x}")))

print("3 runs        ->", line(store, "validsim_runs_total "))
store.delete("vrun-00000003")
print("after 1 delete->", line(store, "validsim_runs_total "))
store.delete("vrun-00000002")
store.delete("vrun-00000001")
print("all deleted   ->", line(store, "validsim_runs_total "))
print()
print("Prometheus rule: a counter MUST only increase. A decrease breaks")
print("rate() and can make alerts fire backwards (e.g. absent()/-increase).")
