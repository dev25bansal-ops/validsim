# ValidSim — Platform & Infrastructure Module Proposal

**Author:** `c5-platform` · **Team:** `audit-issues` · **Date:** 2026-09-25
**Status:** proposal. Nothing here is implemented. Every "today" claim is file:line verifiable.

---

## 0. Executive summary

ValidSim is a **high-quality single-host application**, not yet a deployable platform. The gap is
not code quality — it is that every scale-out, recovery and audit story an enterprise buyer asks
about is an unstarted project.

| # | Theme | Current state (verified) | Blocks enterprise? |
|---|---|---|---|
| 1 | Kubernetes manifests | **None.** `docs/runbook.md:345`: "There is no Kubernetes manifest, Terraform state, deployment controller, or fleet rollback command in this repository." | **YES — hard** |
| 2 | GPU orchestration | Commented-out compose placeholder only (`docker-compose.yml:167-192`) | **YES — hard** |
| 3 | Migration tooling | **No Alembic, no `migrations/`, no schema-version table.** App emits DDL at runtime | **YES — hard** |
| 4 | IaC | None (`vault/04 - Engineering/Tech Stack.md:46` lists Terraform as target) | YES |
| 5 | Backup automation | Procedure documented, **zero scheduler** (`docs/runbook.md:350-356`) | **YES — hard** |
| 6 | Secrets management | Env vars only; known gap (`SECURITY.md:305`) | YES |
| 7 | Air-gapped / on-prem | No bundle, no no-telemetry mode | YES (robotics/regulated) |
| 8 | Multi-region / HA | One connection per process; process-local rate limiter | YES (Tier-4) |
| 9 | Observability stack | Prometheus text endpoint ships; Grafana/Loki do not | MEDIUM |
| 10 | Compose profiles | **No profiles exist today** | no (cheap enabler) |
| 11 | Release engineering | Builds, never publishes — `release.yml:153` admits it | **YES — adoption** |
| 12 | Cost controls | No quotas, no caps, no measured COGS | YES (business model) |

**Five hard blockers: 1, 2, 3, 5, 11.**

### 0.1 The most important point in this document

The product promise is an APPROVE/BLOCK verdict that gates physical robot behaviour
(`SECURITY.md:8-11`). A verdict is only as trustworthy as the system of record behind it. That makes
**migrations (3) and backups (5) correctness-and-safety problems, not ops niceties.** An operator
who upgrades into a silently-mutated schema, or discovers mid-incident that the last backup is six
weeks old, does not have a degraded product — they have a *safety* product that cannot be defended
in an insurer/regulator review.

### 0.2 Build order (do not reorder)

```
3. Migrations ──┬─► 1. K8s manifests   (safe schema change at scale)
                └─► 8. HA              (expand/contract, not big-bang DDL)
5. Backups ───────► 8. HA              (cannot fail over to an unrestorable replica)
6. Secrets ───────► 7. Air-gap         (bundle must carry encrypted, rotatable material)
11. Release ──────► 1, 4, 7            (no immutable version = nothing to deploy)
12. Cost controls ─► vault Unit Economics (assumption → measured data)
```

---

## 1. Verified findings that constrain every recommendation

Load-bearing defects/limits in current code, with evidence.

### F1 — The app runs DDL at runtime, from every process, on first query

`validsim/store/postgres.py:370-395` — `_ensure_ready()` executes `CREATE TABLE/INDEX IF NOT
EXISTS` plus a loop of `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (columns from the hardcoded
`_MIGRATION_COLUMNS`, `postgres.py:99-105`) on the **first query**, in both api and worker. SQLite
has the same shape (`store/sqlite.py:36-55, 190`).

Under 2+ replicas: concurrent DDL at startup; the runtime DB role permanently needs
`CREATE`/`ALTER`, so a compromised pod can mutate its own schema; and **nothing records which
schema version is deployed**. This is why item 3 comes first — you cannot do expand/contract with
runtime DDL.

### F2 — Postgres is missing the composite index SQLite already has

`list_for_checkpoint` sorts every result (`store/postgres.py:454-462`:
`WHERE checkpoint_id = %s ORDER BY created_at ASC`), but Postgres creates only two *single-column*
indexes (`postgres.py:91-92`). SQLite already has the correct composite `(checkpoint_id,
created_at)` (`sqlite.py:51-52`). Postgres therefore fetch-then-sorts per checkpoint — a backend
parity bug. Fix in migration 0003:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_validations_checkpoint_created
    ON validations (checkpoint_id, created_at);
```

### F3 — Every Prometheus scrape loads the entire run history, including episode blobs

`api/metrics.py:104-121` — `_summary()` calls `list(store.history())`. `history()`
(`store/postgres.py:464-491`) then runs `SELECT {_DETAIL_SELECT} ... ORDER BY created_at ASC`,
unfiltered and unlimited, where `_DETAIL_SELECT` includes `episodes_json`
(`postgres.py:110-118`).

So a 15-second scrape deserialises **every stored run, ever, with all episode payloads**. At the
vault's 1,000–100,000 episodes/run target (`vault/04 - Engineering/Solution Architecture.md:121`)
this will OOM the API pod — and it is why the HPA resource requests below are provisional.

**Do NOT use `max(created_at)` to find the latest run** (correction from `c3-data`, verified).
Timestamps are **second-precision** — `engine/scorecard.py:43-45` uses
`isoformat(timespec="seconds")` — so `max()` is ambiguous across ties, while `metrics.py:115` today
takes `runs[-1]` of an oldest-first list, breaking ties by *insertion order* rather than any stored
sequence. Two different code paths would then disagree about the latest composite score. Use the
same deterministic tiebreak the store's own `ORDER BY` uses:

```sql
SELECT composite_score FROM validations
 ORDER BY created_at DESC, run_id DESC LIMIT 1
```

`composite_score` is already a promoted column (`postgres.py:85`), so this needs no blob decode.
Keeping the gauge's definition identical matters: an existing alert on
`validsim_composite_score` would otherwise fire spuriously on the changeover. This is another
argument for the ULID/UUIDv7 run ids in F4 — a time-sortable id makes `ORDER BY run_id DESC` a
chronologically valid tiebreak.

### F4 — 32-bit run ids + `ON CONFLICT DO NOTHING` can silently discard a verdict

`new_run_id()` is `uuid4().hex[:8]` (`store/memory.py:76-79`, duplicated `jobs/queue.py:463-466`)
= **32 bits**; birthday collision hits ~50% at ≈77k ids (~39% at 50k). `save()` is
`ON CONFLICT (run_id) DO NOTHING` (`postgres.py:407-415`), so a collision **silently drops a
validation record** — no error, no log, no row. A dropped record means `gate` reports "no stored
run" (exit 2) or resolves to a *different* run's verdict. This is a **correctness bug, not a
capacity concern**, for a product whose entire pitch is a trustworthy verdict.

**This is a breaking change to the id format — "widen to 16 hex" is not a one-line fix** (corrected
after review by `c3-data`):
- `_RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")` (`cli.py:69`) is applied with `.fullmatch()` at
  `cli.py:207` — it pins **exactly** 8 hex chars, so a 16-char id is rejected by `validsim gate`.
- `_JOB_ID_RE = re.compile(r"^vrun-[0-9a-f]{8}$")` (`jobs/router.py:35`) does the same for the
  jobs API path parameter — and `jobs/router.py:29-34` explains *why* it is strict: the Redis
  backend interpolates the id straight into a key (`validsim:jobs:<job_id>`), so the pattern is a
  security control (audited finding L1), not just a format check.
- Every run id already in a customer database is 8 chars.

So the change needs: a deliberate decision to widen the regexes to `vrun-[0-9a-f]{8,32}` (keeping
the `jobs/router.py` key-injection guarantee intact), **and** a migration story for historical rows.

**Preferred fix: ULID / UUIDv7.** Time-sortable, which means the id's lexicographic order *is*
chronological — so `ORDER BY run_id DESC` becomes a valid "latest" tiebreak, which also fixes the
`created_at` ambiguity in F3. It is self-describing in a support ticket. Consolidate the duplicated
`new_run_id()` into one function in the same change or the two implementations will drift again.

### F5 — Rate limiter is process-local and IP-keyed (two failure modes under replicas)

`_SlidingWindowRateLimiter` is "intentionally process-local" (`api/main.py:147-148`) and keyed on
**client IP only** (`main.py:265-277`, deliberately — rotating `X-API-Key` used to mint fresh
buckets). Behind a load balancer:
- **N replicas ⇒ effective limit is `limit × N`.**
- **Behind a proxy ⇒ every client shares the ingress IP** unless `uvicorn --proxy-headers` +
  `--forwarded-allow-ips` are set. One noisy tenant then exhausts the shared bucket and 429s
  everyone.

Fix: Redis-backed limiter (`redis` is already a dependency, `requirements.txt:7`) keyed on a
**trusted** forwarded IP — never a caller-supplied header — with fail-open + a metric on Redis error.

### F6 — One connection per process, serialised under a lock

`PostgresValidationStore` uses "a single autocommit connection guarded by a `threading.Lock`"
(`store/postgres.py:286-297`), and every write takes it (`postgres.py:430-431`). Replicas scale
horizontally, but each process is fully serialised with one connection. `psycopg_pool` is required
before the HPA means anything.

### F7 — `config_loader` cannot read a nested Kubernetes ConfigMap

`config_loader.py:105-116` raises `ConfigFileError` on any indented line ("nested mappings are not
supported... flat 'key: value' entries only"), and PyYAML is **dev-only**
(`pyproject.toml:29-35`). **Design consequence: the ConfigMap in item 1 must be flat `key: value`
consumed via `envFrom`, never nested YAML.**

### F8 — `health` is a *config* probe, not a *dependency* probe

`api/main.py:592-602` — health is deliberately I/O-free, reading only `app.state`. Good design (it
is public/unauthenticated, so it must not leak or block), but it means a pod whose Postgres has
vanished still reports `ok`. So the K8s `readinessProbe` **cannot** use it; item 1 adds a separate
`livez`/`readyz` pair. The Dockerfile `HEALTHCHECK` (lines 64-65) is fine as *liveness* for the
same reason.

### F9 — Unpinned dependencies block reproducibility, SBOM and offline bundles

`requirements.txt` is 7 lines of `>=` floors with no hashes; `pyproject.toml:18-27` mirrors them.
No lockfile ⇒ two builds of one commit can differ, making SBOM, provenance and reproducible
offline bundles (item 7) unachievable. Preserve `.dockerignore:12-13` (excludes `vault/`, `.env`).

### F10 — Three env vars are read in code but undocumented

`VALIDSIM_JOB_QUEUE_MAX_DEPTH`, `VALIDSIM_JOB_LEASE_SECONDS` (`jobs/queue.py:57,59`) and
`VALIDSIM_LOG_LEVEL` (`logging.py:37`) never appear in `.env.example`. Operators cannot discover
or tune them. Trivial; fold into item 12.

### F11 — `validsim_build_info` double-counts across a rolling deploy

`api/metrics.py:178-183` emits `validsim_build_info{version="…"} 1`. With two versions live during
a rollout, any `sum by (version)` is wrong. Item 9's dashboard must use `max by (version)`.

---

## 2. Item-by-item proposal

Effort in focused dev-hours ±50%, consistent with `docs/ISSUE_CATALOG.md:20`.

---

### 1. Kubernetes manifests

**Artifact** — `deploy/k8s/base/{namespace,configmap,secret.example,api-deployment,worker-deployment,migrate-job,networkpolicy,sweep-cronjob,gpu-worker-job}.yaml` + `overlays/{dev,staging,prod}/`.

**Approach** — Kustomize (no Helm dependency for one consumer). Two hard rules:

**(a) Force Postgres. Do not put the store on a PVC.** SQLite is non-viable in K8s: a rescheduled
pod loses the file, silently destroying the verdict log the whole product depends on; a PVC-backed
SQLite also breaks F1 and F6. Make `VALIDSIM_STORE=postgres` a **hard admission failure** via an
`initContainer` running `validsim doctor --require-durable-store`, and set `VALIDSIM_ENV=production`
so the existing fail-closed auth check (`api/main.py:537-541`) is enforced by the platform rather
than by convention.

**(b) Separate liveness from readiness** (F8).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: validsim-api
spec:
  replicas: 2
  revisionHistoryLimit: 3                 # enables `kubectl rollout undo`
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  selector: { matchLabels: { app: validsim, role: api } }
  template:
    metadata:
      labels: { app: validsim, role: api }
      annotations:
        prometheus.io/scrape: "true"      # item 9 scrapes this
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001                   # matches Dockerfile uid/gid 1001
        fsGroup: 1001
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway
          labelSelector: { matchLabels: { app: validsim, role: api } }
      containers:
        - name: api
          image: ghcr.io/OWNER/REPO@sha256:IMAGE_DIGEST   # digest, never a tag (item 11)
          ports: [ { containerPort: 8000, name: http } ]
          envFrom:
            - configMapRef: { name: validsim-config }     # FLAT keys only (F7)
            - secretRef:    { name: validsim-secrets }    # ESO-projected (item 6)
          env:
            - { name: VALIDSIM_ENV,       value: production }
            - { name: VALIDSIM_STORE,     value: postgres }
            - { name: VALIDSIM_JOB_QUEUE, value: redis }
            - { name: VALIDSIM_LOG_LEVEL, value: INFO }   # F10
          startupProbe:
            httpGet: { path: /api/v1/livez, port: http }
            failureThreshold: 30; periodSeconds: 2
          livenessProbe:                   # F8: config-only, so liveness only
            httpGet: { path: /api/v1/livez, port: http }
            periodSeconds: 20; timeoutSeconds: 3; failureThreshold: 3
          readinessProbe:                  # dependency-aware, unlike /api/v1/health
            exec:
              command: ["python","-m","validsim.cli","doctor","--check","store","--quiet"]
            periodSeconds: 10; timeoutSeconds: 5; failureThreshold: 3
          resources:
            requests: { cpu: 250m, memory: 512Mi }   # PROVISIONAL — retune after F3 is fixed
            limits:   { memory: 1Gi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts: [ { name: tmp, mountPath: /tmp } ]
      volumes: [ { name: tmp, emptyDir: {} } ]
---
apiVersion: v1
kind: Service
metadata: { name: validsim-api }
spec:
  type: ClusterIP
  selector: { app: validsim, role: api }
  ports: [ { port: 80, targetPort: http } ]
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: validsim-api }
spec:
  minAvailable: 1
  selector: { matchLabels: { app: validsim, role: api } }
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: validsim-api }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: validsim-api }
  minReplicas: 2
  maxReplicas: 12
  metrics:
    - type: Resource
      resource: { name: cpu, target: { type: Utilization, averageUtilization: 70 } }
  behavior:
    scaleDown: { stabilizationWindowSeconds: 300 }   # validation load is bursty; don't thrash
