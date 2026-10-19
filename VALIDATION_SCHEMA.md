# ValidSim Validation Result Schema

**Version:** 1.0  
**Last Updated:** 2026-10-08  
**Status:** Stable (backward-compatible extensions only)

---

## Overview

This document defines the **canonical data structures** used throughout ValidSim's validation pipeline. These schemas are consumed by:

- Frontend dashboard (React/Astro pages)
- External integrations (webhooks, CI/CD pipelines)
- Third-party tools (Slack notifications, PDF exports)
- Database migrations (SQLite, PostgreSQL backends)

All fields are **frozen dataclasses** in `validsim/engine/*.py`. Modifications must preserve backward compatibility unless explicitly versioned.

---

## Core Result Types

### EpisodeResult (Raw Simulation Output)

**Location:** `validsim/sim/runner.py:30`  
**Purpose:** Single episode execution outcome

```python
@dataclass(frozen=True)
class EpisodeResult:
    """One episode's raw metrics."""
    
    run_id: str                          # Parent validation run id (vrun-xxxx)
    checkpoint_id: str                   # Model checkpoint being validated
    task_id: str                         # Task name (e.g., "bin_picking")
    episode_index: int                   # Sequential index within run (0-based)
    success: bool                        # Did episode complete its goal?
    duration_s: float                    # Time to completion in seconds
    metrics: dict[str, float]            # Task-specific KPIs (e.g., grasp_quality)
    failure_mode: str | None             # Why it failed, if applicable
    randomization_level: str | None      # Domain randomization seed level used
    created_at: str                      # ISO-8601 UTC timestamp
    error_message: str | None            # Exception text on failure
```

**Constraints:**
- `episode_index` is monotonically increasing per `run_id`
- `metrics` keys are task-defined; no global standardization
- `randomization_level` enables robustness analysis across perturbation groups

---

### EvaluationResult (Success/Failure Aggregation)

**Location:** `validsim/engine/evaluation.py:15`  
**Purpose:** Aggregate episode results into summary statistics

```python
@dataclass(frozen=True)
class EvaluationResult:
    """Summary metrics for one validation run."""
    
    total_episodes: int                              # N episodes executed
    success_count: int                               # Episodes where success=true
    success_rate: float                              # success_count / total_episodes (0.0 when empty)
    per_task_success: dict[str, float] = field()     # success_rate keyed by task_id
    failure_taxonomy: dict[str, int] = field()       # failure_mode -> count
    mean_duration_s: float = 0.0                     # Average duration across episodes
    
    def to_dict(self) -> dict[str, Any]:
        """JSON serialization format."""
```

**Example JSON:**
```json
{
  "total_episodes": 1000,
  "success_count": 742,
  "success_rate": 0.742,
  "per_task_success": {
    "bin_picking": 0.742,
    "door_opening": 0.680
  },
  "failure_taxonomy": {
    "grasp_slip": 156,
    "collision_detected": 89,
    "timeout": 45
  },
  "mean_duration_s": 12.4
}
```

**Important Notes:**
- `success_rate` is **never NaN**; returns 0.0 for empty episode sequences
- `per_task_success` groups by `task_id` from episode results
- `failure_taxonomy` counts unique `failure_mode` strings (None excluded)

---

### SafetyResult (Safety Violations Analysis)

**Location:** `validsim/engine/safety.py:40`  
**Purpose:** Count safety-critical violations during episodes

```python
@dataclass(frozen=True)
class SafetyResult:
    """Safety metric aggregation from episodes."""
    
    total_violations: int                    # Number of safety failures
    violation_details: list[SafetyViolation] # Individual violation records
    safety_score: float                      # Computed 0-100 score
    
    @staticmethod
    def compute(episodes: Sequence[EpisodeResult]) -> "SafetyResult"
```

**SafetyViolation Structure:**
```python
@dataclass(frozen=True)
class SafetyViolation:
    episode_index: int
    violation_type: str                      # e.g., "joint_limit_exceeded"
    severity: str                            # "low", "medium", "critical"
    timestamp_s: float                       # When violation occurred
    context: dict[str, Any]                  # Additional sensor/state data
```

