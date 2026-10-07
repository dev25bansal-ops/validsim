import json
from validsim.scenarios.generator import ADVERSARIAL_CATEGORIES, ScenarioGenerator
from validsim.scenarios.llm_generator import LLMScenarioGenerator


class FP:
    def __init__(self, r):
        self.r = r
    def complete(self, p):
        return self.r


def check(label, raw, n=13):
    g = LLMScenarioGenerator(FP(raw), fallback=ScenarioGenerator(seed=42))
    try:
        out = g.generate("t", n)
        ok = (
            len(out) == n
            and all(
                s.category in ADVERSARIAL_CATEGORIES
                and 0.0 <= s.difficulty <= 1.0
                and s.name
                and isinstance(s.params, dict)
                for s in out
            )
        )
        print(label, "n=", len(out), "valid=", ok, "fb=", g.fallback_used)
    except Exception as e:  # noqa: BLE001
        print(label, "RAISED", type(e).__name__, e)


check("nan-inf", json.dumps([
    {"category": "lighting_change", "name": "x", "params": {}, "difficulty": float("nan")},
    {"category": "sensor_degradation", "name": "y", "difficulty": float("inf")},
]))
check("empty-array", "[]")
check("empty-string", "")
check("whitespace", "   \n  ")
check("object-not-array", json.dumps({"category": "lighting_change"}))
check("prose-wrapped", "Here you go: " + json.dumps(
    [{"category": "lighting_change", "name": "glare", "params": {}, "difficulty": 0.3}]
) + " hope it helps")
check("non-taxonomy", json.dumps([{"category": "quantum_flux", "name": "bad", "difficulty": 0.5}] * 5))
check("bad-difficulty", json.dumps([
    {"category": "lighting_change", "name": "a", "difficulty": "hard"},
    {"category": "sensor_degradation", "name": "b", "difficulty": -99},
    {"category": "task_ambiguity", "name": "c", "difficulty": 999},
]))
check("type-confusion", json.dumps([
    {"category": 123, "name": "a"},
    {"category": "lighting_change", "name": None},
    {"category": "lighting_change", "name": "ok", "params": "str"},
]))
check("nested-garbage", json.dumps([[[1, 2], {}], "x", None, 42]))
check("truncated", '[{"category": "lighting_change", ')
print("n=0 empty:", LLMScenarioGenerator(FP("[]"), fallback=ScenarioGenerator()).generate("t", 0))
# rule-based determinism / coverage sanity
a = ScenarioGenerator(seed=7).generate("zz", 30)
b = ScenarioGenerator(seed=7).generate("zz", 30)
print("determinism:", [(s.id, s.params, s.difficulty) for s in a] == [(s.id, s.params, s.difficulty) for s in b])
print("coverage:", set(s.category for s in a) == set(ADVERSARIAL_CATEGORIES))
print("diff bounds:", all(0.05 <= s.difficulty <= 0.95 for s in a))
print("ids unique:", len(set(s.id for s in a)) == 30)