```

The `worker` needs its own Deployment (no Service) reusing the same env, with the Redis-PING probe
compose already solved (`docker-compose.yml:152-161`) — the base image HEALTHCHECK hits HTTP :8000,
which a non-HTTP worker can never pass.

```yaml
# deploy/k8s/base/networkpolicy.yaml — default deny, then allow the DAG
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: default-deny }
spec: { podSelector: {}, policyTypes: [Ingress, Egress] }
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: validsim-allow }
spec:
  podSelector: { matchLabels: { app: validsim } }
  policyTypes: [Ingress, Egress]
  ingress:
    - from: [ { namespaceSelector: { matchLabels: { name: ingress } } } ]
      ports: [ { port: 8000 } ]
  egress:
    - to: [ { podSelector: { matchLabels: { app: validsim, role: postgres } } } ]
      ports: [ { port: 5432 } ]
    - to: [ { podSelector: { matchLabels: { app: validsim, role: redis } } } ]
      ports: [ { port: 6379 } ]
    - to: [ { namespaceSelector: { matchLabels: { name: dns } } } ]
      ports: [ { port: 53, protocol: UDP } ]
    # NOTE: DNS egress must target kube-dns explicitly; `port: 53` alone is not
    # sufficient on clusters with a dedicated DNS namespace. Add the TCP rule too.
```

**Do not run Postgres/Redis in-cluster by default** — managed is the reference architecture
(item 8); in-cluster (CloudNativePG / StatefulSet) is the air-gapped fallback (item 7).

- **New deps:** none at runtime (YAML only). Optional CI: `kubeconform`/`kubeval`, `kustomize`.
- **Effort:** 24h (manifests 12h, `livez`/`readyz` 6h, admission + CI validation 6h).
- **Timeline:** 1.5 weeks, after item 3.
- **Blocks enterprise? YES — hard.** There is currently no answer to "where does this run?"

---

### 2. GPU orchestration

**Artifact** — `deploy/k8s/gpu/{gpu-node-labels.yaml, gpu-sweep-job.yaml, argo/sweep-pipeline.yaml}`.

**Approach** — the GPU path is already contract-first: `VALIDSIM_BACKEND=isaac` +
`VALIDSIM_ISAAC_WORKER_URL` + `VALIDSIM_ISAAC_WORKER_KEY` (`.env.example:96-113`,
`docs/isaac-worker.md`). This is a *scheduling* problem, not a rewrite.

```yaml
# deploy/k8s/gpu/gpu-sweep-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: validsim-sweep
  annotations:
    argocd.argoproj.io/sync-wave: "2"      # after postgres/redis/config
spec:
  backoffLimit: 2
  ttlSecondsAfterFinished: 86400           # don't accumulate finished Jobs
  template:
    spec:
      restartPolicy: Never
      nodeSelector:
        nvidia.com/gpu.present: "true"     # set by the device plugin below
        validsim.io/gpu-pool: "isaac"
      tolerations:
        - { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }
      containers:
        - name: sweep
          image: ghcr.io/OWNER/REPO-isaac-worker@sha256:IMAGE_DIGEST   # NOT the API image
          args: ["python","-m","validsim.cli","run",
                 "--episodes","5000","--threshold","85",
                 "--backend","isaac","--json","--out","/out/scorecard.json"]
          envFrom:
            - configMapRef: { name: validsim-config }
            - secretRef:    { name: validsim-secrets }
          resources:
            limits:   { nvidia.com/gpu: 1 }   # integer only — GPUs are not fractionally allocatable
            requests: { nvidia.com/gpu: 1 }   # K8s requires request == limit for extended resources
          volumeMounts:
            - { name: out, mountPath: /out }
            - { name: shm, mountPath: /dev/shm }
      volumes:
        - name: out
          persistentVolumeClaim: { claimName: validsim-sweeps }   # scorecards off-cluster (item 4)
        - name: shm
          emptyDir: { medium: Memory, sizeLimit: 8Gi }   # Isaac/Omniverse needs large /dev/shm
```

```yaml
# deploy/k8s/gpu/gpu-node-labels.yaml
# NVIDIA's device-plugin DaemonSet already advertises `nvidia.com/gpu` capacity; this
# manifest only adds ValidSim's own pool label so sweeps land on Isaac-capable nodes.
apiVersion: v1
kind: Node
metadata:
  labels:
    validsim.io/gpu-pool: "isaac"
    nvidia.com/gpu.present: "true"
```

**Device plugin — do not hand-roll it.** Deploy NVIDIA's
`nvcr.io/nvidia/k8s-device-plugin:v0.15.0` (DaemonSet; hostPath for `/var/run/cdi`;
`nvidia-container-runtime` configured; `accept-nvidia-visible-devices-envvar-when-unprivileged=false`).
ValidSim contributes only the node-label Job plus the DaemonSet in the `gpu` profile — re-implementing
CUDA device allocation fails the vault's own "does not add a hire before month 4" test
(`vault/04 - Engineering/Tech Stack.md:87-88`).

**Isaac image reality check.** The compose placeholder points at a `Dockerfile.worker` on an Isaac
base (~15 GB) that **does not exist** (only one `Dockerfile`, verified). Isaac Sim also carries an
NVIDIA EULA that blocks redistribution. So the vault's own **BYO-GPU SKU**
(`vault/06 - Business/Unit Economics.md:44`) is the right model: ship the CPU orchestration image,
let the customer mount their own Isaac worker. Do not publish a 15 GB Isaac image to GHCR.

**Argo sweep pipeline** — `deploy/k8s/argo/sweep-pipeline.yaml`: a `Workflow` with a `fanout`
entrypoint, `parallelism: 4` (matching the 4×A100 target, `Tech Stack.md:82`), one `sim-job`
template running `validsim run` and a `gate` template running `validsim gate --run-id … --json` so
a BLOCK fails the workflow. Also ship a Make wrapper (`make sweep CHECKPOINT=… EPISODES=…`) so
users without Argo get the identical pipeline. *(I read "MakeItSimple" in the brief as a simple
Make target; if it names a specific internal tool, adapt.)*

- **New deps:** Argo Workflows CRDs (or none, Make path); NVIDIA device plugin; a real
  `Dockerfile.isaac` or customer-supplied base.
- **Effort:** 40h (node pool 8h, sweep Job + Argo 16h, device-plugin wiring 8h, Isaac image 8h+).
- **Timeline:** 3 weeks — **gated on a GPU host existing.** There is none in the repo today
  (`docker-compose.yml:167-169`), so this cannot be validated until one is provisioned.
- **Blocks enterprise? YES** for the GPU-parallel claim. **NO** for the mock MVP, which is
  genuinely useful today.

---

### 3. Migration tooling

**Artifact** — `migrations/` (Alembic), `alembic.ini`, a `migrate` sub-typer on `validsim/cli.py`,
`deploy/k8s/base/migrate-job.yaml` (K8s pre-install hook / Argo sync-wave 1).

**Approach**

1. **Alembic, initial revision hand-written** from the two DDL constants
   (`store/postgres.py:79-93`, `store/sqlite.py:36-55`) — not `autogenerate`, which needs a live DB.
2. **Bootstrap the version table BEFORE the first migration runs** (constraint from `c3-data`,
   verified as a real ordering hazard). `alembic upgrade head` against an *existing* database (one
   created by today's lazy DDL) needs the version table to already exist and be readable. Putting
   `CREATE TABLE … schema_version` inside migration `0001` makes the first upgrade fail against
   every deployed database. So the table creation + backfill-to-`0001` is a **standalone bootstrap
   step** that runs *before* `alembic upgrade head` — invoked by `validsim migrate bootstrap`.
3. **Own ledger, not `alembic_version`** — so the API and `doctor` can read it without importing
   Alembic:

```sql
CREATE TABLE IF NOT EXISTS validsim_schema_version (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by  TEXT NOT NULL DEFAULT current_user,
    duration_ms INTEGER
);
```

4. **Advisory lock** so replicas cannot race: `pg_advisory_lock(hashtext('validsim_migrations'))`.
5. **Retire runtime DDL (F1).** `_ensure_ready()` stops issuing DDL and instead *verifies* the
   expected revision, failing loudly with an actionable message. This also removes DDL privileges
   from the runtime DB role — a real security win alongside the `SECURITY.md:305` gap.
6. **Corrections as their own revisions**, each independently revertible:

```
migrations/versions/
  0001_schema_version.sql          -- ledger (baseline-safe)
  0002_baseline_stamp.sql          -- stamps pre-Alembic databases
  0003_idx_checkpoint_created.sql  -- F2  CREATE INDEX CONCURRENTLY (checkpoint_id, created_at)
  0004_widen_run_id.sql            -- F4  16-hex / UUIDv7 (additive, backward compatible)
  0005_metrics_aggregate.sql       -- F3  index supporting the aggregate metrics path
  0006_quotas.sql                  -- item 12  org + quota tables