**Safety Score Calculation:**
- Starts at 100.0
- Deducts points based on `violation.severity`:
  - Critical: -25 points each
  - Medium: -10 points each
  - Low: -5 points each
- Clamped to [0, 100] range

---

### RegressionItem (Single Metric Comparison)

**Location:** `validsim/engine/regression.py:37`  
**Purpose:** One metric's statistical comparison between baseline and current runs

```python
@dataclass(frozen=True)
class RegressionItem:
    """One metric compared between baseline and current runs."""
    
    metric: str                              # e.g., "success_rate", "mean_duration_s"
    before: float                            # Baseline value
    after: float                             # Current value
    delta: float                             # after - before (negative = worse for success)
    p_value: float | None                    # Bootstrap test p-value, or None for descriptive
    significant: bool                        # Does change cross significance threshold?
    severity: Literal["info", "warning", "critical"]
```

**Significance Thresholds:**

| Metric | Test Type | Alpha | Minimum Delta |
|--------|-----------|-------|---------------|
| success_rate | Two-proportion bootstrap (n=500) | 0.05 | >0.02pp = warning, >0.05pp = critical |
| mean_duration_s | Descriptive only | N/A | >20% increase = warning, >50% = critical |

**Severity Classification Logic:**
```python
if p_value is not None and p_value < 0.05:
    if abs(delta) > _CRITICAL_DROP:
        severity = "critical"
    elif abs(delta) > _WARNING_DROP:
        severity = "warning"
    else:
        severity = "info"
else:
    severity = "info"  # Not statistically significant
```

---

### RegressionReport (Collection of Regression Items)

**Location:** `validsim/engine/regression.py:60`  
**Purpose:** Aggregate multiple metric comparisons into one report

```python
@dataclass(frozen=True)
class RegressionReport:
    items: list[RegressionItem] = field(default_factory=list)
    
    @property
    def significant_regressions(self) -> list[RegressionItem]
    
    @property
    def has_regressions(self) -> bool
    
    @property
    def worst_severity(self) -> Severity
```

**Serialization Example:**
```json
{
  "items": [
    {
      "metric": "success_rate",
      "before": 0.820,
      "after": 0.742,
      "delta": -0.078,
      "p_value": 0.023,
      "significant": true,
      "severity": "critical"
    },
    {
      "metric": "mean_duration_s",
      "before": 11.2,
      "after": 12.4,
      "delta": 1.2,
      "p_value": null,
      "significant": false,
      "severity": "warning"
    }
  ]
}
```

---

### BenchmarkResult (Performance Baseline)

**Location:** `validsim/engine/benchmark.py:100`  
**Purpose:** Performance metrics for infrastructure planning

```python
@dataclass(frozen=True)
class BenchmarkResult:
    """Infrastructure performance baseline."""
    
    endpoint: str                                # API endpoint path
    requests_per_second: float                   # Throughput measurement
    p50_latency_ms: float                        # Median response time
    p95_latency_ms: float                        # 95th percentile latency
    p99_latency_ms: float                        # 99th percentile latency
    error_rate: float                            # % of requests returning non-2xx
    sample_size: int                             # Number of probe requests
```

**Usage Pattern:**
- Run load tests with `wrk` or `hey` against `/api/v1/health`
- Store `BenchmarkResult` in database for trend analysis
- Alert if p99 latency exceeds historical baseline by >20%

---

### Scorecard (Final Deploy Decision)

**Location:** `validsim/engine/scorecard.py:328`  
**Purpose:** **The single artifact CI gates on**

