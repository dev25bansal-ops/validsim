---
tags: [engineering, scenarios, taxonomy, adversarial, llm]
status: prototype (W4)
---

# 🧬 ValidSim Adversarial Scenario Taxonomy

The twelve adversarial categories ValidSim scores against, the parameters each
category's sampler draws, how difficulty is computed, the deterministic seeding
model, and the optional LLM backend that invents richer scenarios while
guaranteeing category coverage. This is the design counterpart to the
[[ValidSim REST API — Reference]] (`task.adversarial_count`) and the episode
payload in [[Isaac Sim GPU Worker — HTTP Contract]] (`scenarios[]`).

Source of truth:

* [`validsim/scenarios/generator.py`](../validsim/scenarios/generator.py) — the
  rule-based `ScenarioGenerator`, `ADVERSARIAL_CATEGORIES`, and `_PARAM_SAMPLERS`.
* [`validsim/scenarios/llm_generator.py`](../validsim/scenarios/llm_generator.py) —
  the `LLMScenarioGenerator` backend and the `create_scenario_generator` factory.

> [!important] MVP scope
> The rule-based generator is the default and performs **no network I/O** — it
> is fully reproducible from a seed. The LLM path is opt-in (it activates only
> when `VALIDSIM_LLM_API_KEY` is set) and always degrades back to the rule-based
> generator, so a missing or misbehaving endpoint can never take a validation
> run down with it.

---

## 1. The twelve categories

`ADVERSARIAL_CATEGORIES` is a fixed tuple; every generated scenario's `category`
must be one of these exact strings. The scorecard expects all twelve to be
exercised, which is why the generator cycles through them in order.

| # | Category | What it stresses | Base difficulty |
|---|---|---|---|
| 0 | `lighting_change` | Vision under shifting brightness / colour temp / flicker | `0.25` |
| 1 | `object_property_change` | Grasp dynamics when mass, friction, restitution vary | `0.40` |
| 2 | `human_proximity` | Safe coexistence with a nearby, moving person | `0.60` |
| 3 | `unexpected_obstacle` | Reactive avoidance of an object that appears mid-episode | `0.55` |
| 4 | `sensor_degradation` | Robustness to camera dropout, depth noise, added latency | `0.50` |
| 5 | `mechanical_variation` | Control under joint friction, backlash, and mass drift | `0.45` |
| 6 | `environmental_disturbance` | Disturbance forces from wind and table acceleration | `0.35` |
| 7 | `task_ambiguity` | Goal reasoning under distractors and noisy/ambiguous goals | `0.50` |
| 8 | `multi_robot_interference` | Shared-workspace coordination with neighbour robots | `0.65` |
| 9 | `emergency_scenario` | Behaviour on e-stop, fire alarm, or a person going down | `0.75` |
| 10 | `adversarial_input` | Resilience to perturbed / injected perception or language | `0.70` |
| 11 | `temporal_pressure` | Meeting deadlines when the world runs fast | `0.45` |