```

**Expand/contract process** (`docs/migrations.md`):

| Phase | Action | Safe under live traffic? |
|---|---|---|
| **Expand** | `ADD COLUMN` nullable / `ADD TABLE` / `CREATE INDEX CONCURRENTLY` | Yes — old and new code coexist |
| Deploy | Roll new code; dual-write both shapes | Yes — rolling update, `maxUnavailable: 0` |
| **Backfill** | Batched `UPDATE` (1k rows/txn), resumable, via `validsim migrate backfill` | Yes — idempotent, chunked |
| Verify | `doctor --check schema` + CI parity job | Yes |
| **Contract** | `DROP COLUMN` / `SET NOT NULL` in a **later** release (≥1 release apart) | Yes — old code is gone |

Rules that make it real: never combine expand and contract in one release; every migration documents
a `downgrade()`; `CREATE INDEX CONCURRENTLY` never runs inside a transaction; any migration taking
`ACCESS EXCLUSIVE` on a large table is scheduled, not shipped in-line.

- **New deps:** `alembic>=1.13` in `requirements.txt` + `[project.dependencies]`.
- **Effort:** 28h (scaffold 8h, baseline + advisory lock 8h, CLI 6h, docs + CI parity 6h).
- **Timeline:** 1 week. **Build this first.**
- **Blocks enterprise? YES — hard.** No safe upgrade path exists, and F1 means the app role can
  mutate its own schema at runtime.

---

### 4. IaC (Terraform)

**Artifact** — `deploy/terraform/modules/validsim/{network,kubernetes,postgres,redis,objectstore,secrets}/` + `environments/{dev,staging,prod}/`. A **module**, not a root config, so customers can consume it.

```hcl
# modules/postgres/main.tf (excerpt) — Multi-AZ, encrypted, PITR
resource "aws_db_instance" "validsim" {
  identifier               = "validsim-prod"
  engine                  = "postgres"
  engine_version          = "16.4"
  instance_class          = "db.r6g.xlarge"
  allocated_storage       = 100
  max_allocated_storage   = 500          # storage autoscaling
  multi_az                = true
  db_name                 = "validsim"
  username                = "validsim"
  password                = var.master_password      # from Secrets Manager, never committed
  storage_encrypted       = true
  kms_key_id              = var.kms_key_arn
  backup_retention_period = 14                        # matches runbook's ≥14-day rule
  deletion_protection     = true
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.env}-final"
  copy_tags_to_snapshot   = true
  performance_insights_enabled = true
  # Parameter-group tuning MUST be measured, not guessed: F3 currently makes
  # /metrics O(all rows) — fix that before spending money on IOPS.
  parameter_group_name    = aws_db_parameter_group.validsim.name
}
```

Other modules: `network` (VPC, deny-by-default SGs), `kubernetes` (EKS + OIDC/IRSA + optional GPU
node group), `redis` (Sentinel mode + auth token), `objectstore` (S3 `sweeps/`,`reports/`,
`backups/` with SSE-KMS + versioning + lifecycle), `secrets` (Secrets Manager + ESO CRs).

**`created_at` is TEXT — a real index decision worth stating.** Both stores persist `created_at` as
ISO-8601 **TEXT** (`postgres.py:87`, `sqlite.py:42`) and rely on lexical comparison
(`postgres.py:469-472`) — which is why `idx_*_created` works at all. `TIMESTAMPTZ` would be more
correct and would enable time partitioning, but it is a **breaking change needing dual-read**.
Recommend as **Phase 2** (expand: add `created_at_ts TIMESTAMPTZ`, backfill, switch reads, contract
the text column). Do not attempt it in the first migration.

- **New deps:** Terraform ≥1.6 + a cloud provider. Air-gap: `terraform-provider-libvirt` / OpenTofu
  with a documented on-prem reference architecture.
- **Effort:** 48h (skeleton 12h, RDS+Redis 12h, EKS+GPU node group 16h, S3+lifecycle 8h).
- **Timeline:** 3 weeks. This is the "reproducible environment" story investors ask for.
- **Blocks enterprise? YES** — no customer can audit or reproduce the infrastructure without it.

---

### 5. Backup / restore automation

**Today (verified).** `docs/runbook.md:350-356` is admirably honest: the ≥14-day daily full-dump
procedure is the MVP operating procedure, "but note that this repository contains **no scheduler or
retention automation**: schedule it outside the repo and enforce retention there." This closes that.

**Artifacts**
1. `validsim/ops/backup.py` — a backup **service** (not crontab): `create|verify|restore-drill`.
2. `.github/workflows/backup.yml` — scheduled `cron: "17 3 * * *"` (03:17 UTC, deliberately off the
   hour to dodge the GitHub scheduler stampede) against a disposable Postgres service container.
3. `scripts/backup/k8s-cronjob.yaml` — a `CronJob` for self-hosted (GH Actions is unavailable
   air-gapped, item 7).
4. A `validsim_backup_last_success_timestamp_seconds` gauge + alert (item 9).

**Approach**
- `pg_dump -Fc` streamed with `-T` (TTY safety, as the runbook already insists,
  `docs/runbook.md:371-374`) → SHA-256 sidecar + JSON manifest (db, **schema_version**, row count,
  size, created_at) → S3 with **lifecycle rules** (daily 14d, monthly 90d) + **SSE-KMS**. Embedding
  `schema_version` in the manifest means a restore knows which code version to point at it — the
  expand/contract payoff from item 3.
- **Restore drill** — the step everyone skips and the only thing that proves the backup. Restore
  into a **disposable** database, then prove a known run is readable through the real code path:
  ```bash
  createdb validsim_restore
  pg_restore -U validsim -d validsim_restore --exit-on-error --single-transaction < backup.dump
  VALIDSIM_PG_URL=…@…/validsim_restore VALIDSIM_STORE=postgres \
    python -m validsim.cli gate --run-id "$KNOWN_RUN" --json     # must exit 0
  ```
  Reuse the runbook's verified flags verbatim (`docs/runbook.md:399, 411`): `--exit-on-error
  --single-transaction`; never `--jobs` with a streamed archive; never `-v` on cleanup.
- **Verification metrics** (feed item 9):
  ```
  validsim_backup_last_success_timestamp_seconds 1234567890
  validsim_backup_restore_drill_success 1
  validsim_backup_last_dump_bytes 104857600
  ```
  Alert at `time() - … > 26*3600`, so one missed run pages before two.
- **PITR/WAL is the upgrade path, not the MVP** — the runbook says so itself
  (`docs/runbook.md:355-356`). Terraform enables it; recommend as Phase 2.

- **New deps:** `boto3` (or `minio`/`rclone` client for self-hosted) as an **optional extra**
  (`[project.optional-dependencies] backup`) so the core install stays light.
- **Effort:** 24h (service 10h, S3+lifecycle 4h, workflow 4h, drill + metrics 6h).
- **Timeline:** 1 week.
- **Blocks enterprise? YES — hard.** Every security questionnaire asks "how do you recover?" and the
  current honest answer is "manually".

---

### 6. Secrets management

**Today (verified).** Env-var only; `docker-compose.yml:64` uses
`${POSTGRES_PASSWORD:?…}` and `.env` is gitignored (`.gitignore:60-61`). `SECURITY.md:305` lists
"secrets management beyond env vars" as a known gap. `Tech Stack.md:48` names "AWS Secrets Manager
/ HashiCorp Vault" **without choosing** — I asked `c5-integrations` to confirm and defaulted below.

**Artifact** — `.sops.yaml`, `deploy/secrets/*.sops.yaml` (age-encrypted, committed),
`ExternalSecret` CRs, `docs/secrets.md`, `validsim doctor --check secrets`.

**Three-tier model, so every customer finds their tier:**

| Tier | Tool | Best for | Cost |
|---|---|---|---|
| **Default** | **SOPS + age** → ESO → K8s Secret | GitOps customers, air-gapped | free, no infra |
| **Managed** | ESO → AWS/GCP Secret Manager | Cloud customers | ~0 |
| **Self-hosted** | ESO → HashiCorp Vault (K8s auth) | Regulated enterprises | ops burden |

- **Encrypted in git, never decrypted in git.** `sops` + age keys distributed out-of-band; CI
  verifies with `sops --decrypt`; `gitleaks`/`detect-secrets` in pre-commit + CI so a plaintext
  secret cannot land.
- **Rotation procedure** (the part enterprises actually need):
  1. Create the new version in the backing store (dual-version period).
  2. `sops` update the encrypted file → commit.
  3. ESO projects it → `kubectl rollout restart deploy/validsim-api` — **required**, because
     `VALIDSIM_API_KEY` is read once at `create_app()` (`api/main.py:535`, and the runbook says so
     at `docs/runbook.md:141-145`).
  4. Verify: `/api/v1/health` shows `auth_enabled: true`, and the old key gets a real 401.
  5. Revoke the old version; record the rotation.
  6. **Cadence:** API key + DB creds 90d; `VALIDSIM_LLM_API_KEY` / `VALIDSIM_SMTP_PASSWORD` 60d or
     on suspicion; Isaac worker key per-customer.
- **`validsim doctor --check secrets`** fails when a secret-bearing env var is a **literal** while
  `VALIDSIM_ENV=production` and a file-mounted `/var/run/secrets/validsim/*` projection is expected,
  and flags weak/short/default keys. That is the "fails on plaintext secrets in env" behaviour.

- **New deps:** `sops` + `age` (CI + operator), `external-secrets` (K8s), optionally Vault.
- **Effort:** 20h (SOPS+ESO 10h, doctor check 6h, rotation doc 4h).
- **Timeline:** 1 week.
- **Blocks enterprise? YES.** A single shared `VALIDSIM_API_KEY` with no rotation
  (`SECURITY.md:306`) is a procurement blocker even before multi-tenancy.

---

### 7. On-prem / air-gapped deployment

**Artifact** — `dist/offline/` built by `scripts/offline-bundle.{sh,ps1}`, a `VALIDSIM_TELEMETRY=0`
mode, `docs/air-gap.md`.

```
validsim-offline-<version>/
  images/validsim-<ver>.tar            # docker save; multi-arch
  images/postgres-16-alpine.tar
  images/redis-7-alpine.tar
  wheelhouse/validsim-*.whl + requirements.txt wheels   # for `pip install --no-index`
  manifests/k8s/*.yaml                 # same as item 1, no external refs
  secrets/secrets.sops.yaml + age pubkey                 # customer supplies the private key
  LICENSE.txt                           # Isaac Sim EULA is separate — customer-side
  VERSION, SHA256SUMS, VERIFY.sh
```

- **No-telemetry mode, stated honestly.** There is **no telemetry in the code today** (verified: no
  phone-home/analytics endpoint exists). So `VALIDSIM_TELEMETRY=0` is a *contract plus an auditable
  check* — `doctor` verifies egress-making vars (`VALIDSIM_LLM_BASE_URL`,
  `VALIDSIM_ISAAC_WORKER_URL`, `VALIDSIM_WEBHOOKS_LIVE=1`) point at internal hosts or are disabled,
  and the NetworkPolicy blocks egress. The docs must say: *"no telemetry is collected; this flag
  makes egress-blocking auditable and gates webhook/LLM egress."* I will not claim a feature that
  does not exist.
- **Local Postgres for air-gap** (item 8's self-hosted branch): CloudNativePG or a StatefulSet with
  `wal-g` shipping to an on-prem S3-compatible store (MinIO). Same backup discipline as item 5.
- **`VERIFY.sh` runs in the disconnected environment**: verify `SHA256SUMS`, `docker load`,
  `pip install --no-index -r wheelhouse/`, then `validsim doctor`. A bundle the customer cannot
  verify offline is not an offline bundle. (Requires F9's pinning to be meaningful.)

- **New deps:** none at runtime; `docker save/load`, `pip download --platform`.
- **Effort:** 24h (bundle script 10h, multi-arch wheelhouse 8h, docs 6h).
- **Timeline:** 1.5 weeks.
- **Blocks enterprise? YES** for on-prem/robotics/regulated (defense, critical manufacturing).
  NO for cloud customers.

---

### 8. Multi-region / HA

**Artifact** — `docs/architecture/HA.md` + Terraform `multi-region` overlay + Redis Sentinel
manifests. Mostly a documented reference architecture plus a connection-pool fix.

**Postgres HA — managed by default; self-hosted Patroni only for air-gap.**

| Option | Verdict | Why |
|---|---|---|
| **Managed (RDS Multi-AZ / Cloud SQL)** | **Default** | Automated failover, PITR, no ops. A two-founder team should not run Patroni. |
| **Self-hosted Patroni + etcd** | Air-gap / on-prem only | Real on-call burden; justified only when egress is forbidden. |

- **Critical prerequisite, and it must come BEFORE connection pooling** (corrected after review by
  `c4-reliability`, who identified a stronger reason than mine). I originally framed F6 as "each
  process is serialised, so add `psycopg_pool`". That understates it. `_ensure_ready()` runs
  `CREATE TABLE IF NOT EXISTS` **plus 5× `ALTER TABLE ADD COLUMN IF NOT EXISTS`**
  (`postgres.py:370-395`) *inside the same `threading.Lock`* that every write takes
  (`postgres.py:430-431`) — and those `ALTER`s take **`ACCESS EXCLUSIVE`** locks. So a cold start is
  not gentle serialisation: **N replicas all cold-start, all queue behind one lock, each running 6
  lock-taking DDL statements in turn, while serving zero traffic.** Add a replica scaling up
  mid-incident and you have DDL contention on the write path during precisely when you need it
  least.
  **Correct order: (1) move DDL out of `_ensure_ready` into `validsim migrate` → (2) `psycopg_pool`
  → (3) scale replicas.** Doing the pool first leaves DDL-in-the-lock in place. This is the
  strongest argument for item 3 outranking everything else.
  **Do:** add `psycopg_pool` with health-checked connections, plus a CI job that kills the DB
  mid-request and asserts recovery. **Do not** claim HA until that test passes.
- **Redis:** Sentinel (3 nodes) for the job queue; Cluster is unnecessary until the queue shards.
  Note the queue is **not** the system of record — `docs/runbook.md:443-445` is explicit that Redis
  loss costs in-flight jobs, not scorecards. So Redis HA is about job durability, not verdict
  durability.
- **Stateless API replicas** behind a cloud LB work because the app is already stateless (store and
  queue injected via `app.state`, `api/main.py:516`) — **with two exceptions**: F5 (process-local
  limiter ⇒ limit ×N and shared-IP throttling; fix with a Redis-backed limiter keyed on trusted
  forwarded IP, fail-open + metric) and F6 (one connection per process ⇒ add `psycopg_pool`).
- **Rate-limiter SLO:** proposed to `c4-reliability` (<1% of writes); awaiting their number rather
  than inventing one.

- **New deps:** `psycopg_pool` (optional extra), Redis Sentinel.
- **Effort:** 40h (pool 12h, shared limiter 12h, failover CI test 8h, HA doc 8h).
- **Timeline:** 3 weeks.
- **Blocks enterprise? YES** for Tier-4 (insurers/regulators, `Solution Architecture.md:113`).
  NO for MVP.

---

### 9. Observability stack

**Artifact** — `deploy/observability/{prometheus.yml, alerts.yml, grafana/dashboards/validsim.json,
loki/loki-config.yaml, promtail/promtail-config.yaml}` + an `observability` compose profile.

The app already emits Prometheus text (`api/metrics.py:131-192`) and JSON logs
(`logging.py:79-110`), so this is wiring, not invention.

```yaml
  prometheus:
    profiles: ["observability"]
    image: prom/prometheus:v2.54.1
    volumes:
      - "./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro"
      - "prom-data:/prometheus"
    ports: [ "127.0.0.1:9090:9090" ]     # loopback-only, matching existing compose style
  grafana:
    profiles: ["observability"]
    image: grafana/grafana:11.1.0
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_SECURITY_ADMIN_PASSWORD: "${GRAFANA_PASSWORD:?set GRAFANA_PASSWORD}"   # F9-style fail-fast
    volumes: [ "./observability/grafana:/var/lib/grafana" ]
  loki:
    profiles: ["observability"]
    image: grafana/loki:3.1.1
  promtail:
    profiles: ["observability"]
    image: grafana/promtail:3.1.1
    volumes:
      - "/var/lib/docker/containers:/var/lib/docker/containers:ro"
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
```

```yaml
# deploy/observability/alerts.yml — every expr below uses metrics that already exist
groups:
  - name: validsim
    rules:
      - alert: ValidSimAPIDown
        expr: up{job="validsim-api"} == 0
        for: 5m
        annotations: { summary: "ValidSim API unreachable for 5m" }
      - alert: ValidSimErrorRateHigh
        expr: |
          sum(rate(validsim_http_requests_total{class="5xx"}[5m]))
            / sum(rate(validsim_http_requests_total[5m])) > 0.05
        for: 10m
      - alert: ValidSimThrottled            # F5 visibility
        expr: rate(validsim_http_requests_total{class="4xx"}[5m]) > 0.1
        for: 15m
        annotations: { summary: "Sustained 4xx — check rate limiter (process-local) and auth" }
      - alert: ValidSimBlockRateSpike       # product signal, not just infra
        expr: |
          increase(validsim_blocks_total[1h])
            / clamp_min(increase(validsim_runs_total[1h]), 1) > 0.5
        for: 30m
        annotations: { summary: ">50% of validations BLOCKed in the last hour" }
      - alert: ValidSimBackupStale          # item 5
        expr: time() - validsim_backup_last_success_timestamp_seconds > 93600
        for: 1h
      - alert: ValidSimMetricsScrapeTooSlow # F3 canary
        expr: scrape_duration_seconds{job="validsim-api"} > 2
        for: 10m
        annotations: { summary: "Metrics scrape slow — /metrics loads full run history" }
```

**Grafana dashboard** `validsim.json`, checked in and provisioning-mounted so one profile gives it
with zero clicks: request rate by class, p95 latency, run/approve/block trend, composite-score
gauge, queue depth, build info, backup freshness. **Use `max by (version)` for build info (F11) —
never `sum` across a rolling deploy.**

**Loki + Promtail** ingest the existing JSON logs, which carry `request_id` — so an operator can
join a client's `X-Request-ID` to a specific run.

- **New deps:** images only (`prometheus`, `grafana/grafana`, `grafana/loki`, `grafana/promtail`).
- **Effort:** 20h (prometheus+alerts 8h, dashboard 8h, loki/promtail 4h).
- **Timeline:** 1 week.
- **Blocks enterprise? MEDIUM** — not a hard blocker, but "no dashboards" is a serious demo/ops
  liability and a support-cost multiplier. High visibility per hour; do it early.

---

### 10. Docker Compose profile matrix

**Today (verified).** `docker-compose.yml` has **no `profiles:` key anywhere** — one
undifferentiated stack, with the GPU worker as a commented placeholder (lines 167-192).

| Profile | Services | Use |
|---|---|---|
| *(none — default)* | `api`, `worker`, `redis`, `postgres` | `make docker-up` unchanged. **Zero-risk.** |
| `dev` | docs: `VALIDSIM_STORE=memory`, `VALIDSIM_BACKEND=mock` | Laptop; no Docker DBs; fully side-effect free |
| `gpu` | `worker-gpu` (real service, replacing the comment) | Isaac host; needs NVIDIA Container Toolkit |
| `observability` | `prometheus`, `grafana`, `loki`, `promtail` | item 9 |
| `airgap` | + `minio`, `registry:2` mirror | offline bundle pull + backup target |
| `backup` | + one-shot `backup` service | restore drills |

```yaml
  worker-gpu:
    profiles: ["gpu"]
    build: { context: ., dockerfile: Dockerfile.isaac }   # MUST exist before uncommenting
    deploy:
      resources:
        reservations:
          devices: [ { driver: nvidia, count: all, capabilities: [gpu] } ]
```

Makefile targets so the matrix is discoverable: `make dev-up`, `make obs-up`, `make gpu-up`,
`make airgap-up`.

- **New deps:** none (profile keys only).
- **Effort:** 12h (profiles 4h, real `Dockerfile.isaac` 6h, Makefile 2h).
- **Timeline:** 3 days.
- **Blocks enterprise? NO — cheapest enabler here.** Do it early; it makes every other profile
  testable.

---

### 11. Release engineering

**Today (verified).** `release.yml` builds an sdist+wheel and runs `twine check` but **never
uploads** — and says so itself (`release.yml:153`). The image pushes to GHCR as `latest` + tag
(`release.yml:113-115`). And `actions/validate/action.yml:108-110` already contains a
`pip install validsim==${VS_VERSION}` path **reserved** for "future" publication. The call-site
exists; only the publish step is missing.

**Approach**
1. **Semver + changelog gate.** `CHANGELOG.md` already follows Keep a Changelog (lines 5-6) and the
   version comes from `validsim.__version__` (`__init__.py:9`, `pyproject.toml:44-45`). Add
   `scripts/check_version.py` failing the release when: tag ≠ `__version__`; the version already
   exists on PyPI/GHCR; or `[Unreleased]` is non-empty (forcing a changelog entry).
2. **Make `pip install validsim` actually work** — the adoption blocker. Add a gated `publish` job
   using PyPI **Trusted Publishing (OIDC, no long-lived token)**, behind a **manual approval
   environment**, plus a `--repository` smoke install. Keeping today's "check but don't publish" as
   the default and publishing only on explicit approval is the honest, safe path.
3. **Multi-arch (amd64 + arm64).** `platforms: linux/amd64,linux/arm64`. Verify first:
   `psycopg[binary]` and `reportlab` must have arm64 wheels — if `reportlab` lacks them, the PDF
   endpoint 501s on arm64 (`docs/runbook.md:209-218`), so don't promise arm64 before checking.
4. **Provenance / SBOM / signing.** `provenance: mode=max` + `sbom: true` on
   `build-push-action`, `cosign` keyless signing, SBOM attached. This closes the
   "signed container images / SBOM generation" gap at `SECURITY.md:307`.
5. Align the publish gate with the support matrix already written at `SECURITY.md:177-203`.

- **New deps:** PyPI Trusted Publisher config, `cosign`, optional SBOM action.
- **Effort:** 24h (version gate 4h, publish job 8h, multi-arch 6h, provenance/SBOM 6h).
- **Timeline:** 1.5 weeks.
- **Blocks enterprise? YES for adoption** — nobody installs a package that isn't published, and the
  validate action's documented path is currently dead. SBOM/provenance is a strong enterprise
  checkbox.

---

### 12. Cost controls

**Today (verified).** No quotas, no org model, no caps. `config.py:136-137` bounds `episodes` to
≤100000 and `adversarial_count` to ≤1000, and `jobs/queue.py:65` defaults queue depth to 1000 — but
these are **per-request validation bounds, not spend controls**. There is no
`VALIDSIM_MAX_EPISODES`, no per-org accounting, and no measured cost anywhere.

**The stakes.** `vault/06 - Business/Unit Economics.md:43` assumes a 5,000-episode run is ≤2
A100-hours, billed at $3.50/A100-hr, giving "GPU COGS ≈ $5–7 per 5K run against a $75 list price"
and a **70–80% gross margin**. That is the entire margin thesis and **none of it is measured** (the
doc's own warning, lines 14-15). This item converts the assumption into data.

**Approach**
1. **Episode budgets, enforced server-side.** `VALIDSIM_MAX_EPISODES` (per-run cap, default 100000,
   matching the existing `TaskConfig.episodes` ceiling at `config.py:136`) → `422` with an
   actionable message. `VALIDSIM_MONTHLY_EPISODE_BUDGET` (per-org, rolling 30d) → `429`/`402` when
   exceeded.
2. **Per-org quotas.** Needs a tenant key: `VALIDSIM_ORG_ID` (auth is a single shared
   `VALIDSIM_API_KEY` today, `SECURITY.md:306`). Schema in migration `0006`:
   `organizations(org_id PK, name, monthly_episode_budget, monthly_gpu_budget_s, created_at)` and
   `usage(org_id, window_start, episodes, gpu_seconds, runs, PRIMARY KEY(org_id, window_start))`.
   A quota middleware checks before enqueue; a nightly rollup aggregates from existing
   `validations` rows (which already carry `created_at` and episode count in the scorecard).
3. **Cost-per-validation reporting — the key artifact.** Instrument the worker to record
   `gpu_seconds` and `episodes` per run (wall-clock around `run_and_score`; for Isaac, sum
   worker-reported GPU time). Emit `validsim_run_cost_usd{checkpoint,task}` per run and an aggregate
   `validsim_cost_per_validation_usd`. Then `validsim cost report --period 30d`: runs, episodes,
   GPU-hours, est. COGS, COGS/run, COGS per 1k episodes, implied gross margin at list price. Price
   the GPU-hour from one knob `VALIDSIM_GPU_HOUR_USD` (default `3.50`, matching the vault line) so
   the model re-prices when vendor pricing changes.
4. **Keep existing back-pressure**: queue-depth cap already returns `503` (`jobs/router.py:104-120`).
   Quotas layer on top, they don't replace it.
5. Add the three undocumented env vars (F10) to `.env.example` here.

- **New deps:** none (Postgres + existing `redis`).
- **Effort:** 32h (caps 8h, org + quota schema/middleware 14h, cost instrumentation + report 10h).
- **Timeline:** 2 weeks.
- **Blocks enterprise? YES for the business model** (and any usage-based pricing). Not a technical
  blocker for single-tenant.

---

## 3. Decisions agreed with teammates (2026-09-25)

This proposal was reviewed by `c3-data`, `c3-api`, `c4-reliability` and `c5-integrations`. Four of
my positions were **wrong or incomplete** and are corrected above. The agreed positions:

### 3.1 File ownership (no overlap) — agreed with `c3-data`

| Area | Files | Owner |
|---|---|---|
| Alembic scaffolding + `validsim migrate` CLI | NEW `validsim/store/migrations/` | **c5-platform** (me) |
| Store migration hook: delete inline DDL, add version check | `store/postgres.py:99-105, 370-395`, `store/sqlite.py:59-65, 181-191` | **c3-data** |
| Composite index (F2) | migration DDL only | **c5-platform** (me) |
| Metrics aggregate method + `api/metrics.py:104-121` | store interface | **c3-data** |
| Queue-side run-id (F4) | `jobs/queue.py:463-466` | **c5-platform** (me) — c3-data mirrors in store |

### 3.2 Lazy DDL: **remove, do not gate** — agreed with `c3-data`

Stronger than my original reasoning. `IF NOT EXISTS` does **not** prevent the `ACCESS EXCLUSIVE`
lock — Postgres takes the lock to *evaluate* the condition — and `ADD COLUMN IF NOT EXISTS` is a
no-op that reports success, so it can never detect a wrong-typed or half-applied column. It gives
the *appearance* of idempotence while being blind to the breakage it should catch. Replacement is
**version-check on open, fail fast, do not auto-migrate**:

```python
def _ensure_ready(self):
    if self._pool is None:
        self._pool = ...                       # open pool
        self._verify_schema_version(conn)      # SELECT version FROM validsim_schema_version
        # mismatch -> RuntimeError("schema v{N} != code v{M}; run `validsim migrate`")
    return self._pool
```

This preserves the side-effect-free-construction property ADR 0004 depends on, and an old pod
mid-rolling-upgrade now **fails fast and visibly** instead of silently `ALTER`ing underneath a new
one. It also lets the runtime DB role drop to `SELECT, INSERT` on `validations` — least privilege.

### 3.3 `CREATE INDEX CONCURRENTLY` caveat — flagged by `c3-data`, must be in the migration

It **cannot run inside a transaction block**, and Alembic runs migrations in a transaction by
default — so it needs `op.execute(...)` with autocommit enabled around it. It also leaves an
`INVALID` index behind if it fails, so the migration must verify `pg_index.indisvalid` afterward. A
failed concurrent build otherwise leaves **no index and no error**.

### 3.4 Metrics contract — co-owned with `c3-api`

`c3-api` found something I had not: **three of the existing "counters" can decrease.**

| Current | Problem | Resolution (agreed) |
|---|---|---|
| `validsim_runs_total` | `# TYPE counter`, but derived from `len(store.history())` — drops on every `DELETE` and on restart | **Keep the name** (published contract). Add monotonic `validsim_runs_created_total`; demote row-count to gauge `validsim_runs_stored` |
| `validsim_approvals_total`, `validsim_blocks_total` | Same — both go **down** on delete | Same treatment; alert on the new monotonic counters |
| `validsim_http_requests_total` | Counts by **status class only** — every request collapses into 4 counters | `c3-api` owns the per-endpoint counter |
| *(missing)* | `duration_ms` is computed and logged but **never recorded as a metric** — no p95 derivable | **I own** `validsim_http_request_duration_seconds` histogram + in-flight gauge |
| *(missing)* | No queue/lease metrics; a dead worker is **invisible** — `reap_expired` return value is discarded (`worker.py:176`) | `validsim_jobs_depth`, `_queued_total`, `_lease_expired_total`, `_reaped_total`, and **`_oldest_queued_age_seconds`** (the one that pages) |

**Critical cardinality detail both `c3-api` and `c4-reliability` raised:** label `route` with the
FastAPI **route template** (`/api/v1/validations/{run_id}`), never `scope["path"]`. The middleware
reads the expanded path, and run ids are `vrun-<8 hex>` — labelling on the raw path creates
`16^8` label values per route and destroys the Prometheus instance.

**Alerting nuance from `c3-api`:** a lease expiry is **not** a failure by itself — the worker
renews at `lease/3` (`worker.py:193-195`), so a slow-but-alive worker renews rather than being
stolen. Alert `lease_expired_total` on **rate-of-change**, not absolute value.

**Deliberate trade to record:** `/metrics` is intentionally unauthenticated (`api/main.py:847-860`)
and exposes run volumes, approval/block counts and the latest composite. The alternative —
authenticating scrapes — means every Prometheus holds a ValidSim API key. Keep it unauthenticated
but **never expose it on a public interface**; bind to loopback or a private NetworkPolicy.

### 3.5 Backup SLOs — split by data kind (from `c4-reliability`, better than my single number)

| Data | RPO | Why |
|---|---|---|
| Scorecard / verdict rows | **~0–1h, event-driven** | The safety artifact. Must always be re-readable and re-gateable. |
| Episode-level detail (`episodes_json`, the bulk) | **24h** | Large, and **deterministically re-derivable** (`stable_seed`) |

**The key insight I missed: you do not need the DB dump to recover evidence.** The pipeline is
seeded and deterministic, so a lost run can be *re-derived*. Therefore: **export a scorecard-level
JSON object to storage on every terminal job** (immutable, versioned by `run_id`, trivial cost) —
that drops evidence RPO from 24h to ~0 and makes the nightly `pg_dump` about *bulk history* rather
than about not losing verdicts.

**It also de-risks the `DELETE` hole.** `docs/runbook.md:420-441` honestly lists three options for
the mutable-evidence problem without choosing. Immutable off-store scorecard exports are effectively
**option (a) — the append-only retraction ledger — at ~5% of the cost**, with no schema or
permission redesign. This should be written into the vault as a decision.

**RTO must be split too** — my single "4h" was answering the wrong question:
- **RTO-to-serve-a-gate ≈ 15 minutes.** The gate is deterministic, so a blocked customer needs a
  working API + store, not their history. **A fresh DB is a valid recovery state, not a degraded
  one.** This is the number that belongs in an SLA.
- **RTO-to-full-history-restore: 4h.** Honest number for bulk episode data.

**Drills must be two-tier** (I was going to pick one cadence; there are two):
| Tier | Cadence | Proves |
|---|---|---|
| Evidence-readability check | **Weekly** | Replays a known `run_id` through the CLI `gate` from a scratch export. Catches the failure that actually happens: a schema change making old rows unreadable. |
| Full `pg_restore` drill | **Monthly** | Full restore into a disposable DB, then assert a known run returns the same composite. Expensive, so monthly. |

Add a second gauge: `validsim_backup_last_restore_drill_success_timestamp_seconds`. **An untested
backup is a belief, not a backup** — and drill staleness catches "failing silently for three weeks,"
which is strictly more likely than the job dying visibly. Thresholds: backup `>26h`, drill `>35d`.

### 3.6 Rate limiting — High severity, and one mode is a **billing** bug (from `c4-reliability`)

`vault/06 - Business/Pricing Tiers.md:18` sells the Free tier as "**10 runs/mo**". With a
process-local limiter and 3 replicas, that deployment silently grants **30** — and it **fails open,
upward**. The tier limit is unenforced. That is commercially load-bearing, not hygiene.

**I withdraw my "429 rate under X%" SLO proposal — `c4-reliability` is right and I was wrong.**
429 is the limiter *working correctly*; setting an SLO on it creates a direct incentive to widen or
disable the limiter, and the metric that would "improve" is the one telling you the control is off.
Correct framing:
- **SLO: write-path availability *excluding* 429s.**
- **Alerts:** `validsim_ratelimit_degraded > 0` for >5m, plus **effective-limit drift**
  (`limit × observed_replicas`) so the N-replica multiplication is *visible* rather than silent.

**I also withdraw the fail-open/fail-closed binary.** It was the wrong question. Moving the limiter
to Redis makes Redis a hard write-path dependency, so: fail-open = no rate limiting at all (bad for
multi-tenant billing); fail-closed = a Redis blip 503s every write (bad for availability). **Third
option: degrade to the existing process-local limiter** — same semantics, smaller budget — and
expose `validsim_ratelimit_degraded`. Worst case is degraded fairness, not unbounded writes or total
outage.

**Deeper issue that survives both fixes:** IP keying is wrong for multi-tenant regardless of
proxies — two customers behind one corporate NAT share a bucket *today, single replica*. The durable
answer is **org-scoped keys**, and rate limiting *and* the per-org quotas in item 12 need the same
identity model (`org_id` + resolution path). **Build that identity model once, use it for both** —
otherwise we ship a Redis-shared IP limiter and still cannot enforce the pricing tiers we're selling.

### 3.7 Secrets backend: **defer the decision** (from `c5-integrations`, and I agree)

The vault genuinely does not pick one — one row, both options, no choice
(`vault/04 - Engineering/Tech Stack.md:48`). I proposed a three-tier model; that was premature.
All five secrets in play are single scalar env vars (API key, `VALIDSIM_ISAAC_WORKER_KEY`,
`VALIDSIM_WEBHOOK_SECRET`, SMTP password), which is a "mount a Secret / `envFrom`" problem **every**
backend solves identically at the Kubernetes layer. The choice only becomes load-bearing for
*dynamic rotation* and *access audit* — a Year-2 SOC 2 question, not a launch question, and
`vault/08 - Team & Legal/Compliance.md` already sequences SOC 2 Type II at Year 2.

**Revised item 6:** ship SOPS-encrypted manifests (no new infrastructure, works with the GitOps
path) and **leave the runtime backend explicitly undecided**. An architecture doc reading "AWS
Secrets Manager / HashiCorp Vault" looks like a decision to a security reviewer who then cannot
tell what you actually run. Revisit when rotation is a procurement requirement.

**Air-gap coordination:** `c5-integrations` independently confirmed **no telemetry code exists** —
matching my F-note — and warned that an air-gapped customer *will* test
`VALIDSIM_TELEMETRY=0`. So the docs must state plainly: no telemetry is collected; the flag exists to
make egress-blocking auditable and to gate webhook/LLM egress in a NetworkPolicy.

**Compose profiles:** adopted verbatim, including the `observability` name (one profile, not two
spellings). Alert rules start from the metric names that already exist rather than inventing new
ones.

### 3.8 SLO ownership split — agreed with `c4-reliability`

- **`c4-reliability` owns** reliability/error-budget SLOs: gate correctness, determinism,
  degradation behaviour (status code per dependency), DLQ/retry, lease-reap rate, breaker state.
- **`c5-platform` owns** infrastructure SLOs: availability, RPO/RTO, restore drills, replica/HA.
- **Joint — the seam where dependency failure meets HTTP status code.** Their degradation matrix
  makes my availability SLOs *meaningful*: an availability SLO that doesn't say "Postgres down
  serves 503 and never a wrong verdict" is a number without a mechanism.

`c4-reliability` also owns the **degradation matrix** and **`validsim doctor` + the shared probe
registry** — `doctor` calls the identical functions a `/ready` endpoint needs, so **do not build a
second probe path**. That reverses my item 1's design, which proposed a separate `readyz`.

### 3.9 Two documentation gaps assigned to me

1. **`VALIDSIM_JOB_LEASE_SECONDS` is documented nowhere** — confirmed: `docs/async-jobs.md` §7
   (lines 365-376) lists only `VALIDSIM_JOB_QUEUE`, `VALIDSIM_REDIS_URL`,
   `VALIDSIM_JOB_QUEUE_MAX_DEPTH`; it is also absent from `docs/runbook.md` §2 and `.env.example`.
   It is the knob that determines how long a stranded job stays stuck — exactly what an operator
   needs mid-incident. **Headroom concern:** the default is `_DEFAULT_LEASE_SECONDS = 3600`
   (`queue.py:82`) while the vault's GPU runs are 15-45 min, leaving only ~3 renewals. Raise the
   default to ~4× the longest expected job.
2. **`runbook.md` §5.1's backup procedure still reads as aspirational** once the scheduler exists —
   it should point at the service + drill and carry the RPO/RTO numbers from §3.5.

### 3.10 Escalated, not recorded: unsupportable availability numbers

`c4-reliability` and I both want this escalated rather than logged. **Any availability/SLA/uptime
number currently in the vault or a sales deck is unsupportable and is a live commercial risk** if
you sell to enterprise OEMs with SLA commitments (`vault/06 - Business/Pricing Tiers.md:21` lists
"SLA" as an Enterprise-tier feature). Neither of us will put a number in writing until it is
measured. Recommend the founders treat this as a decision, not a documentation fix.

---

## 4. CLI + observability contracts agreed with `c3-cli` (2026-09-25, round 2)

### 4.1 CLI surface decisions

| Decision | Outcome | Reason |
|---|---|---|
| `doctor` exit codes | **0 = all probes pass, 1 = ≥1 hard-fail, 2 = bad invocation only** | 2 already means *usage error* (`cli.py:179`, `:213`). Overloading it would make `doctor` indistinguishable from a typo in CI. |
| `migrate` placement | **Top-level command. No `admin` sub-typer.** | `admin` implies a privilege/role boundary that **does not exist** — this CLI has no auth — so the grouping would imply a security model that isn't implemented and mislead operators. `job_app` is a precedent for *sub-typers*, which cuts toward top-level. Also makes `validsim migrate --check` typeable in CI without a subcommand dance. |
| Required `migrate` flags | `--check` (**exit 2 if pending migrations**), `--plan` (print DDL, no exec) | Non-optional; `--check` is the CI gate, `--plan` is the review path. |
| `backup`, `cost report` | Mine, uncontested | No overlap with `c3-cli` or `c4-reliability` workstreams. |

### 4.2 `doctor` ownership and probe checklist

**c4-reliability implements `doctor` + `/api/v1/ready` off ONE probe registry.** Not a CLI-local
version — that would duplicate readiness logic and drift from the API immediately. **I do not build
a second probe path**; my item 1 `readinessProbe` calls the same registry. `c3-cli` folds the same
checks into `Settings` validation. One list, three consumers: CLI, `/ready`, K8s probe.

| # | Probe | Fails when | Severity |
|---|---|---|---|
| 1 | Durable store | `VALIDSIM_STORE=memory` while `VALIDSIM_ENV=production` | hard |
| 2 | Prod API-key policy | prod with blank `VALIDSIM_API_KEY` — mirrors the boot check at `api/main.py:537-541`, so `doctor` predicts the crash-loop *before* the pod restarts | hard |
| 2b | Key strength | short / placeholder-looking value | warn |
| 3 | Postgres reachable | connection fails | hard |
| 4 | **Schema revision matches code** | `validsim_schema_version` ≠ code revision → *"run `validsim migrate`"* | hard |
| 5 | Redis reachable | ping fails (when `VALIDSIM_JOB_QUEUE=redis`) | hard |
| 6 | Queue caps sane | `VALIDSIM_JOB_QUEUE_MAX_DEPTH` / `VALIDSIM_JOB_LEASE_SECONDS` unset/non-positive; lease ≪ longest expected job (default 3600s is too tight for 15–45min GPU runs) | warn → hard in prod |
| 7 | **Plaintext-secret scan** | see below | hard in prod |
| 8 | Log level valid | `VALIDSIM_LOG_LEVEL` unparseable (degrades to INFO) | warn |
| 9 | Store writable | configured but unwritable (e.g. read-only replica DSN) | warn |

**Probe 7 is the one item neither `c3-cli` nor `c4-reliability` had, and it is what makes SOPS/ESO
meaningful rather than decorative.** In production, a secret-bearing var set to a **literal** rather
than sourced from a file-mounted projection must hard-fail: `VALIDSIM_API_KEY`,
`POSTGRES_PASSWORD`, `VALIDSIM_LLM_API_KEY`, `VALIDSIM_SMTP_PASSWORD`,
`VALIDSIM_ISAAC_WORKER_KEY`. Also fail when a projection is **expected but absent**
(`/var/run/secrets/validsim/*` missing in prod — catches "we thought ESO was wired up"), and fail
weak/placeholder values (the `.env.example` literal `validsim-dev-password` is the realistic
accident).

Why this belongs in `doctor` and not only in docs: `SECURITY.md:305-308` lists secrets-beyond-env as
a known gap and `.env` is gitignored, so the likeliest real failure is an operator running prod with
a literal from a shell profile. `doctor` converts a silent misconfiguration into a loud failure.

### 4.3 ConfigMap constraint — document it, because nothing in the repo does

`c3-cli` and `c5-devtools` independently hit the same wall I did (F7). Agreed: **`envFrom` with flat
`VALIDSIM_*` keys is the primary K8s path** (works on 3.10, no parser question at all). The
`c3-cli` recommendation to go **TOML-only and drop the hand-rolled reader** (`config_loader.py:59-141`
— 60 lines of comment-stripping/coercion existing only to avoid promoting PyYAML to a runtime dep) is
theirs to sequence. Either way, **the repo must state that nested YAML is unsupported** — as of now
nothing does, which is how someone discovers it in production.

### 4.4 `cost report` — `StoredRun` is insufficient, and one field is a trap

`c3-cli` asked whether `StoredRun` already carries enough for cost reporting. **It does not**, and
one field that appears to is actively misleading.

`StoredRun` (`store/memory.py:26-52`) carries `run_id, checkpoint_id, task_id, created_at,
scorecard, evaluation, safety, episodes, baseline_run_id, regression` — **no duration, no
`gpu_seconds`, no cost**. A package-wide grep for `perf_counter|monotonic|time.time|elapsed|
gpu_seconds` returns only the rate limiter (`api/main.py:190`), the HTTP access log
(`api/main.py:347`) and the SSE deadline (`jobs/router.py:256`) — **nothing on the validation path**.

**The trap: `EpisodeResult.duration_s` (`sim/runner.py:61`) is simulated, not measured.** It is drawn
from the *seeded* RNG at `runner.py:154`: success `rng.uniform(4.0, 12.0)`, failure
`rng.uniform(6.0, 20.0)`, timeout `rng.uniform(18.0, 35.0)`. It describes the fictional episode
inside the simulated world.

If `cost report` summed it we would get a plausible, **deterministic, entirely fictional** COGS
number — and determinism is precisely what would stop anyone noticing it was wrong. A more dangerous
failure than a crash.

**Revised item 12:**
- Add `wall_clock_s` + `gpu_seconds` to `StoredRun` **and** a migration column (`c3-data` owns the
  store interface). Record via `time.perf_counter()` around `run_and_score`; for Isaac, sum
  worker-reported GPU time.
- **Forbid `episodes[].duration_s` as a cost input** in the docstring *and* in a test asserting the
  cost report never reads it. A guard comment beside the field is warranted, because it looks like
  exactly the right field.
- For the mock backend, report **wall-clock measured / GPU cost unknown**, labelled as such. Today
  GPU COGS genuinely is near-zero (the vault says so), so the first honest output is *"wall-clock
  per run, GPU cost unmeasured"* — not an invented `$X/run`.

**Determinism conflict — flagged to `c4-reliability`.** A `wall_clock_s` field is inherently
**non-deterministic**, and the design leans hard on determinism (`stable_seed`; the
`reap_expired` docstring at `queue.py:557-562` explicitly relies on "the pipeline is deterministic
and the persisted result upsert is idempotent"). **Keep timing out of `Scorecard`** — the
determinism-critical, hash-compared artifact — and put it in a separate non-hashed column or side
table, so re-running the same seed still yields an identical scorecard.

**Net effect on the business claim:** the vault's "$5–7 per 5K run" is not merely unmeasured, it is
**not yet measurable from stored data**. That is a sharper statement than my original, and it makes
the instrumentation a prerequisite for the margin model rather than a nice-to-have.

---

## 5. Round-3 agreements: version-check semantics, metric names, doc defect fixed

### 5.1 Schema version check — **asymmetric by design, on first query** (with `c4-reliability`)

`c4-reliability` implements the DDL removal; I own the Alembic side. Agreed semantics:

| Installed vs required | Behaviour | Why |
|---|---|---|
| `installed < REQUIRED` | **Raise a clear startup error** — *"run `validsim migrate`"* | The store is unusable; must not serve broken reads |
| `installed == REQUIRED` | Serve normally | — |
| `installed > REQUIRED` | **Serve normally (tolerate too-new)** | **This is what keeps `runbook.md` §4.6.3's documented rollback working.** Rolling back to an older revision against a newer schema must not hard-fail. |

The asymmetry is deliberate and `c3-cli` independently reached it. It must be documented explicitly
in the error text and the ADR, because "tolerate a newer schema" will otherwise look like a bug to a
future reviewer and get "fixed" into a hard failure.

**Timing: first query, not startup — and explicitly *not* a second `create_app` path.** Preserves
`create_store()`'s side-effect-free construction (ADR 0004 + the fake-connection test seam) and the
lazy `psycopg` import. An un-migrated API therefore serves *errors* (loud, traced) while staying
*out of rotation* (probe #4 fails). That split is precisely why `doctor` must exist.

### 5.2 SQLite must land in the same PR — correcting `c4-reliability`'s "lower-stakes" ordering

`sqlite.py:170-179` shows `__init__` opens a connection then runs `executescript(_SCHEMA)` (a
`CREATE TABLE` + two `CREATE INDEX`) and `_migrate()` (a `PRAGMA table_info` + N `ALTER TABLE ADD
COLUMN`) **unconditionally, on every construction**, then commits. Three consequences:

1. **It already violates side-effect-free construction on SQLite today** — Postgres defers to first
   query, SQLite does not. So this is a pre-existing inconsistency running *opposite* to the one we
   spent last round fixing.
2. **Real lock contention, not theoretical** — `ALTER TABLE` takes a write lock, so two processes
   opening the same DB file serialise on every construction (exactly what the
   `VALIDSIM_STORE=sqlite` CI Action path and any second worker would do).
3. **It is the same "migration without a ledger" defect** we are deleting from Postgres — deferring
   it means shipping the pattern we just agreed to remove.

Fix in the same PR using `PRAGMA user_version` (a built-in integer header, no ledger table needed).

### 5.3 `migrate down` guard (from `c4-reliability`, adopted)

`down` drops columns and **permanently loses episode detail**. It must refuse unless `--force` **and**
a verified backup exists (reuse the item 5 manifest + SHA-256 — do not invent a second
backup-verification path), and the README/runbook must state plainly that **`down` is not a rollback
mechanism**: the documented rollback is redeploying the previous image (§4.6.3). Otherwise someone
discovers `down` mid-incident and destroys the evidence they needed.

### 5.4 Org identity — stopping myself over-claiming (correction from `c4-reliability`)

There is exactly **one** `VALIDSIM_API_KEY` per deployment (`api/main.py:535`), so hashing it yields
**one bucket for all tenants**. That is a strictly better *shared* bucket, but it is **not tenancy**.
Recast honestly: build the **resolution path** (`header → org record`) once, with a single-key fast
path, so multiple keys bound to an org is additive later. Rate limiting and quotas both consume that
one path. Do not describe this as "per-org keying" in the vault.

### 5.5 The scorecard export is a **stronger** guarantee, not a cheaper one

`runbook.md:420-441` options (a) and (b) both depend on **database-enforced write permissions** — so a
future engineer with migration privileges can bypass them. The export's ledger lives in versioned
object storage, giving the same evidentiary property ("a verdict once recorded cannot be
un-recorded") that is **immutable by construction rather than by policy**, and cannot be un-recorded
by anyone holding DB access. Lead the recommendation with the guarantee, not the 5% cost figure.

### 5.6 Metric names locked, and an alertability split

Locked (no second spelling; `c3-api` owns the counters and may rename, we match):
`validsim_runs_created_total`, `validsim_backup_last_success_timestamp_seconds`,
`validsim_backup_last_restore_drill_success_timestamp_seconds`.

| Metric | Type | Alertable? |
|---|---|---|
| `validsim_http_requests_total` | true monotonic in-process counter | **yes** |
| `validsim_runs_created_total` (new) | monotonic, once per `store.save` | **yes** |
| `validsim_composite_score` | real gauge (legitimately rises/falls) | **yes**, `< threshold` sustained |
| `validsim_runs_total` / `_approvals_total` / `_blocks_total` | **row-count-derived, mislabeled `# TYPE counter`** | **display-only — never `rate()`/`increase()`** |

**Scope recommendation to `c3-api`:** ship `validsim_approvals_created_total` and
`validsim_blocks_created_total` in the same change. "Page me when validations start blocking" is the
primary product signal and currently has **no valid counter to hang on** — without it the dashboard
still cannot alert on blocks, which defeats the fix.

**Sharpest framing of the bug** (from `c5-integrations`): because `blocks = total - approvals`
(`metrics.py:117-118`), the pair is **one derived value counted twice**, so a customer writing
`approvals + blocks == runs` as a data-loss reconciliation gets a tautology that always balances.

### 5.7 Doc defect found by `c5-integrations` and **fixed on the runbook side**

The runbook asserted the rate limiter **fails open**; the code deliberately **fails closed**
(`api/main.py:217-220`, `:227-228`, `DEFAULT_RATE_LIMIT = 60` at `:110`). Two runbook locations
repeated the wrong claim plus a phantom `30` value (`.env.example:82` ships `60`):
`runbook.md:75` (env table) and `runbook.md:121-127` (standalone callout). **Both corrected**, and the
callout gained the N-replica / trusted-proxy caveat.

This is the same *class* of problem as the air-gap telemetry concern: **an operator reading the
runbook would believe a typo leaves a safety-adjacent control disabled when the code fails safe.**
`docs/api-reference.md` contradicts itself on the same point (`:145`, `:758`, `:828` say opt-in /
disabled-by-default while `:764` in the same file is correct) — that is `c3-api`'s file and their
fix; I did not touch it.

---

## 6. Round-4 corrections: probe #7 blocking bug, cost model, and two verified findings

### 6.1 Probe #7 as I specced it **hard-fails on a fresh `git clone`** — blocking, `c4-reliability` correct

My probe #7 said plaintext-secret vars hard-fail in production, and I named `.env.example`'s
`validsim-dev-password` as "the realistic accident." **But `.env.example` is a tracked file
containing every one of those names with literal values, by design** — verified:
`.env.example:26, 41, 70, 113, 126, 145`. A fresh clone would fail `validsim doctor`, which for a
tool whose purpose is winning a 15-second eval is the worst possible first-run experience.

**Corrected probe #7:**

| Condition | Severity | Rationale |
|---|---|---|
| Secret var set to a **literal** (not from a projection) | **WARN** | Loud, not fatal — one misconfigured secret must not cascade into "everything is broken" |
| **No source configured at all** (projection expected in prod but absent) | **HARD in prod** | Genuinely means no secret manager; loudness cannot fix it |
| Weak / placeholder-looking value | WARN | — |

Plus three implementation requirements:
1. **Never scan** `.env.example`, `*.example`, `tests/fixtures/**`, `docker-compose.yml`,
   `.github/workflows/**` — these legitimately hold literals.
2. **Parse DSN userinfo.** `.env.example:41` embeds the password *inside* `VALIDSIM_PG_URL` as
   `postgresql://validsim:<pw>@localhost`, so a `KEY=value` scan finds nothing. The scan must extract
   and test any `postgres://user:pass@host` password against the known-secret set.
3. **Empty is not a finding** (`VALIDSIM_API_KEY=` is a placeholder), and **print file + line, never
   the value** — echoing a discovered secret into CI logs is how a warning becomes a real leak.

**The distinction that makes this coherent:** *"a literal is set"* (warn) vs *"no source is
configured"* (hard).

### 6.2 Cost model: a column is **not enough** — per-attempt ledger required

`c4-reliability` found that `wall_clock_s`/`gpu_seconds` on `StoredRun` **silently under-counts**,
because the three stores have divergent save semantics — verified:

| Backend | Save | Retry outcome |
|---|---|---|
| Postgres | `ON CONFLICT (run_id) DO NOTHING` (`postgres.py:407-429`) | **first write wins** — spend frozen at attempt #1 |
| memory (`memory.py:87`) / SQLite (`INSERT OR REPLACE`, `sqlite.py:203`) | overwrite | **last write wins** — attempt #N only |

**Neither accumulates across attempts.** A job that crashed three times burned three times the GPU
and the store records one. For metered compute that under-counts precisely when it matters.

**Decision: per-attempt, keyed `(run_id, attempt)`.** Rationale: per-job can only be an *estimate*,
and an estimate is what created the original unmeasured-COGS problem. A metered GPU must be metered
per attempt. `JobRecord` has `lease_epoch` (`jobs/models.py:99`) but **no `attempts` counter**, so the
attempt number must come from the queue — flagged to `c3-data` (store interface) and `c5-integrations`
(`JobRecord`). **This must be settled before the migration lands, because the two shapes want
different tables.**

### 6.3 Determinism: `wall_clock_s` must not enter the hashed serialisation

`c4-reliability` notes the digest spec already strips `run_id`/`created_at`; `wall_clock_s` joins that
strip list. Verified: `Scorecard.to_dict()` (`scorecard.py:113`) / `to_json()` (`:120`) is the
serialisation that gets persisted (`postgres.py:423`) and hashed. **Add `wall_clock_s` to the strip
list — do not add it to the hashed `to_dict()` unless stripped first.** Measured cost rides
alongside the scorecard, never inside the determinism-critical artifact.

### 6.4 Verified: SQLite already overwrites where Postgres refuses to

`sqlite.py:203` is `INSERT OR REPLACE` and `memory.py:87` is a plain dict assignment — both
overwrite, while Postgres is first-write-wins. This is a **backend parity divergence with a
correctness consequence**, not just a save-semantics detail: on a re-run of the same `run_id`,
Postgres preserves the original verdict and the other two silently replace it. It compounds F4 (run-id
collision) — a collision on Postgres is a no-op; on SQLite/memory it is a **silent overwrite of an
existing verdict**. Filed to `c3-data`.

### 6.5 Verified: `robustness` is a **constant 100.0** under the mock backend — a real scoring caveat

`c5-integrations` flagged that `robustness` must not be presented as measured. Verified and stronger
than they stated: `scorecard.py:59-64` groups episodes by `randomization_level` and **early-returns
`100.0` when fewer than two groups exist** ("no cross-condition variance is observed"). Under the mock
backend every nominal episode uses `task.randomization` (a single level, `runner.py:175`), so with the
default config there is exactly one group and `robustness` is **always 100.0**.

`robustness` is weighted **20%** of the composite (`scorecard.py:34`), so **20 points of every
composite score are currently a constant.** That is a material caveat for any exported scorecard,
model card, or compliance artifact — and it must carry the **mock-backend marker** in every export.
`c5-integrations` owns this via their item 4 (model card); recorded here because it also constrains
item 5's export payload and item 12's `cost report` output.

### 6.6 Export immutability needs a **content-addressed precondition**, or the guarantee is void

`c5-integrations` correctly noted that "immutable by construction" only holds if the export is
append-only and content-addressed — otherwise "immutable" quietly becomes "immutable until the next
write." Requirement: **`PUT` with `If-None-Match: *`**, or a key that includes the content hash. A
one-line implementation constraint that decides whether the guarantee is real; far cheaper to specify
now than to retrofit after a compliance auditor asks. This lands on their item 4, and the same
artifact set should carry scorecard + episodes + run config, satisfying the model-card,
experiment-tracker and compliance-ledger use cases from **one** export.

### 6.7 Two clarifications that change alert design (from `c5-integrations`)

`validsim_composite_score` is a gauge over **the most recent run only** (`metrics.py:115`,
`runs[-1]`, `0.0` when empty). Two consequences for the alert rule:
- *"`< threshold` sustained N min"* means **N min of an unchanged gauge**, not N min of continuously
  failing runs — a run every 10 min takes 30 min to alert at N=3. For a deploy gate that is arguably
  the correct conservative behaviour, but it must be a **documented property of the rule**, not a 3am
  surprise.
- The gauge reads **`0.0` on an empty store**, so a naive `< threshold` rule **fires on a fresh
  deploy**. It needs a `validsim_runs_created_total > 0` guard.

### 6.8 Proxy config and per-tenant keying are **one change, not two**

`c5-integrations` is right that the two facts compose and the ordering is what operators get wrong.
Behind a proxy without `--proxy-headers`/`--forwarded-allow-ips`, every request presents the ingress
IP; across N replicas each enforces its own budget. **N replicas behind a shared ingress is N× the
intended budget, attributed to a single key.** Critically: **without the trusted-proxy config, no
application-level keying can recover per-tenant attribution, because the IP was never the client's.**
Set the proxy config *and* the keying together; documented as one change in the runbook caveat.

---

## 7. Round-5 decisions: measured cost trap, ledger names, exit-code ruling

### 7.1 The `duration_s` trap is **measured, not theoretical** — and it validates the vault's assumption at 8× the price

`c3-cli` measured this; **I independently reproduced it against the real simulation code** rather than
taking the number on trust:

```
seed 42  n=5000  sum(duration_s)=50272.5s = 13.96 'GPU-hr' = $48.88/run
seed 43  n=5000  sum(duration_s)=50274.1s = 13.97 'GPU-hr' = $48.88/run
```

Priced at the vault's own `$3.50/A100-hr` (`vault/06 - Business/Unit Economics.md:43`), against the
vault's claimed **$5–7 per 5K run**: **~8× too high.**

**The number is near-identical across seeds** — which is the whole danger. It is a *seeded* draw
(`sim/runner.py:110`, `:131-136`), so it is perfectly reproducible, arrives with units and a currency
symbol, and lands in exactly the shape a finance reader expects. Nobody sanity-checks a `$48.88`.
And it is high enough to *appear* to validate the margin thesis while being fiction.

**Therefore the guard is not "don't read `duration_s`." It is: a cost figure whose only available
source is a seeded RNG must never reach a finance surface at all.** The test asserts the **absence**
of the read (a future refactor cannot quietly reintroduce it), not merely a docstring.

**Verified: `StoredRun` carries exactly its 10 fields** (`memory.py:43-52`) — no `wall_clock_s`, no
`gpu_seconds`, no duration. Episodes *are* already stored, so the only genuinely missing inputs are
wall-clock and GPU seconds. `sqlite.py:59-61`'s `_MIGRATION_COLUMNS` mechanism makes the columns
additive for existing DBs.

### 7.1a There is a **second** cost field, and it is the more dangerous of the two

`c3-cli` flagged `EvaluationResult.mean_duration_s` and is right that it is worse than
`EpisodeResult.duration_s`. Verified at `engine/evaluation.py:83`:
`mean_duration_s=statistics.fmean(e.duration_s for e in episodes)` — a mean of the same seeded
fiction, **already persisted inside every scorecard's `evaluation_json`** (`evaluation.py:43`,
written to Postgres at `postgres.py:425`).

Measured and projected (seed held constant across both methods — see the correction below):
```
seed   | raw_sum_s | raw 5k cost | mean_s | aggregate 5k cost | delta
    7  |  50226.0 |     $48.83    | 10.045 |        $48.83    | 0.00%
   42  |  50272.5 |     $48.88    | 10.054 |        $48.88    | 0.00%
  123  |  50262.3 |     $48.87    | 10.052 |        $48.87    | 0.00%
```

| | `EpisodeResult.duration_s` | `EvaluationResult.mean_duration_s` |
|---|---|---|
| Location | per-episode, inside `episodes_json` | **inside `evaluation_json` — persisted in every scorecard** |
| Looks like | a raw field | **an authoritative-looking aggregate with units** |
| Trap risk | moderate | **high — it reads as a summary statistic** |

**The two are not merely "close" — they are algebraically identical, and the seed caveat `c3-cli`
raised is what surfaced it.** Summing `duration_s` over N episodes and multiplying the mean by N are
the same operation, so the "1.33% agreement" we measured earlier was **pure sampling noise from using
two different seeds**, nothing more. Held at a constant seed the delta is **exactly 0.00%** at every
seed tested. So the finding is *stronger* than we stated: an engineer who bans `duration_s` and
reaches for "the official" mean gets **not a similar answer, but the identical number** — which makes
the aggregate the more insidious trap precisely because it looks like the safe, authoritative choice.

**Therefore the guard test must assert the absence of BOTH fields** (and the docstrings on both say
*"simulated episode time, not wall-clock; never a cost input"*), because a comment on one field does
not protect the other, and the aggregate is what people reach for *because* it looks authoritative.

**Verified persistence end-to-end:** `evaluation.py:43` in `to_dict()` → `postgres.py:99-105`
(`evaluation_json JSONB`) → written at `:407-414` → read at `:110-117`. I also round-tripped a
300-episode run through a real `SqliteValidationStore`: `9.895` in memory → `9.895` after save/load,
**bit-equal**. So the field survives the store round trip exactly, and any consumer reading a stored
scorecard has it available and looks authoritative.

### 7.2 Ledger table name — **locked, both teammates blocked on this**

```
validsim_run_cost
    run_id        TEXT NOT NULL
    attempt       INTEGER NOT NULL
    wall_clock_s  DOUBLE PRECISION NOT NULL
    gpu_seconds   DOUBLE PRECISION          -- NULL = unmeasured; 0.0 = genuinely zero
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    recorded_by   TEXT NOT NULL DEFAULT current_user
    PRIMARY KEY (run_id, attempt)
```

| Decision | Rationale |
|---|---|
| `validsim_run_cost` (not `validsim_cost_ledger`) | Names the **subject**, not the mechanism. A ledger is an implementation detail; the table is the cost of a run. Matches the `validsim_`-prefixed convention. |
| `PRIMARY KEY (run_id, attempt)` | Per-attempt is settled — see §6.2. |
| **`gpu_seconds` NULLABLE, and `None` ≠ `0.0`** | `None` = *no GPU was used*; `0.0` = *GPU used for zero seconds*. Without the distinction a mock run renders a real-looking `$0.00` and an operator reads it as "the GPU was free." `None` forces *unmeasured*. |
| `wall_clock_s` **not nullable** | Wall-clock is always measurable, even for mock. |
| **`wall_clock_s` joins the digest strip list** | Non-deterministic; must never perturb the hash-compared `Scorecard` (`scorecard.py:113`). |
| **Append-only on every backend** | The first table that does **not** inherit `save_mode` semantics. A declared exception to contract-suite uniformity, encoded in the docstring so the next person doesn't "fix" it. |
| Retention ≥ reporting period | A 90-day report against a 30-day ledger is unreproducible — for billing data, worse than useless. |

**Why per-attempt is structurally forced** (verified): the worker passes `run_id=spec.run_id` on
**every** attempt (`jobs/worker.py:281-289`), and the store is keyed on `run_id`. So all attempts
target the *same row* — there is nowhere to put attempt #2. A job that crashed three times burned
three times the GPU and every backend records one. Postgres freezes at attempt #1 (`DO NOTHING`);
memory/SQLite overwrite with attempt #N. **Both under-bill.**

**Interim honest answer for `cost report`:** ship `wall_clock_s` populated and `gpu_seconds` marked
**unmeasured** (`None`). Never ship a number derived from `duration_s`.

### 7.3 `migrate --check` exit semantics — pinned, and they differ from `doctor`'s

`c3-cli` and `c4-reliability` converged on the store semantics; pinning the CLI contract so the Typer
surface isn't written against a guess:

| Condition | `migrate --check` | `doctor` | `gate` |
|---|---|---|---|
| DB older than code requires | **2** (pending migrations — a preflight question) | 1 (hard-fail probe) | **n/a — `gate` has no schema-version check** |
| DB newer than code | **0** + warning | 0 | n/a |
| Matched | 0 | 0 | 0/1 per stored verdict |
| *(unrelated)* misuse / no durable store / unknown run | n/a | n/a | **2** |

**Correction (from `c3-cli`, verified):** my earlier table listed `gate` → 2 for a schema mismatch.
**That is wrong — `gate` has no schema-version concept at all.** Per `docs/github-actions.md:196`, `2`
means *misuse*: no durable store, unknown/malformed run id, no stored run, or both flags given. A
too-old schema surfaces as whatever the store raises on open, not a clean 2. `gate`'s contract is
deliberately narrow — read the stored verdict, don't recompute — and **preflighting the schema there
should be resisted**, because it would widen a command whose whole value is that it does one thing.
The table is now restricted to what each command actually does.

`--check` answers *"is it safe to deploy this code?"* (so 2); `doctor` answers *"is this environment
sound?"* (so 1). **Store and CLI agree on the underlying asymmetry** — fail too-old, tolerate
too-new — and that is what keeps `runbook.md` §4.6.3's rollback working.

### 7.4 `INCONCLUSIVE` (exit 3) would be a breaking change — needs an explicit ruling

`c4-reliability` raised this and is right to flag it. `gate` currently exits **0/1/2**, and the
documented contract lives in exactly **two** places (`c3-cli` re-verified — my "three" was wrong):

- `actions/validate/action.yml:175-178` — the 0/1/2 contract in a comment, in the step customers run
- `docs/github-actions.md:190-192`, table authoritative at `:194-196`

**But the third dependency is worse than a doc, and it is the real argument for deferring.** The
`Makefile` contains **no exit-code documentation at all** — the only `gate` reference is the recipe
itself (`Makefile:49`), which chains with `&&`:
```
$(PYTHON) -m validsim.cli run … && $(PYTHON) -m validsim.cli gate --latest && $(PYTHON) -m validsim.cli report --latest
```
So **`gate` returning 1 and `gate` returning 2 break that chain identically.** The Makefile cannot
distinguish "the checkpoint legitimately failed the gate" from "you forgot to set `VALIDSIM_STORE`."
Adding exit 3 would collapse yet another distinct meaning into the same undifferentiated failure — so
the problem is not only the two doc updates, it is that **there is no way for a chained consumer to
tell the codes apart at all.**

Any consumer treating non-zero as failure would start failing on a 3. The right default is **yes**
("system-side failure should fail a deploy gate"), but it must be *written down*, not inferred.
**Recommend deferring to the v2 versioning policy** rather than smuggling it into a patch.

---

## 8. Round-6 findings: retry policy is inverted, and robustness breaks the digest

### 8.1 The job retry policy is **inverted** — unbounded retries for crashes, zero for errors

`c5-integrations` traced this; **I verified every step.** It is a live availability defect, not a
cost-ledger prerequisite.

`jobs/worker.py:137-148`:
```python
try:
    run = self._execute(claimed.spec)
except Exception as exc:  # noqa: BLE001 - one bad job must not kill the worker
    outcome = (JobStatus.FAILED, str(exc))
```
**Any exception is terminal on the first attempt.** There is no retry budget in the worker and no
`attempts` field on `JobRecord` (`jobs/models.py:91-99` has `lease_epoch` only).

Meanwhile the queue has machinery for the *opposite* behaviour: `reap_expired()`
(`queue.py:554-579`) returns a job to `queued`, and `claim_next` (`queue.py:501-521`) increments
`lease_epoch` on every claim. So:

| Failure mode | Behaviour today | Correct? |
|---|---|---|
| Worker **dies** mid-job (OOM, SIGKILL) | lease expires → reaped → `queued` → re-claimed → **retried forever**, unobservable | Wrong — unbounded |
| Worker **raises** (bad checkpoint path, Isaac worker unreachable, transient GPU OOM) | **terminal `failed` on attempt 1** — no retry, not even once | Wrong — zero retries |

**This is backwards in the more damaging direction: the transient failure (GPU worker briefly
unreachable) gets no retry, while the deterministic failure loops indefinitely.** For a validation
product gating physical deployments, a transient Isaac-worker blip failing a customer's run with no
retry is the more expensive of the two.

**Fix has two halves and they are not the same half.** `attempts` on `JobRecord` is *necessary but
not sufficient* — a counter with no bound does nothing. What's needed is a `max_attempts`
(env-configurable, like `VALIDSIM_JOB_LEASE_SECONDS`) **enforced at claim time**, with a terminal
state after exhaustion that is distinguishable from a first-attempt failure.

**The vault already anticipated this and left it unimplemented:** `vault/04 - Engineering/Async Job
Queue.md:15` lists the **dead-letter queue** as target-state. So there is a designed owner for the
concept and no implementation — and today an unobservable infinite retry loop is what you get
instead.

**Verified: the reaper cannot spin hot.** `run_forever` (`worker.py:150-178`) calls `reap_expired()`
once per iteration, and the default lease is 3600s (`queue.py:82`), so a crash-loop is bounded to
roughly **one requeue per hour per job**. `c5-integrations` asked this to be checked rather than
assumed — correct instinct, and the answer is "no hot loop, but worse in a different way": a job can
sit in `queued`/`running` for **hours with nobody aware**, which argues for `attempts` being visible
**on the job record** (queryable via the API) rather than only in a log.

**Ownership:** `c4-reliability` reports `c6-integration` is already landing
`JobStatus.DEAD_LETTER` **and** `JobRecord.attempts` in one change. Covered — do **not** open a
second workstream with `c5-integrations`, or two owners build the same field. Priority: **P1, ahead
of the cost ledger**, because the ledger is a reporting feature and this is an availability defect.
It unblocks three things (bounded retry, DLQ, cost key), not one.

### 8.2 `robustness` being constant **breaks the determinism digest** — not just presentation

`c4-reliability` escalated this above where I filed it, and they're right. Verified:
`scorecard.py:63-64` early-returns `100.0` when `len(rates) < 2`, and `runner.py:175`/`:182` pass
`randomization_level=task.randomization` for every episode.

**The digest consequence is the serious part:** a constant contributes nothing to distinguishing
runs, so **`content_digest` cannot detect a robustness regression** — two runs with genuinely
different cross-condition robustness produce **identical digests** under the default config. My
presentation-only framing missed that the feature is functionally blind in that dimension.

**Root cause is upstream, and it makes the metric unreachable-as-configured:**
`TaskConfig.randomization` is a single `Literal["none","partial","full"]` (`config.py:17`), and every
nominal episode receives that one level — so the mock backend **structurally cannot** produce more
than one group.

Two options, and this is a **founders' product call, not engineering**:

| Option | Effect | Risk |
|---|---|---|
| **(a) Sweep levels per run** (split episodes across none/partial/full) | Makes the metric real | **Changes the seed→episode mapping, therefore every scorecard.** A breaking change to all stored evidence; must not ship without re-baselining |
| **(b) Ship a marker, document as unmeasurable, defer the sweep** | Honest, zero-risk, gap stated not hidden | None |

**Recommendation: (b) now, plus the marker as its own P0.** The marker must live **in the scorecard
itself**, not just the export — the scorecard is the artifact customers read *and* the digest hashes.
Without `robustness_measured: bool` (derived from `len(by_group) >= 2`) in the hashed artifact, the
blindness is permanent. A consumer must be able to distinguish "100.0 because it was measured" from
"100.0 because there was nothing to measure."

### 8.3 Save-semantics divergence is a **safety** issue — and the fix direction matters

`c4-reliability` strengthened my §6.4 framing correctly. The reachability point is decisive:
`cli.py:73` (`_DURABLE_STORE_BACKENDS = frozenset({"sqlite", "postgres"})`) means **`gate` runs on
SQLite** — and `actions/validate/action.yml:100-101` configures `VALIDSIM_STORE=sqlite` by default
for most users. So **the append-only guarantee holds on Postgres and fails on the backend the
validate action actually uses.** A run-id collision there is a silent verdict overwrite on the exact
path a customer's deploy gate depends on.

**Fix direction matters and must be stated: make SQLite/memory refuse-or-noop like Postgres — do
NOT unify by making Postgres overwrite.** Postgres's behaviour is the one `runbook.md:420-441`
documents as the append-only guarantee; changing it would invalidate that contract. Filed as a
**correctness finding, not a consistency one.**

### 8.4 Cost reporting must not normalise by composite

`c4-reliability` caught an interaction with §6.5: because 20 points of every composite are a
constant, **any "cost per composite point" metric is currently dividing by a partly-fictional
number.** `cost report` must not present cost-per-robustness-point or normalise by composite.
Recorded in the item 12 spec.

### 8.5 `gate --json` exists but collapses the very distinction `smoke` needs

`c3-cli` corrected my shared-fix suggestion: the capability is **already half-built**. `gate --json`
exists (`cli.py:419-421`, payload at `:451-461`) and emits
`{run_id, composite_score, threshold, decision}`. So the fix isn't "add machine-readable output" —
it's that the existing output loses the information:

1. **`decision` collapses two distinct failures.** `cli.py:450` computes `verdict = _APPROVED if
   approved else _BLOCKED`, so it reports `BLOCK` for *both* "composite below threshold" *and* "no
   recognised verdict stored" — which `docs/github-actions.md:195` documents as two different
   meanings.
2. **A usage error exits 2 before any JSON is printed.** `_require_stored` / `_require_durable_store`
   raise earlier in the function, so a consumer parsing stdout gets **nothing exactly when it most
   needs the reason**.

**Consequence:** `smoke`'s `&&` chain cannot branch on 1-vs-2 from stdout alone, which is the same
defect as the `make smoke` P0 — one root, two symptoms.

**The fix is additive and cheap** (both in `c3-cli`'s lane):
- add a **`reason`** field to the `gate --json` payload (`threshold_not_met` / `no_recognised_verdict` /
  `not_durable_store` / `unknown_run`);
- **emit the payload on the failure paths too**, not just the success path.

Exit code stays the contract; JSON becomes the *explanation*. That gives `smoke` what it needs
(`gate --json || inspect reason`) without touching 0/1/2, and it is the same change that would make a
future exit 3 diagnosable rather than opaque. Not building it unprompted — it is adjacent to
`c3-cli`'s existing `--json` work.

---

## 9. Summary — effort, timeline, blockers

| # | Item | Artifact | Effort | Timeline | Blocker? | Depends on |
|---|---|---|---|---|---|---|
| 1 | K8s manifests | `deploy/k8s/base` + overlays | 24h | 1.5 wk | **YES (hard)** | 3, 6, 11 |
| 2 | GPU orchestration | `deploy/k8s/gpu` + Argo | 40h | 3 wk | **YES** (mock: no) | 1, GPU host |
| 3 | Migrations | `migrations/`, `validsim migrate` | 28h | 1 wk | **YES (hard)** | — (first) |
| 4 | IaC | `deploy/terraform/` | 48h | 3 wk | **YES** | 1, 3 |
| 5 | Backup automation | `ops/backup.py`, `backup.yml` | 24h | 1 wk | **YES (hard)** | 3, §3.5 |
| 6 | Secrets (SOPS only, backend deferred) | `.sops.yaml`, `deploy/secrets/` | 20h | 1 wk | YES | — |
| 6b | Org identity model (`org_id` + resolution) | store + auth | 16h | 1 wk | YES (billing) | 3 |
| 7 | Air-gapped | `dist/offline/`, `air-gap.md` | 24h | 1.5 wk | **YES** (on-prem) | 1, 6, F9 |
| 8 | HA / multi-region | `HA.md`, pool, shared limiter | 40h | 3 wk | **YES** (Tier-4) | 3, 5 |
| 9 | Observability | `deploy/observability/` | 20h | 1 wk | MEDIUM | F3, 3-api |
| 10 | Compose profiles | `docker-compose.yml` | 12h | 3 d | no | — |
| 11 | Release engineering | `release.yml` | 24h | 1.5 wk | **YES (adoption)** | F9 |
| 12 | Cost controls | quota tables, `cost report` | 32h | 2 wk | YES (business) | 6b, 4.4, 13 |
| 13 | **Bounded retry + DLQ** (inverted policy, §8.1) | `max_attempts`, `JobRecord.attempts`, DLQ | 20h | 1 wk | **YES (availability)** | c6-integration |
| 14 | **`robustness_measured` marker** (§8.2) | scorecard field + digest strip | 6h | 2 d | **YES (integrity)** | founders (§8.2) |

**Total ≈ 378h ≈ 9.5 person-weeks** (items 13–14 added in round 6).

**Corrected build order** (`c4-reliability` identified that doing connection pooling before moving
DDL out of the query path leaves the worst problem in place):
```
Week 1        3 (migrate)  →  14 (robustness marker, 6h)  →  10 (profiles)
Weeks 1–2     6b (org identity)  →  5 (backups)  →  13 (bounded retry/DLQ)
Weeks 3–4     1 (K8s)  →  9 (observability)  →  11 (release)  →  12 (cost)
Weeks 5–8     4 (IaC)  →  2 (GPU)  →  8 (HA)  →  7 (air-gap)
```
Best value per hour: **14** (6h, integrity) → **10** (12h) → **3** (28h, unblocks 1/4/5/8/12) → **9** (20h).

**Two items are newly promoted above the original plan** because review made them look different in
kind, not just in size: **13** (the retry policy is *inverted* — an availability defect, and P1 ahead
of the cost ledger it blocks) and **14** (a constant field in a hashed artifact is a correctness
defect, not a documentation gap).

---

## 10. Open questions / cross-team dependencies

All primary questions are now **answered** (§3, §4). Remaining minor items:

| Owner | Question | Default |
|---|---|---|
| `c3-data` | Add `wall_clock_s` + `gpu_seconds` to `StoredRun`? (needed by `cost report`) | Yes — additive, and keep timing **out** of `Scorecard` (§4.4) |
| `c3-data` | Where does `validsim_schema_version` live — `schema_version` or their name? | their name (they raised it first); §3 already updated |
| `c3-api` | Confirm histogram label names for v1 | as proposed; rename rides the v2 policy |
| `c3-cli` / `c5-devtools` | TOML-only config loader (drop hand-rolled YAML reader)? | Theirs to sequence; I use `envFrom` meanwhile |
| `c5-devtools` | Compose quickstart in runbook vs README? | runbook (I'm writing it); **after** cosign signing |
| founders | Scorecard-export as the `DELETE`-hole resolution? | Recommend option (a)-equivalent; §3.5 |
| founders | Any availability/SLA number in vault/sales material? | **Withdraw until measured** (§3.10) |

**Assumptions I am flagging rather than hiding:**
1. "MakeItSimple" is read as a **simple Make wrapper**, not a named product.
2. **No telemetry exists today** — `VALIDSIM_TELEMETRY=0` is an auditable egress contract (§3.7).
3. GPU throughput figures are **planning assumptions** — and per §4.4 **not yet measurable from
   stored data**, which is a sharper caveat than my original.
4. Isaac Sim images cannot be redistributed (NVIDIA EULA) — hence the BYO-GPU model the vault
   already proposes (`vault/06 - Business/Unit Economics.md:44`).

---

## 11. What I did *not* do, and why

- **No YAML landed in the repo.** The brief asked for a proposal "with a real YAML sketch"; the
  manifests stay in this document so the layout is reviewable as a design first.
- **I modified no application source.** Items 3, 5, 6b, 8 and 12 change Python owned by others —
  proposed and messaged instead (§3.1 file ownership, §4.2 probe registry).
- **I withdrew two of my own recommendations** after review: the "429 rate" SLO and the
  fail-open/fail-closed limiter binary (§3.6). Both were wrong framings.
- **I did not resolve the secrets backend** — deferred by agreement (§3.7).
- **I did not build a second probe path** — `doctor`/`/ready` come from `c4-reliability`'s registry
  (§4.2), so my K8s `readinessProbe` calls theirs.

---

## 12. Appendix — evidence index

| Claim | Evidence |
|---|---|
| No K8s manifests / Terraform / deploy controller | `docs/runbook.md:345-346` |
| No backup scheduler or retention automation | `docs/runbook.md:350-356` |
| Rate limiter process-local + IP-keyed | `validsim/api/main.py:147-148, 265-277` |
| Runtime DDL from every process at first query | `validsim/store/postgres.py:370-395`; `store/sqlite.py:36-55, 190` |
| Missing Postgres composite index (SQLite has it) | `store/postgres.py:91-92` vs `store/sqlite.py:51-52`; query `store/postgres.py:454-462` |
| `/metrics` loads full history incl. episode blobs | `api/metrics.py:104-121`; `store/postgres.py:110-118, 464-491` |
| 32-bit run id + `ON CONFLICT DO NOTHING` | `store/memory.py:76-79`; `jobs/queue.py:463-466`; `store/postgres.py:407-415` |
| Run-id regexes pin exactly 8 hex (widening is breaking) | `cli.py:69, 207`; `jobs/router.py:29-35` |
| `created_at` is second-precision (so `max()` is ambiguous on ties) | `engine/scorecard.py:43-45`; tiebreak at `api/metrics.py:115` |
| `StoredRun` has no timing/cost field (cost not computable from stored data) | `validsim/store/memory.py:26-52` |
| **`duration_s` is SIMULATED, not measured — must never be a cost input** | `validsim/sim/runner.py:61` (field), `:154` (seeded `rng.uniform` assignment) |
| No timing instrumentation on the validation path at all | grep `perf_counter\|monotonic\|elapsed\|gpu_seconds` → only `api/main.py:190,347`, `jobs/router.py:256` |
| Exit code 2 currently means usage error (so `doctor` must not reuse it) | `validsim/cli.py:179, 213` |
| `blocks = total - approvals` (so approvals+blocks is a tautology, not a reconciliation) | `validsim/api/metrics.py:117-118` |
| **SQLite runs DDL in `__init__` on every construction** (already non-side-effect-free) | `validsim/store/sqlite.py:170-179` (executescript + `_migrate()` + commit) |
| `DELETE` endpoint exists → row-count "counters" decrease | `validsim/api/main.py:665` |
| **Rate limiter fails CLOSED in code; runbook claimed fail-open (fixed)** | `api/main.py:110, 217-220, 227-228` vs old `runbook.md:75, 121-127` |
| `api-reference.md` self-contradicts on the same point (c3-api's file) | `docs/api-reference.md:145, 758, 828` vs `:764` |
| Exactly one API key per deployment (so key-hashing ≠ tenancy) | `validsim/api/main.py:535` |
| `installed > REQUIRED` must be tolerated to keep §4.6.3 rollback working | `docs/runbook.md:310-346` |
| **`.env.example` holds all secret NAMES with literal values (probe #7 must allowlist it)** | `.env.example:26, 41, 70, 113, 126, 145` |
| DSN embeds password as URL userinfo (defeats `KEY=value` scanning) | `.env.example:41` |
| **Save semantics diverge: PG first-write-wins vs memory/SQLite overwrite** | `postgres.py:407-429`; `sqlite.py:203`; `memory.py:87` |
| `JobRecord` has `lease_epoch` but **no `attempts` counter** (per-attempt cost needs one) | `validsim/jobs/models.py:85, 99` |
| `Scorecard.to_dict()`/`to_json()` is the hashed serialisation (strip `wall_clock_s`) | `engine/scorecard.py:113, 120`; persisted at `postgres.py:423` |
| **`robustness` is constant 100.0 under mock = 20% of every composite** | `engine/scorecard.py:34, 59-64`; single group at `sim/runner.py:175` |
| `VALIDSIM_JOB_LEASE_SECONDS` / `_MAX_DEPTH` were undocumented (now added) | was absent from `.env.example`, `runbook.md` §2, `async-jobs.md` §7 |
| **`duration_s` sum prices at $48.88/run vs vault's $5–7 (~8×)** | reproduced via `run_validation` w/ `MockIsaacBackend`, 5000 eps, seeds 42/43; draws at `sim/runner.py:110, 131-136, 154` |
| Worker reuses `spec.run_id` on **every** attempt → all attempts hit one row | `validsim/jobs/worker.py:281-289` |
| `gate` exit contract 0/1/2 is documented in **three** places | `actions/validate/action.yml:175-178`; `docs/github-actions.md:190-192`; `Makefile` smoke target |
| `validsim_run_cost` ledger locked; `gpu_seconds` NULLABLE (`None` ≠ `0.0`) | §7.2 (this proposal) |
| **`EvaluationResult.mean_duration_s` is a second, more dangerous cost field** | `engine/evaluation.py:43, 83`; persisted in every scorecard at `postgres.py:425` |
| Both cost fields agree to ~1% ($48.88 vs $48.23 at 5k eps) | §7.1a — banning one is insufficient |
| **CORRECTED: the two cost fields are algebraically IDENTICAL at a fixed seed (0.00% delta)** | §7.1a — the earlier "1.33%" was cross-seed sampling noise |
| `mean_duration_s` survives the store round trip bit-exactly | verified via `SqliteValidationStore` save/load: `9.895` → `9.895` |
| `gate --json` exists but collapses failure modes; usage errors print no JSON | `cli.py:419-421, 451-461` (payload), `:450` (verdict collapse) |
| `gate` 0/1/2 contract documented in **two** places, not three | `actions/validate/action.yml:175-178`; `docs/github-actions.md:190-196` |
| **Makefile has NO exit-code docs; `smoke` chains with `&&` so 1 and 2 are indistinguishable** | `Makefile:49` (only `gate` reference in the file) |
| `gate` has **no schema-version check** (2 = misuse only) | `docs/github-actions.md:196` |
| **Any worker exception is terminal on attempt 1 (no retry at all)** | `validsim/jobs/worker.py:137-148` |
| **Dead worker → reaped → retried forever, unobservable (no `attempts` field)** | `jobs/queue.py:554-579` (reap), `:501-521` (claim, `lease_epoch+1`); `jobs/models.py:91-99` (no `attempts`) |
| Reaper is **not** a hot loop (bounded to ~1 requeue/hr/job at 3600s lease) | `jobs/worker.py:150-178`; `_DEFAULT_LEASE_SECONDS = 3600` at `queue.py:82` |
| DLQ is **target-state, unimplemented** | `vault/04 - Engineering/Async Job Queue.md:15` |
| **`gate` runs on SQLite — the backend where append-only FAILS** | `cli.py:73` `_DURABLE_STORE_BACKENDS`; `actions/validate/action.yml:100-101` sets `VALIDSIM_STORE=sqlite` |
| Robustness constant ⇒ **identical digests for different robustness** | `scorecard.py:63-64`; `runner.py:175, 182`; `config.py:17` single-level `Literal` |
| `reap_expired` return value discarded → dead worker invisible | `jobs/worker.py:176`; `jobs/queue.py:554-579` |
| Worker renews lease at `lease/3` (slow ≠ stolen) | `jobs/worker.py:193-195`; default `_DEFAULT_LEASE_SECONDS = 3600` at `queue.py:82` |
| `VALIDSIM_JOB_LEASE_SECONDS` undocumented everywhere | `docs/async-jobs.md` §7 (365-376); `docs/runbook.md` §2; `.env.example` |
| Free tier sells "10 runs/mo" (so limit×N is a billing bug) | `vault/06 - Business/Pricing Tiers.md:18` |
| Enterprise tier advertises "SLA" (unsupportable numbers are commercial risk) | `vault/06 - Business/Pricing Tiers.md:21` |
| Secrets backend genuinely undecided in vault (one row, both options) | `vault/04 - Engineering/Tech Stack.md:48` |
| Pipeline is deterministic (lost runs are re-derivable) | `validsim/sim/runner.py::stable_seed`; `docs/runbook.md:471-474` |
| SOC 2 sequenced at Year 2 (rotation is not a launch blocker) | `vault/08 - Team & Legal/Compliance.md` |
| Single connection under `threading.Lock` | `store/postgres.py:286-297, 430-431` |
| `config_loader` flat-YAML only; PyYAML dev-only | `validsim/config_loader.py:105-116`; `pyproject.toml:29-35` |
| `health` is config-only (no I/O) | `validsim/api/main.py:592-602` |
| `VALIDSIM_API_KEY` read once at `create_app` | `validsim/api/main.py:535`; `docs/runbook.md:141-145` |
| No compose profiles; GPU is a comment | `docker-compose.yml` (no `profiles:`); lines 167-192 |
| Release builds but never publishes | `.github/workflows/release.yml:119-153`, esp. 153 |
| `pip install validsim==X` path reserved for future | `actions/validate/action.yml:108-110` |
| Unpinned dependencies, no hashes/lockfile | `requirements.txt:1-8`; `pyproject.toml:18-27` |
| Episode/adversarial bounds are per-request, not spend caps | `validsim/config.py:136-137`; `jobs/queue.py:65` |
| Queue back-pressure returns 503 | `jobs/router.py:104-120` |
| Unit-economics margin assumptions unmeasured | `vault/06 - Business/Unit Economics.md:14-15, 43` |
| Secrets beyond env vars is a known gap | `SECURITY.md:305-308` |
| Single shared API key, no RBAC | `SECURITY.md:306` |
| Redis is not the system of record | `docs/runbook.md:443-445` |
| Target 1k–100k episodes per run | `vault/04 - Engineering/Solution Architecture.md:121-124` |
| K8s/Argo/Terraform/S3 listed "not implemented" | `vault/04 - Engineering/Tech Stack.md:15` |
| Restore flags / no `-v` during recovery | `docs/runbook.md:399, 411, 417-418` |
| No external hires before month 4 (device-plugin rationale) | `vault/04 - Engineering/Tech Stack.md:87-88` |