```python
@dataclass(frozen=True)
class Scorecard:
    """Final, serializable verdict for one validation run."""
    
    # Core identity (required)
    run_id: str                                  # Store-assigned id (e.g., "vrun-1a2b3c4d")
    checkpoint_id: str                           # Checkpoint under validation
    task_id: str                                 # Task that was executed
    
    # Composite decision score (0-100)
    composite_score: float                       # Weighted average see below
    
    # Component scores (each 0-100)
    success_rate: float                          # Fraction of episodes succeeded
    safety_score: float                          # Safety metric (see SafetyResult)
    robustness_score: float                      # Cross-group consistency; 0.0 if unmeasured
    
    # Measurement provenance flags (NEW in v1.0)
    robustness_measured: bool = True             # Were ≥2 randomization groups present?
    randomization_group_count: int = 0           # Actual group count observed
    regression_baseline_available: bool = True   # Was a baseline supplied for comparison?
    
    # Regression analysis (optional)
    regression_delta: float | None               # Success-rate delta vs baseline, or None
    confidence_interval: tuple[float, float] | None  # 95% bootstrap CI for success_rate
    
    # Deploy decision
    deploy_decision: Literal["APPROVE", "BLOCK"]
    threshold: float = 85.0                      # Score required to approve
    
    # Metadata
    created_at: str = ""                         # ISO-8601 UTC timestamp
    episode_count: int = 0                       # Total episodes scored
    failure_taxonomy: dict[str, int] = field()   # Failure mode counts
    
    # Adversarial testing (NEW in v1.0)
    adversarial_episode_count: int = 0
    adversarial_success_rate: float | None = None
    block_reasons: tuple[str, ...] = ()          # Reasons why BLOCKED
    
    def to_dict(self) -> dict[str, Any]:
        """JSON serialization with CI tuple → list conversion."""
```

---

## Composite Score Formula

**Weighted Sum of Measured Components Only**

```
composite = Σ(weight × component) / Σ(weights where measured)
```

**Component Weights:**
| Component | Weight | Condition |
|-----------|--------|-----------|
| Success Rate | 0.40 | Always measured |
| Safety Score | 0.30 | Always measured |
| Robustness | 0.20 | Only if ≥2 randomization groups |
| Regression | 0.10 | Only if baseline supplied |

**Critical Design Decision:** Unmeasured components **abstain** rather than receiving a fabricated 100.0.

**Example 1 (Normal Run):**
- 1 randomization group (`robustness_measured=false`)
- No baseline (`regression_baseline_available=false`)
- Success rate = 74.2%, Safety = 95.0%

```
Numerator = (0.40 × 74.2) + (0.30 × 95.0) = 29.68 + 28.5 = 58.18
Denominator = 0.40 + 0.30 = 0.70
Composite = 58.18 / 0.70 = 83.11 → BLOCK at 85.0 threshold
```

**Example 2 (Full Evidence):**
- 3 randomization groups (`robustness_measured=true`, score = 88.0)
- Baseline available (`regression_baseline_available=true`, delta = -0.03)
- Success rate = 82.0%, Safety = 98.0%

```
Numerator = (0.40 × 82.0) + (0.30 × 98.0) + (0.20 × 88.0) + (0.10 × 92.5) = 97.25
Denominator = 1.00
Composite = 97.25 → APPROVE
```

---

## Block Reasons List

**When a run is BLOCKED, `block_reasons` explains why:**

```python
block_reasons = []

# Rule 1: Did we run enough episodes?
if episode_count < task.episodes_requested:
    block_reasons.append(f"Insufficient episodes: {episode_count}/{task.episodes_requested}")

# Rule 2: Did we achieve at least one success?
if success_count == 0:
    block_reasons.append("Zero successes across all episodes")

# Rule 3: Adversarial floor check (if adversarial episodes ran)
if adversarial_episode_count >= 30:
    from scipy.stats import binom_test
    if binom_test(adversarial_success_count, adversarial_episode_count, p=0.60, alternative='less') < 0.05:
        block_reasons.append(f"Adversarial success rate {adversarial_success_rate:.1%} significantly below 60% floor")

# Rule 4: Composite threshold
if composite_score < threshold:
    block_reasons.append(f"Composite score {composite_score:.1f} below threshold {threshold}")
```

**Examples:**
```json
{
  "deploy_decision": "BLOCK",
  "block_reasons": [
    "Insufficient episodes: 500/1000",
    "Composite score 72.3 below threshold 85.0"
  ]
}
```

---

## Serialization Formats

### JSON Response (API v1)

**Endpoint:** `GET /api/v1/validations/{id}/scorecard`

