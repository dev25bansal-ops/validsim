# ValidSim Production Deployment Guide

**Purpose**: Deploy ValidSim MVP to production infrastructure for real-world robot model validation workflows.

**Target Audience**: DevOps engineers, SREs, platform teams deploying to cloud providers (AWS/GCP/Azure) or on-premise Kubernetes clusters.

---

## Quick Start (Docker Compose)

For staging environments or small-scale deployments:

```bash
# Pull latest image
docker pull ghcr.io/abhijeet1267/validsim:latest

# Generate secrets into .env — docker-compose.prod.yml requires
# POSTGRES_PASSWORD, REDIS_PASSWORD and VALIDSIM_API_KEY and aborts if any is
# unset. (.gitignore excludes .env, so secrets are never committed.)
[ -f .env ] || touch .env
grep -q '^POSTGRES_PASSWORD=' .env 2>/dev/null || echo "POSTGRES_PASSWORD=$(openssl rand -hex 32)" >> .env
grep -q '^REDIS_PASSWORD='    .env 2>/dev/null || echo "REDIS_PASSWORD=$(openssl rand -hex 32)"    >> .env
grep -q '^VALIDSIM_API_KEY='  .env 2>/dev/null || echo "VALIDSIM_API_KEY=$(openssl rand -hex 32)"  >> .env

# Start stack (the file sets VALIDSIM_ENV=production, VALIDSIM_STORE=postgres
# and VALIDSIM_JOB_QUEUE=redis for you)
docker compose -f docker-compose.prod.yml up -d

# Verify health (production requires the key)
curl -H "X-API-Key: $(grep '^VALIDSIM_API_KEY=' .env | cut -d= -f2)" http://localhost:8000/api/v1/health
```

---

## Architecture Overview

### Components

| Component | Role | Production Considerations |
|-----------|------|--------------------------|
| **API Service** | FastAPI backend serving REST endpoints, dashboard SPA | Stateless, horizontally scalable behind load balancer |
| **PostgreSQL** | Primary data store for validations, runs, job metadata | Requires read replicas for high availability, WAL archiving for backup |
| **Redis** | Job queue and rate limiting state | Use Redis Cluster or AWS ElastiCache for HA |
| **Worker Process** | Async job consumer (simulation episodes) | Can scale independently based on queue depth |
| **Dashboard SPA** | Static frontend served via API's `StaticFiles` mount | CDN caching recommended (CloudFront, Cloudflare) |

### Data Flow

```
User/API Client → Load Balancer → API Service (FastAPI)
                                    ├→ PostgreSQL (persist runs)
                                    └→ Redis (job queue)
                                        
Worker Process ← Redis (consume jobs) → Simulation Backend → Robot Fleet
```

---

## Prerequisites

### Infrastructure Requirements

- **Kubernetes cluster** (v1.25+) OR **Docker Swarm** / **DC/OS**
- **PostgreSQL 15+** (managed: AWS RDS, GCP Cloud SQL; or self-hosted with patroni)
- **Redis 7+** (managed: AWS ElastiCache, Azure Cache for Redis; or self-hosted)
- **Container registry** (GHCR, ECR, GCR, or private Harbor registry)
- **Load balancer** (ALB/NLB, NGINX ingress, Traefik)

### Security Requirements

