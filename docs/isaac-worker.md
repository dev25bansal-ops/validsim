---
tags: [engineering, simulation, gpu, http-contract]
status: contract-only (pre-DGX)
---

# 🖥️ Isaac Sim GPU Worker — HTTP Contract

The wire contract between [`validsim/sim/isaac_worker.py`](../validsim/sim/isaac_worker.py)
and the containerized Isaac Sim / Isaac Lab worker. It pins the message shape
**before** any GPU exists, so the swap of
[`MockIsaacBackend`](../validsim/sim/runner.py) →
[`IsaacWorkerBackend`](../validsim/sim/isaac_worker.py) is a config change
(`VALIDSIM_BACKEND=isaac`), not a code change.

> [!important] The worker image is not in this repository
> Nothing here serves HTTP. The worker is a separate ~15 GB image (Isaac Sim
> 4.x headless, PhysX on an A100) built when DGX Cloud credits land. Until then
> the client is verified only against `httpx.MockTransport` fakes in
> [`tests/test_isaac_worker.py`](../tests/test_isaac_worker.py) — which is why
> this file, not a live service, is the source of truth.

## 1. Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/episodes/run` | Run `episodes + len(scenarios)` episodes, return results |
| `GET` | `/health` | Liveness/readiness probe (2xx = worker accepts jobs) |

Auth: `Authorization: Bearer <token>` on every request when
`VALIDSIM_ISAAC_WORKER_KEY` (or `api_key=`) is set; omitted otherwise.
Transport: JSON only. A non-JSON body, any HTTP status `>= 400`, or a schema
deviation raises `SimWorkerError` (carrying `status_code` when there was one).
Connect/timeout errors get **two** attempts; HTTP status errors get **none** —
a `4xx/5xx` is a deterministic answer, and re-driving a GPU episode is expensive.

## 2. `POST /episodes/run`

Request (one `run_episode()` call; `episodes: 0` means "the scenario *is* the
episode", so the reply always has `episodes + len(scenarios)` results, nominal
first):

```json
{
  "task_id": "pick-place",
  "robot": { "name": "franka_panda", "urdf_path": "robots/franka.urdf", "dof": 7 },
  "environment": { "name": "kitchen", "scene_usd": "scenes/kitchen.usda" },
  "checkpoint_id": "ckpt-v41",
  "seed": 42,
  "episodes": 0,
  "randomization_level": "full",
  "scenarios": [
    {
      "id": "adv-pick-place-0000",
      "category": "human_proximity",
      "params": { "human_distance_m": 0.42, "human_speed_mps": 1.1, "crossing": true },
      "difficulty": 0.6
    }
  ]
}
```

> [!warning] `checkpoint_id` is the policy under test — do not omit it
> This field identifies the artifact being validated, and it is `null` only when
> the caller did not supply one. A worker that ignores it can only ever run a
> default policy, in which case **every scorecard it produces is a statement
> about that default rather than about the submitted checkpoint**, and the
> product cannot rank checkpoints at all. Load the policy named here, and echo
> it back on each episode so the client can confirm the right artifact ran.

Response — every `EpisodeResult` field is required on the wire (`null` for the
optional ones); unknown keys are rejected, never ignored:

```json
{
  "episodes": [
    {
      "episode_id": "pick-place-seed0000000042",
      "task_id": "pick-place",
      "seed": 42,
      "success": false,
      "collision_count": 2,
      "max_contact_force_n": 140.0,
      "min_human_distance_m": 0.31,
      "failure_mode": "collision",
      "duration_s": 9.0,
      "joint_states_summary": { "position_rms": 0.4, "velocity_rms": 0.1, "effort_max": 33.0, "dof": 7 },
      "randomization_level": "full"
    }
  ]
}
```

The client rejects, with a contract-mismatch message: a missing/unknown episode
key, a wrong type (including `true` where a number is expected), an episode
count that differs from the request, a `failure_mode` outside the ValidSim
taxonomy, a `randomization_level` outside `none|partial|full`, a level that does
not echo the request, and a `seed` that is not `seed + position`.

## 3. Episode semantics