```json
{
  "run_id": "vrun-1a2b3c4d",
  "checkpoint_id": "ckpt-v0.3.2",
  "task_id": "bin_picking",
  "composite_score": 83.11,
  "success_rate": 0.742,
  "safety_score": 95.0,
  "robustness_score": 0.0,
  "robustness_measured": false,
  "randomization_group_count": 1,
  "regression_baseline_available": false,
  "regression_delta": null,
  "confidence_interval": [0.712, 0.771],
  "deploy_decision": "BLOCK",
  "threshold": 85.0,
  "created_at": "2026-10-08T14:23:17+00:00",
  "episode_count": 1000,
  "failure_taxonomy": {
    "grasp_slip": 156,
    "collision_detected": 89,
    "timeout": 45
  },
  "adversarial_episode_count": 100,
  "adversarial_success_rate": 0.720,
  "block_reasons": [
    "Composite score 83.1 below threshold 85.0"
  ]
}
```

**Notes:**
- Confidence interval serialized as **list**, not tuple (JSON limitation)
- Null values use `null`, not `None` or empty string
- Timestamps always ISO-8601 with timezone (+00:00 or Z)

---

## Field Evolution & Versioning

### v1.0 Changes (2026-10-08)

**Added Fields:**
- `robustness_measured: bool` - Provenance flag explaining 0.0 robustness
- `randomization_group_count: int` - Actual number of groups exercised
- `regression_baseline_available: bool` - Whether regression component abstained properly
- `adversarial_episode_count: int` - Count of adversarial scenarios run
- `adversarial_success_rate: float | None` - Success rate on adversarial subset
- `block_reasons: tuple[str, ...]` - Explicit reasons for BLOCK decisions

**Backward Compatibility Guarantee:**
- All new fields have **default values** matching previous behavior
- Positional construction (e.g., database `INSERT` without these columns) still works
- Legacy rows reconstruct without claiming they were "unmeasured" (we cannot tell)

---

## Validation Rules

### Client-Side Validation (Frontend Dashboard)

Before displaying a Scorecard:

```javascript
function validateScorecard(card) {
  const errors = [];
  
  // Required fields
  if (!card.run_id || !card.checkpoint_id || !card.task_id) {
    errors.push("Missing core identity fields");
  }
  
  // Score ranges
  if (card.composite_score < 0 || card.composite_score > 100) {
    errors.push(`Invalid composite_score: ${card.composite_score}`);
  }
  
  // Consistency checks
  if (card.success_rate < 0 || card.success_rate > 1) {
    errors.push(`success_rate must be 0-1, got: ${card.success_rate}`);
  }
  
  // Provenance flag consistency
  if (card.robustness_score === 0 && card.robustness_measured) {
    console.warn("Robustness measured but score is 0 — verify calculation");
  }
  
  return { valid: errors.length === 0, errors };
}
```

---

## Storage Schemas

### SQLite Table Definition

```sql
CREATE TABLE IF NOT EXISTS validations (
  run_id TEXT PRIMARY KEY,
  checkpoint_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  composite_score REAL NOT NULL,
  success_rate REAL NOT NULL,
  safety_score REAL NOT NULL,
  robustness_score REAL NOT NULL,
  robustness_measured INTEGER NOT NULL DEFAULT 0,
  randomization_group_count INTEGER NOT NULL DEFAULT 0,
  regression_baseline_available INTEGER NOT NULL DEFAULT 1,
  regression_delta REAL,
  confidence_interval_low REAL,
  confidence_interval_high REAL,
  deploy_decision TEXT NOT NULL,
  threshold REAL NOT NULL DEFAULT 85.0,
  created_at TEXT NOT NULL,
  episode_count INTEGER NOT NULL DEFAULT 0,
  failure_taxonomy TEXT,  -- JSON-encoded dict
  adversarial_episode_count INTEGER NOT NULL DEFAULT 0,
  adversarial_success_rate REAL,
  block_reasons TEXT      -- JSON-encoded array
);

CREATE INDEX idx_checkpoint ON validations(checkpoint_id);
CREATE INDEX idx_created ON validations(created_at DESC);
```

**Field Mapping Notes:**
- Booleans stored as INTEGER (0/1) — SQLite type affinity quirk
- Confidence interval split into two columns (low/high) because JSON stores tuples as lists
- Failure taxonomy and block reasons stored as JSON text for flexibility
- `NULL` allowed for optional fields (`regression_delta`, `adversarial_success_rate`)

---

### PostgreSQL Table Definition