Base difficulty is the *centre* of each category's distribution before seeded
jitter and bias are applied (see [§4](#4-how-difficulty-is-computed)).

---

## 2. The `AdversarialScenario` shape

Each scenario is a frozen dataclass — the unit handed to the simulator:

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Deterministic identifier (see [§5](#5-deterministic-seeding-model)). |
| `category` | `str` | One of the twelve exact strings. |
| `name` | `str` | Human-readable label. |
| `params` | `dict[str, Any]` | Category-specific randomized parameters ([§3](#3-category-parameters)). |
| `difficulty` | `float` | Normalized `0.0`–`1.0`, higher = harder. |

`__post_init__` enforces the contract at construction time: a `difficulty`
outside `[0, 1]` or a `category` not in `ADVERSARIAL_CATEGORIES` raises
`ValueError`. This is the same guard the LLM path relies on when it re-hydrates
model output into scenarios.

---

## 3. Category parameters

Each category has a dedicated sampler in `_PARAM_SAMPLERS` that draws its
parameters from the shared RNG stream. Ranges below are the **inclusive** bounds
the sampler draws from; the *round* column is the decimal precision the value is
rounded to before storage. All samplers take `(rng, i)` but **ignore `i`** — the
index only affects which category is chosen, never the parameter values.

### `lighting_change`

| Param | Type | Range | Round |
|---|---|---|---|
| `lux` | int | `[5, 3000]` | — |
| `color_temperature_k` | int | `[2500, 7500]` | — |
| `flicker_hz` | float | `[0.0, 60.0]` | 1 dp |

### `object_property_change`

| Param | Type | Range | Round |
|---|---|---|---|
| `mass_kg` | float | `[0.02, 5.0]` | 3 dp |
| `friction_coefficient` | float | `[0.05, 1.5]` | 3 dp |
| `restitution` | float | `[0.0, 0.95]` | 3 dp |

### `human_proximity`

| Param | Type | Range | Round |
|---|---|---|---|
| `human_distance_m` | float | `[0.1, 1.2]` | 3 dp |
| `human_speed_mps` | float | `[0.3, 1.8]` | 3 dp |
| `crossing` | bool | `True` with p = 0.5 | — |

### `unexpected_obstacle`

| Param | Type | Range | Round |
|---|---|---|---|
| `spawn_offset` | coord | `x, y ∈ [-0.5, 0.5]`, `z ∈ [0.0, 1.0]` | 3 dp |
| `obstacle_radius_m` | float | `[0.02, 0.3]` | 3 dp |
| `appears_at_step` | int | `[1, 200]` | — |

`spawn_offset` is a nested `{"x": …, "y": …, "z": …}` dict sampled within a 1 m³
workspace (`_rcoord`).

### `sensor_degradation`

| Param | Type | Range | Round |
|---|---|---|---|
| `camera_dropout_rate` | float | `[0.0, 0.6]` | 3 dp |
| `depth_noise_mm` | float | `[0.0, 80.0]` | 1 dp |
| `latency_ms` | int | `[0, 250]` | — |

### `mechanical_variation`

| Param | Type | Range | Round |
|---|---|---|---|
| `joint_friction_scale` | float | `[0.5, 2.0]` | 3 dp |
| `backlash_rad` | float | `[0.0, 0.05]` | 4 dp |
| `mass_scale` | float | `[0.8, 1.3]` | 3 dp |

### `environmental_disturbance`

| Param | Type | Range | Round |
|---|---|---|---|
| `wind_mps` | float | `[0.0, 6.0]` | 2 dp |
| `table_acceleration_mps2` | float | `[0.0, 3.0]` | 3 dp |

### `task_ambiguity`

| Param | Type | Range | Round |
|---|---|---|---|
| `distractor_objects` | int | `[1, 6]` | — |
| `goal_description_noise` | float | `[0.0, 0.5]` | 3 dp |
| `target_swapped` | bool | `True` with p = 0.3 | — |

### `multi_robot_interference`

| Param | Type | Range | Round |
|---|---|---|---|
| `neighbor_robot_count` | int | `[1, 3]` | — |
| `neighbor_speed_mps` | float | `[0.2, 1.5]` | 3 dp |
| `shared_workspace` | bool | constant `True` | — |

### `emergency_scenario`

| Param | Type | Range | Round |
|---|---|---|---|
| `e_stop_at_step` | int | `[10, 300]` | — |
| `fire_alarm` | bool | `True` with p = 0.4 | — |
| `human_down` | bool | `True` with p = 0.4 | — |

### `adversarial_input`

| Param | Type | Range | Round |
|---|---|---|---|
| `perturbation_eps` | float | `[0.001, 0.08]` | 4 dp |
| `attack_surface` | str | one of `vision`, `state`, `language` | — |
| `injection_rate` | float | `[0.05, 0.5]` | 3 dp |

### `temporal_pressure`

| Param | Type | Range | Round |
|---|---|---|---|
| `deadline_scale` | float | `[0.3, 0.9]` | 3 dp |
| `stream_speedup` | float | `[1.1, 3.0]` | 2 dp |

---

## 4. How difficulty is computed

For each scenario the rule-based generator samples one difficulty value from the
category's base, plus a seeded jitter, plus an optional bias, then clamps:

```text
difficulty = clamp(
    base[category]
    + rng.uniform(-0.15, +0.15)          # seeded jitter, always in play
    + difficulty_bias * 0.5,             # bias term (0.0 by default)
    0.05, 0.95,                          # clamp bounds
)
difficulty = round(difficulty, 4)
```

* **Base** (`_BASE_DIFFICULTY`) — the category centre, from `0.25`
  (`lighting_change`) up to `0.75` (`emergency_scenario`).
* **Jitter** — `±0.15` drawn from the seeded RNG so repeated categories vary.
* **Bias** (`_DIFFICULTY_BIAS_SCALE = 0.5`) — `difficulty_bias` lives in
  `[-1, 1]`; a full `+1.0` hardens the whole batch by `0.5`, a full `-1.0` eases
  it by `0.5`. The constructor rejects a bias outside `[-1, 1]`.
* **Clamp** — the final value is constrained to `[0.05, 0.95]`, so no scenario is
  ever trivially easy or impossibly hard.

> [!note] Bias never perturbs the RNG stream
> The bias is added *after* the seeded jitter and consumes **no** RNG draws. So
> ids, categories, and parameters are byte-for-byte identical across bias
> settings, and `difficulty_bias = 0.0` reproduces the unbiased distribution
> exactly. Only the `difficulty` field moves.

Worked bias effect on the first `pick-place` scenario (`lighting_change`,
base `0.25`, jitter `+0.0822`):

| `difficulty_bias` | Raw sum | After clamp `[0.05, 0.95]` |
|---|---|---|
| `-1.0` | `0.25 + 0.0822 − 0.50 = −0.1678` | `0.05` (clamped) |
| `0.0` | `0.3322` | `0.3322` |
| `+1.0` | `0.25 + 0.0822 + 0.50 = 0.8322` | `0.8322` |

---

## 5. Deterministic seeding model

All randomness flows from a single `random.Random` seeded per task:

```python
rng = random.Random(f"{self._seed}:{task_id}")
```

* The seed string is **`seed:task_id`** (e.g. `"42:pick-place"`). The base seed
  defaults to `42`; `task_id` is any string.
* One RNG stream serves the whole batch: for each scenario the difficulty jitter
  is drawn **first**, then that category's parameter sampler draws from the same
  stream. Because draws are sequential, a scenario's jitter depends on how many
  draws earlier scenarios consumed — it is not a simple per-index function.
* Consequence: `generate(task_id, n)` is **reproducible** — same seed, same
  `task_id`, same `n` ⇒ identical scenarios, every call, every process. Change
  `task_id` or the seed and the whole batch changes.

Scenarios cycle through the categories in order via `i % 12`, so `id` and
`name` are index-derived:

```text
id   = f"adv-{task_id}-{i:04d}"                 # e.g. adv-pick-place-0007
name = f"{category.replace('_',' ').title()} #{i:04d}"   # "Task Ambiguity #0007"
```

---

## 6. The `coverage()` helper

`coverage(task_id, n)` returns per-category counts for exactly the batch
`generate(task_id, n)` would produce:

```python
ScenarioGenerator(seed=42).coverage("pick-place", 15)
# {
#   "lighting_change": 2, "object_property_change": 2, "human_proximity": 2,
#   "unexpected_obstacle": 1, "sensor_degradation": 1, "mechanical_variation": 1,
#   "environmental_disturbance": 1, "task_ambiguity": 1,
#   "multi_robot_interference": 1, "emergency_scenario": 1,
#   "adversarial_input": 1, "temporal_pressure": 1
# }
```

Guarantees:

* Every one of the twelve categories is **always a key**, with `0` where absent.
* The values **sum to `n`**.
* Because scenarios cycle deterministically, **any `n ≥ 12` yields full
  coverage** (every count `≥ 1`). For `n < 12` only the first `n` categories in
  tuple order appear.
* Raises `ValueError` for negative `n` (propagated from `generate`).

---

## 7. The LLM path — `LLMScenarioGenerator`

> [!warning] Implemented, tested, and not used by any run
> Everything in this section describes a real, tested class — and a path no
> shipped execution takes. `run_and_score` hardcodes the rule-based generator
> (§7.5), so **adversarial scenarios in production are deterministic**, which is
> also why they are reproducible from `(checkpoint_id, task_id)`. Read this
> section as the contract for the opt-in surface, not as a description of
> current behaviour.

`LLMScenarioGenerator` is a **drop-in replacement** for the rule-based
generator: `generate(task_id, n)` accepts the same arguments and returns the
same `list[AdversarialScenario]`. It asks a provider to invent `n` scenarios,
then repairs the result against the taxonomy.

### 7.1 Providers & prompt

* `ScenarioProvider` is a one-method `Protocol` — `complete(prompt) -> str`.
* `OpenAICompatibleProvider` speaks the OpenAI `/chat/completions` dialect, so it
  works against OpenAI, Azure OpenAI, vLLM, Ollama's shim, NVIDIA NIM, … by
  pointing `VALIDSIM_LLM_BASE_URL` at the deployment root. It sends
  `temperature: 0.8` (creative variation is the point; discipline is enforced by
  validation, not the sampler).
* `build_scenario_prompt(task_id, n)` renders a **versioned** prompt
  (`_PROMPT_VERSION = "2025-w24-v1"`). It demands a strict JSON array of exactly
  `n` objects with keys `category` / `name` / `params` / `difficulty`, restricts
  `category` to the twelve exact strings, and embeds two few-shot examples.
  Changing the prompt is a schema-visible change and should bump the version.

### 7.2 Schema validation (untrusted output)

Model output is treated as **untrusted data** — never executed, never
interpolated into shell, never reaching the simulator unvalidated. Each parsed
element is filtered by `_validate_item`:

| Condition | Result |
|---|---|
| Element is not a JSON object | **dropped** |
| `category` missing / not a string / not in taxonomy | **dropped** |
| `name` missing / not a string / blank | **dropped** |
| `params` not a dict | defaults to `{}` |
| `difficulty` not float-coercible / non-finite | defaults to `0.5` |
| `difficulty` numeric | **clamped to `[0, 1]`**, rounded to 4 dp |

> [!warning] Two different clamps
> The LLM validation path clamps difficulty to `[0, 1]` (so a model's `1.7`
> becomes `1.0`), whereas the rule-based generator clamps to the tighter
> `[0.05, 0.95]`. Survivors are re-`id`ed `llm-<i>`; the batch is truncated to
> the first `n`.

### 7.3 Category-coverage top-up guarantee

After validation, `generate` guarantees a batch of exactly `n` scenarios while
maximising category coverage. Let `llm_count` be accepted scenarios and
`room = n − llm_count`:

1. Compute `missing` = canonical categories absent from the accepted LLM set
   (in tuple order).
2. **Prioritise diversity:** top up one fallback scenario per missing category,
   taking at most `missing[:room]` — i.e. as many distinct missing categories as
   the remaining room allows.
3. **Then fill count:** if room remains after the category top-ups, fill it with
   a plain `fallback.generate(task_id, needed)` batch.

Top-ups count toward `n`, so the batch-size contract is preserved. Full 12/12
coverage is therefore *best-effort within `n`*: if the LLM covers few categories
and `room` is smaller than the missing count, some categories stay uncovered
(see the worked example in [§8.2](#82-llm-top-up-worked-example)). The
deterministic fallback used for top-ups draws one 12-scenario cycling batch
(whose layout guarantees each category exactly once) and serves each missing
category from it, so repeated draws stay identical. Fallback scenarios are
re-`id`ed contiguously as `adv-<task_id>-<i>` so ids stay unique within the
merged batch.

### 7.4 Graceful degradation & diagnostics

The provider is attempted `1 + max_retries` times. If **every** attempt fails
(transport or parse error) nothing is accepted, and the **entire** batch comes
from the fallback — the simulator is never starved. State exposed after each
call:

| Attribute | Meaning |
|---|---|
| `scenarios_generated` | Cumulative scenarios across all calls. |
| `last_run_used_fallback` | Whether the last call touched the fallback (top-up or wholesale). |
| `last_fallback_reason` | Human-readable reason, or `None` when clean. |
| `last_run_diagnostics` | `{missing_categories, topup_from_fallback}` for the last call. |

`last_run_diagnostics.missing_categories` is the count of the twelve canonical
categories absent from the accepted LLM scenarios; `topup_from_fallback` is how
many fallback scenarios were used.

### 7.5 Factory & environment

> [!warning] This factory has no production caller
> The only production code that builds scenarios is
> `validsim.engine.pipeline.run_and_score`, which hardcodes
> `ScenarioGenerator(seed=seed).generate(...)` at
> `validsim/engine/pipeline.py:131`. **No shipped run consults the environment
> variables below** — the API, the CLI and the job worker all generate
> deterministically. `create_scenario_generator()` is a working, tested opt-in
> surface kept for a future change, not a live feature. Use
> `current_scenario_backend()` to see what the environment *would* select, and
> read this table as the contract the factory implements rather than as current
> runtime behaviour.

`create_scenario_generator(seed=42)` picks the backend with no code change:

| Condition | Returned generator |
|---|---|
| `VALIDSIM_LLM_ENABLED` explicitly truthy **and** `VALIDSIM_LLM_API_KEY` non-empty | `LLMScenarioGenerator(OpenAICompatibleProvider(), fallback=ScenarioGenerator(seed))` |
| opt-in on but key missing, or opt-in off/absent (default) | `ScenarioGenerator(seed)` — rule-based, no network I/O |

The two-argument opt-in is deliberate: a bare API key no longer silently
changes the behaviour of a validation run. An explicit switch is required.

| Env var | Purpose | Default |
|---|---|---|
| `VALIDSIM_LLM_ENABLED` | The opt-in switch. Only an explicit truthy value (`1`/`true`/`yes`/`on`) enables the LLM path. | unset → rule-based |
| `VALIDSIM_LLM_API_KEY` | Bearer token; required *and* sufficient once the opt-in is on (base URL and model fall back to OpenAI-compatible defaults). | unset → rule-based |
| `VALIDSIM_LLM_BASE_URL` | API root for the OpenAI-compatible endpoint. | `https://api.openai.com/v1` |
| `VALIDSIM_LLM_MODEL` | Chat model name. | `gpt-4o-mini` |

`OpenAICompatibleProvider` raises `ScenarioProviderError` immediately if no API
key is available.

---

## 8. Worked example batches

### 8.1 Rule-based batch (`seed=42`, `task_id="pick-place"`, `n=12`)

`ScenarioGenerator(seed=42).generate("pick-place", 12)` — full coverage, one
scenario per category. `jitter` is the implied `difficulty − base` (bias `0.0`):

| `id` | `category` | `difficulty` | base | jitter | `params` |
|---|---|---|---|---|---|
| `adv-pick-place-0000` | `lighting_change` | `0.3322` | 0.25 | +0.0822 | `lux=1367, color_temperature_k=5468, flicker_hz=43.0` |
| `adv-pick-place-0001` | `object_property_change` | `0.4811` | 0.40 | +0.0811 | `mass_kg=1.294, friction_coefficient=0.972, restitution=0.7` |
| `adv-pick-place-0002` | `human_proximity` | `0.6606` | 0.60 | +0.0606 | `human_distance_m=1.056, human_speed_mps=1.153, crossing=false` |
| `adv-pick-place-0003` | `unexpected_obstacle` | `0.5990` | 0.55 | +0.0490 | `spawn_offset={x:-0.291,y:0.179,z:0.769}, obstacle_radius_m=0.16, appears_at_step=172` |
| `adv-pick-place-0004` | `sensor_degradation` | `0.4705` | 0.50 | −0.0295 | `camera_dropout_rate=0.445, depth_noise_mm=39.5, latency_ms=229` |
| `adv-pick-place-0005` | `mechanical_variation` | `0.3094` | 0.45 | −0.1406 | `joint_friction_scale=0.606, backlash_rad=0.0331, mass_scale=1.154` |
| `adv-pick-place-0006` | `environmental_disturbance` | `0.4962` | 0.35 | +0.1462 | `wind_mps=2.99, table_acceleration_mps2=0.105` |
| `adv-pick-place-0007` | `task_ambiguity` | `0.3605` | 0.50 | −0.1395 | `distractor_objects=5, goal_description_noise=0.07, target_swapped=false` |
| `adv-pick-place-0008` | `multi_robot_interference` | `0.6610` | 0.65 | +0.0110 | `neighbor_robot_count=3, neighbor_speed_mps=1.417, shared_workspace=true` |
| `adv-pick-place-0009` | `emergency_scenario` | `0.7098` | 0.75 | −0.0402 | `e_stop_at_step=22, fire_alarm=false, human_down=true` |
| `adv-pick-place-0010` | `adversarial_input` | `0.6759` | 0.70 | −0.0241 | `perturbation_eps=0.0532, attack_surface="language", injection_rate=0.453` |
| `adv-pick-place-0011` | `temporal_pressure` | `0.5025` | 0.45 | +0.0525 | `deadline_scale=0.759, stream_speedup=2.41` |

Every jitter lands inside `±0.15`, and `coverage("pick-place", 12)` for this
batch is `{each category: 1}`. Re-running with the same seed and `task_id`
reproduces the table exactly.

### 8.2 LLM top-up worked example

A stub provider returns **5** elements: two `lighting_change`, two
`emergency_scenario`, and one with `category="NOT_A_CATEGORY"`. One element
carries `difficulty: 1.7`. Requesting `n = 12`:

* **Validation:** the bogus-category element is dropped → `4` accepted
  (`llm-0…llm-3`); the `1.7` difficulty is clamped to `1.0`.
* **Missing categories:** LLM covered only `lighting_change` and
  `emergency_scenario` → `missing = 10`.
* **Room:** `12 − 4 = 8`. Top-up serves the first 8 missing categories (canonical
  order): `object_property_change, human_proximity, unexpected_obstacle,
  sensor_degradation, mechanical_variation, environmental_disturbance,
  task_ambiguity, multi_robot_interference`.
* **Fill:** `needed = 12 − 4 − 8 = 0`, so no plain count-fill.

Result — **12 scenarios**, ids:

```text
['llm-0','llm-1','llm-2','llm-3',
 'adv-pick-place-0000','adv-pick-place-0001','adv-pick-place-0002','adv-pick-place-0003',
 'adv-pick-place-0004','adv-pick-place-0005','adv-pick-place-0006','adv-pick-place-0007']
```

Diagnostics after the call:

```json
{
  "last_run_used_fallback": true,
  "last_fallback_reason": "LLM output missed 10 of 12 categories; topped up 8 from the rule-based fallback",
  "last_run_diagnostics": { "missing_categories": 10, "topup_from_fallback": 8 }
}
```

Note the honest limit of the guarantee: with `room = 8 < missing = 10`, the final
batch covers **10 of 12** categories — `adversarial_input` and
`temporal_pressure` remain uncovered. Diversity is maximised within the fixed
batch size, not forced beyond it.

---

Links: [[ValidSim REST API — Reference]] · [[Isaac Sim GPU Worker — HTTP Contract]]
· [Operations Runbook](runbook.md)