- TLS certificates (ACM, Let's Encrypt, or custom CA)
- API key management (HashiCorp Vault, AWS Secrets Manager)
- Network policies (VPC peering, security groups, pod network policies)
- RBAC configuration for service accounts

---

## Deployment Options

### Option 1: Single-Node Docker Compose (Staging/SMB)

Use `docker-compose.prod.yml` from repository root — the canonical file is
checked in (it was previously referenced here but missing from the repository).
Inspect it before deploying:

```bash
less docker-compose.prod.yml
```

The stack defines four services (`postgres`, `redis`, `api`, `worker`), each
with a healthcheck; the app services wait for `service_healthy` before starting.
Key requirements encoded in the file:

- **`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `VALIDSIM_API_KEY` are all
  required** — Compose aborts if any is unset (fail-fast rather than start
  insecure).
- **`VALIDSIM_ENV=production`** makes an unset/blank `VALIDSIM_API_KEY` a
  startup failure.
- **`VALIDSIM_STORE=postgres` and `VALIDSIM_JOB_QUEUE=redis`** are set on **both**
  the `api` and `worker`. Without `VALIDSIM_JOB_QUEUE=redis` on the API, a
  submitted job is enqueued into the API process' private in-memory queue, the
  worker (which drains Redis) never sees it, and the job stays `queued` forever
  with no error.
- Redis requires a password (`--requirepass`); the `api`/`worker` connect via
  `redis://:<password>@redis:6379/0`.
- The `api`/`worker` images come from GHCR
  (`ghcr.io/abhijeet1267/validsim:<tag>`), published by
  `.github/workflows/release.yml`.

Deploy:

```bash
# Generate secrets and write them to .env (run once). Compose reads
# ${POSTGRES_PASSWORD}, ${REDIS_PASSWORD} and ${VALIDSIM_API_KEY} from the
# project-dir .env file — writing them to separate .env.* files would leave the
# variables empty and abort startup ("set POSTGRES_PASSWORD"). .gitignore
# excludes .env/.env.* so these secrets are never committed.
[ -f .env ] || touch .env
grep -q '^POSTGRES_PASSWORD=' .env 2>/dev/null || echo "POSTGRES_PASSWORD=$(openssl rand -hex 32)" >> .env
grep -q '^REDIS_PASSWORD='    .env 2>/dev/null || echo "REDIS_PASSWORD=$(openssl rand -hex 32)"    >> .env
grep -q '^VALIDSIM_API_KEY='  .env 2>/dev/null || echo "VALIDSIM_API_KEY=$(openssl rand -hex 32)"  >> .env

# Start
docker compose -f docker-compose.prod.yml up -d

# Verify health (with or without a key configured; production always has one)
curl -H "X-API-Key: $VALIDSIM_API_KEY" http://localhost:8000/api/v1/health
```

### Option 2: Kubernetes (Production HA)

#### Helm Chart Structure

Create `charts/validsim/` directory with templates:

```yaml
# values.yaml
api:
  replicas: 2
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"

worker:
  replicas: 3
  resources:
    requests:
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "4Gi"
      cpu: "2000m"

postgresql:
  primary:
    persistence:
      size: 50Gi
  auth:
    password: {{ .Values.secret.postgresPassword }}

redis:
  master:
    persistence:
      size: 10Gi
  auth:
    password: {{ .Values.secret.redisPassword }}

ingress:
  enabled: true
  className: nginx
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: validsim.yourdomain.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: validsim-tls
      hosts:
        - validsim.yourdomain.com

serviceAccount:
  create: true
  name: validsim-service-account
```

Deploy:

```bash
helm install validsim ./charts/validsim \
  --namespace validsim \
  --create-namespace \
  --set secret.postgresPassword=$(openssl rand -hex 32) \
  --set secret.redisPassword=$(openssl rand -hex 32) \
  --set secret.apiKey=$(openssl rand -hex 32) \
  --wait
```

#### Kubernetes Manifests (Alternative to Helm)

Apply manifests from `k8s/manifests/`:

```bash
kubectl apply -f k8s/manifests/postgres-pvc.yaml
kubectl apply -f k8s/manifests/redis-configmap.yaml
kubectl apply -f k8s/manifests/secrets.yaml
kubectl apply -f k8s/manifests/api-deployment.yaml
kubectl apply -f k8s/manifests/worker-deployment.yaml
kubectl apply -f k8s/manifests/service.yaml
kubectl apply -f k8s/manifests/ingress.yaml
```

---

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VALIDSIM_API_KEY` | Yes | — | API authentication token (min 32 chars) |
| `VALIDSIM_ENV` | Yes | `development` | Runtime environment (`development`, `staging`, `production`) |
| `VALIDSIM_PG_URL` | Yes | — | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`) |
| `VALIDSIM_REDIS_URL` | No | `memory://` | Redis URL for job queue (`redis://user:pass@host:6379/0`) |
| `VALIDSIM_WORKER_CONCURRENCY` | No | `4` | Number of parallel episode workers per process |
| `VALIDSIM_RATE_LIMIT_MAX` | No | `100` | Max requests per minute per API key |
| `VALIDSIM_CORS_ORIGINS` | No | `*` | Comma-separated allowed CORS origins |
| `VALIDSIM_LOG_LEVEL` | No | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Database Migration

Run migrations before first deploy:

```bash
# PostgreSQL
psql -U validsim -d validsim -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"

# SQLite (if using local dev)
sqlite3 validsim.db ".schema"  # Verify tables exist
```

Verify migration status:

```bash
python -m validsim.cli health  # Should show "store_backend": "postgres"
```

---

## Scaling Strategy

### Horizontal Scaling

#### API Service

Scale based on request latency and CPU utilization:

```yaml
# KEDA autoscaler example
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: validsim-api-scaler
spec:
  scaleTargetRef:
    name: validsim-api
  minReplicaCount: 2
  maxReplicaCount: 10
  triggers:
    - type: resource
      adapter: resource
      metadata:
        type: resource-name
        value: cpu
        averageValue: "70%"
```