```sql
CREATE TABLE validations (
  run_id TEXT PRIMARY KEY,
  checkpoint_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  composite_score DOUBLE PRECISION NOT NULL,
  success_rate DOUBLE PRECISION NOT NULL,
  safety_score DOUBLE PRECISION NOT NULL,
  robustness_score DOUBLE PRECISION NOT NULL,
  robustness_measured BOOLEAN NOT NULL DEFAULT false,
  randomization_group_count INTEGER NOT NULL DEFAULT 0,
  regression_baseline_available BOOLEAN NOT NULL DEFAULT true,
  regression_delta DOUBLE PRECISION,
  confidence_interval_low DOUBLE PRECISION,
  confidence_interval_high DOUBLE PRECISION,
  deploy_decision TEXT NOT NULL CHECK (deploy_decision IN ('APPROVE', 'BLOCK')),
  threshold DOUBLE PRECISION NOT NULL DEFAULT 85.0,
  created_at TIMESTAMPTZ NOT NULL,
  episode_count INTEGER NOT NULL DEFAULT 0,
  failure_taxonomy JSONB,
  adversarial_episode_count INTEGER NOT NULL DEFAULT 0,
  adversarial_success_rate DOUBLE PRECISION,
  block_reasons JSONB
);

CREATE INDEX idx_validations_checkpoint ON validations(checkpoint_id);
CREATE INDEX idx_validations_created ON validations(created_at DESC);
CREATE INDEX idx_validations_decision ON validations(deploy_decision) WHERE deploy_decision = 'BLOCK';
```

**PostgreSQL Advantages:**
- Native `JSONB` for efficient querying of `failure_taxonomy`
- `TIMESTAMPTZ` preserves timezone info automatically
- `CHECK` constraint enforces valid `deploy_decision` values
- Index on BLOCK decisions optimizes "find all rejected runs" queries

---

## Extensibility Guidelines

### Adding New Metrics

If you need to extend the schema with new metrics:

1. **Add to `EvaluationResult` or `SafetyResult` first** (depending on what's being measured)
2. **Update `Scorecard.build_scorecard()`** to incorporate the new metric into the composite
3. **Document in this file** under "Schema Evolution"
4. **Run migration** for existing databases (`validsim/migrate.py`)

**Do NOT modify frozen dataclass fields directly.** If changing requires breaking changes:
- Create `ScorecardV2` (or similar versioned class)
- Maintain both implementations until legacy clients migrate
- Update API response version header

---

## Common Pitfalls

### Pitfall 1: Misinterpreting 0.0 Robustness Score

**Wrong:** "Robustness = 0.0 means the model is unstable across randomizations."

**Right:** "Robustness = 0.0 usually means **only one randomization group was exercised**. Check `robustness_measured` — if `false`, the 0.0 is an **abstention**, not a measurement."

### Pitfall 2: Ignoring `block_reasons` Length

```python
# BAD: Only checking deploy_decision
if scorecard.deploy_decision == "BLOCK":
    alert("Deployment blocked!")

# GOOD: Showing specific reasons
if scorecard.deploy_decision == "BLOCK":
    for reason in scorecard.block_reasons:
        alert(reason)
```

### Pitfall 3: Assuming Regression Delta is Always Present

```python
# BAD
delta = scorecard.regression_delta
if delta < -0.02:
    alert("Significant regression detected!")

# GOOD
if scorecard.regression_baseline_available:
    delta = scorecard.regression_delta
    if delta and delta < -0.02:
        alert("Significant regression detected!")
else:
    log("No baseline available; regression delta not computed")
```

---

## Related Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System overview, data flow diagrams
- **[ADR 0002](docs/adr/0002-scorecard-composite-gate.md)** — Why we exclude unmeasured components from denominator
- **[API Reference](docs/api-reference.md)** — REST endpoints that return these schemas
- **[Engine Pipeline](validsim/engine/pipeline.py)** — How these objects are constructed end-to-end

---

## Changelog

| Date | Version | Change | Author |
|------|---------|--------|--------|
| 2026-10-08 | 1.0 | Initial release with robustness provenance flags, adversarial tracking, block reasons | Founder 2 |
| 2026-09-28 | 0.9 | Draft spec (internal use only) | Founder 1 |