* **Seeded determinism.** An episode is a pure function of
  `(seed, task, checkpoint_id, randomization_level, scenario)` — the same
  guarantee `MockIsaacBackend` gives, so `run_validation` needs no changes and a
  rerun of a stored seed reproduces a stored verdict. `run_validation` assigns
  `seed + i` per episode; the worker must echo those seeds back in order.
  Determinism is per `(checkpoint, seed)`: the same seed against a *different*
  checkpoint is a different episode, and must be allowed to differ.
* **Randomization levels.** `none` = nominal scene. `partial` ≈ −10 pp success,
  `full` ≈ −20 pp (the mock's penalties in `_RANDOMIZATION_PENALTY`): textures,
  lighting, object pose/mass/friction, camera noise. The level is requested once
  per run and echoed per episode.
* **Adversarial difficulty.** `difficulty ∈ [0, 1]` scales the failure
  probability (the mock subtracts `difficulty * 0.5`); `category` selects the
  injection (one of the twelve `ADVERSARIAL_CATEGORIES`) and `params` its
  concrete values. `human_proximity` runs must report
  `min_human_distance_m`; every other scene reports `null` rather than omitting
  the key. `failure_mode` must be one of `collision, timeout, grasp_failure,
  joint_limit, perception_error, unstable_placement, emergency_stop`, and must
  be `null` on success.

## 4. Deployment status (future DGX worker)

For this GPU path, the repository currently ships only the HTTP client and its
contract tests. It does **not** ship an Isaac Sim/Lab worker image, a worker
Dockerfile, or an active `worker-gpu` Compose service. The commented
`worker-gpu` block in [`docker-compose.yml`](../docker-compose.yml) is a
placeholder, not a runnable deployment. There is no runnable GPU-worker build
recipe in this repository.

A future worker implementation must serve the endpoints in §1. Configure the
client with:

* `VALIDSIM_ISAAC_WORKER_URL` — the worker's actual base URL. The client has no
  hard-coded port; the local example in [`.env.example`](../.env.example) is
  `http://localhost:8080`. Set the host and port to the deployed worker.
* `VALIDSIM_ISAAC_WORKER_KEY` — the optional bearer token. The client sends
  `Authorization: Bearer <value>` when the value is non-empty. Source the
  secret from the deployment environment or secret store; do not commit it or
  bake it into an image. Leave it empty for local development when the worker
  does not require authentication.

When a worker is supplied, the following deployment considerations still apply:

* **Sizing:** ≥10 GB VRAM per instance (A100-40/80 GB on DGX Cloud); the API
  box stays CPU-only and reaches the worker over the private network via the
  configured `VALIDSIM_ISAAC_WORKER_URL`.
* **Parallelism:** 8–64 concurrent GPU episodes. Run one worker per GPU and
  fan out with replicas / a job queue rather than packing scenes onto one
  device; the client reuses one connection pool per backend instance.
* **Batching:** the contract is batch-shaped (`episodes`, `scenarios`), so a
  future `run_validation`-equivalent can submit a whole verdict in one POST.
  Today `run_episode` uses the degenerate batch-of-one, which costs a round trip
  per episode — fine for shadow runs, worth revisiting at scale.
* **Timeouts:** `timeout` applies to connect *and* read; a single PhysX episode
  can take minutes, so raise it well above the 30 s default for real batches.

## 5. Rollout plan — shadow mode first

1. **Contract freeze.** This file + the fake tests land now; a worker PR must
   pass a conformance suite replaying the fixtures in
   [`tests/test_isaac_worker.py`](../tests/test_isaac_worker.py).
2. **Shadow runs.** Same checkpoint, same seeds, both backends:
   `MockIsaacBackend` and `IsaacWorkerBackend`, each writing its own run id.
   Nothing user-facing reads the GPU numbers yet.
3. **Compare drift.** Track success-rate delta (nominal and per adversarial
   category), failure-taxonomy distribution, and the safety observables
   (contact force, human proximity). A well-calibrated mock should stay within
   a few points; a large gap is a finding about the *mock*, not a blocker.
4. **Promote.** Only after N consecutive shadow runs agree within tolerance does
   `VALIDSIM_BACKEND=isaac` become the source of truth for published scorecards
   and `validsim gate` verdicts. The mock stays the default for CI and CPU-only
   users, so this promotion is reversible by one environment variable.

Until step 4, treat any GPU-derived scorecard as provisional and keep the
mock-derived verdict beside it.