#### Worker Pool

Scale based on Redis queue depth:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: validsim-worker-scaler
spec:
  scaleTargetRef:
    name: validsim-worker
  minReplicaCount: 1
  maxReplicaCount: 20
  triggers:
    - type: redis
      adapter: redis
      metadata:
        redisHost: redis:6379
        redisPort: 6379
        redisKey: validsim:queue:length
        value: "100"
        targetAverageValue: "50"
```

### Vertical Scaling

Adjust resource quotas in deployments:

```yaml
resources:
  requests:
    memory: "1Gi"
    cpu: "500m"
  limits:
    memory: "4Gi"
    cpu: "2000m"
```

Increase worker `CPU limit` for compute-heavy simulation tasks. Increase `memory` for large episode batches.

---

## Monitoring & Observability

### Metrics Collection

Export Prometheus metrics from FastAPI app:

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

Scrape interval: 15s

#### Key Metrics

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `http_requests_total` | Counter | Total HTTP requests by route/method/status | Error rate > 1% |
| `http_request_duration_seconds` | Histogram | Request latency P50/P95/P99 | P99 > 5s |
| `db_connection_pool_usage` | Gauge | Active DB connections | > 80% pool size |
| `redis_queue_depth` | Gauge | Pending jobs in queue | > 1000 jobs |
| `validation_runs_total` | Counter | Completed validations | Downtime > 5min |

### Logging

Structured JSON logs via `python-json-logger`:

```python
import logging
import sys
from python_json_logger import JsonLogger

logger = logging.getLogger("validsim")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
logger.addHandler(handler)
```

Forward to ELK stack, Datadog, or Splunk.

### Tracing

OpenTelemetry integration for distributed tracing:

```bash
export OTEL_SERVICE_NAME=validsim-api
export OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
```

Trace propagation headers included in all outbound requests.

---

## High Availability Patterns

### PostgreSQL HA

- **Primary-Replica Setup**: One writer, two+ readers for query offload
- **Wal-G Backup**: Point-in-time recovery with hourly snapshots
- **Patroni**: Leader election and automatic failover

### Redis HA

- **Redis Sentinel**: Automatic failover with 3-node cluster
- **AWS ElastiCache**: Managed Redis with Multi-AZ replication

### API Statelessness

- All state externalized to PostgreSQL/Redis
- Container restarts safe; no local disk dependency
- Session tokens JWT-signed with short TTL (1hr)

---

## Disaster Recovery

### Backup Strategy

#### Database Backups

```bash
# Daily full backup (cron)
0 2 * * * pg_dump -h postgres -U validsim validsim | gzip > /backups/validsim-$(date +%Y%m%d).sql.gz

# WAL archiving (PostgreSQL config)
wal_level = replica
archive_mode = on
archive_command = 'aws s3 cp %p s3://validsim-wal-archive/%f'
```

Restore:

```bash
gunzip validsim-20260110.sql.gz | psql -h postgres -U validsim validsim
```

#### Redis Snapshots

RDB persistence configured for durability:

```conf
save 900 1
save 300 10
save 60 10000
stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
```

### Failover Runbook

1. **Detect Failure**: Alert fires on API health check failure
2. **Switch DNS**: Update load balancer health checks to redirect traffic
3. **Promote Replica**: If Postgres primary down, promote standby
4. **Restart Services**: `kubectl rollout restart deployment/validsim-api`
5. **Validate**: Health check passes within 2 minutes

**RTO Target**: < 5 minutes  
**RPO Target**: < 1 hour (based on backup frequency)

---

## Security Hardening

### API Authentication

Enforce X-API-Key header validation at load balancer layer (terminating TLS):

```nginx
location /api/ {
    if ($http_x_api_key = "") {
        return 401;
    }
    
    proxy_pass http://api-service:8000;
}
```

Rate limiting via Redis token bucket:

```python
async def rate_limit_dependency(api_key: str, request: Request):
    ip = request.client.host
    count = await redisincr(f"ratelimit:{ip}:{api_key}")
    if count > 100:
        raise HTTPException(429, "Rate limit exceeded")
```

### Network Policies

Restrict access via Kubernetes NetworkPolicy:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: validsim-api-netpol
spec:
  podSelector:
    matchLabels:
      app: validsim-api
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              name: ingress-nginx
      ports:
        - protocol: TCP
          port: 8000
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: validsim-postgres
      ports:
        - protocol: TCP
          port: 5432
    - to:
        - podSelector:
            matchLabels:
              app: validsim-redis
      ports:
        - protocol: TCP
          port: 6379
```

### Secret Management

Store secrets in HashiCorp Vault:

```yaml
# vault-secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: validsim-secrets
stringData:
  postgres-password: {{VaultSecret "database/postgres#password"}}
  redis-password: {{VaultSecret "cache/redis#password"}}
  api-key: {{VaultSecret "app/api#key"}}
```

---

## Testing Checklist Before Production

- [ ] All 3086 tests passing (`python -m pytest tests/ -q`)
- [ ] Coverage >= 90% (`pytest --cov=validsim --cov-report=term-missing --cov-fail-under=90`)
- [ ] API health endpoint returns 200 with correct fields (`status`, `auth_enabled`, `store_backend`, `job_queue_backend`)
- [ ] Database migrations applied successfully (`validsim-cli health` shows backend type)
- [ ] Job queue drains correctly under load (`docker compose up worker` consumes queued jobs)
- [ ] Rate limiting enforces limits (100 req/min default)
- [ ] PDF scorecard generation works for sample run ID
- [ ] Dashboard SPA loads without errors (check browser console)
- [ ] TLS termination working (HTTPS only, HSTS header present)
- [ ] Monitoring dashboards populated (Grafana, CloudWatch, etc.)
- [ ] Alerts configured (error rate, latency, queue depth, disk space)
- [ ] Backup/restore procedure validated (test restore in isolated environment)
- [ ] Failover tested (chaos engineering: kill Pod, verify auto-restart)

---

## Rollout Procedure

### Blue-Green Deployment

1. **Deploy Green Environment**: New version alongside existing blue
2. **Run Smoke Tests**: Automated health checks against green
3. **Switch Traffic**: Load balancer points to green
4. **Monitor**: Watch error rates, latency, queue depth for 15 minutes
5. **Rollback if Needed**: Switch back to blue; investigate green logs

### Canary Release

- Route 5% of traffic to new version initially
- Gradually increase to 25%, 50%, 100% over 1 hour
- A/B test metrics between canary and stable versions

---

## Cost Optimization

### Resource Requests/Limits

Tune based on actual usage patterns:

```bash
# Monitor resource usage
kubectl top pods -n validsim
kubectl describe pod <pod-name> -n validsim | grep -A 10 "Limits"
```

Right-size to avoid over-provisioning while maintaining headroom.

### Spot Instances

Use AWS EC2 Spot for worker nodes (stateless, fault-tolerant):

```yaml
# Kubernetes node selector
nodeSelector:
  topology.kubernetes.io/zone: us-west-2a
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: node-type
              operator: In
              values:
                - spot
```

Expected savings: 60-90% vs On-Demand pricing.

---

## Troubleshooting

### Common Issues

#### API Returns 401 Unauthorized

**Cause**: Missing or incorrect X-API-Key header

**Fix**:
```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/health
```

#### Workers Not Consuming Jobs

**Cause**: Redis connectivity issue or queue depth exceeded

**Fix**:
```bash
# Check Redis connection
redis-cli -h redis ping  # Should return "PONG"

# Inspect queue length
redis-cli LLEN validsim:queue:length
```

#### Slow Response Times

**Cause**: Database query bottleneck or worker saturation

**Fix**:
```sql
-- Check slow queries
EXPLAIN ANALYZE SELECT * FROM validations WHERE checkpoint_id = 'abc123';

-- Add index if needed
CREATE INDEX idx_validations_checkpoint ON validations(checkpoint_id);
```

---

## Support & Escalation

### Internal Contacts

- **Platform Team**: Platform-engineering@company.com
- **On-Call Rotation**: PagerDuty escalation policy "ValidSim-SRE"
- **GitHub Issue Tracker**: https://github.com/abhijeet1267/validsim/issues

### External Dependencies

- **PostgreSQL Maintenance**: Database team (DBA-team@company.com)
- **Redis Operations**: Cache team (cache-ops@company.com)
- **Cloud Provider Support**: AWS Enterprise Support (ticket portal)

---

## Appendix: Sample Production Configurations

### AWS Architecture Diagram

```
User → CloudFront (HTTPS) → ALB → EKS (validsim-api, validsim-worker)
                                            ↓
                          RDS PostgreSQL (Multi-AZ) + ElastiCache Redis
                                            ↓
                        ECR (container registry) + Lambda (backup jobs)
```

### GCP Equivalent

```
User → Cloud Load Balancing → GKE (validsim-api, validsim-worker)
                                        ↓
                       Cloud SQL PostgreSQL + Memorystore Redis
                                        ↓
                    Artifact Registry + Cloud Functions (scheduled backups)
```

---

*Document Version*: 1.0  
*Last Updated*: 2026-10-11  
*Maintained By*: Founder 2 Engineering Team
