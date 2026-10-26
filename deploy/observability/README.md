# Observability — Prometheus + Grafana

Basic monitoring stack for the Synapse Memory engine. The engine already exposes a
dependency-free Prometheus text endpoint at `GET /metrics` (port `8008`); this stack
just scrapes it and renders a dashboard.

## What you get

- **Prometheus** scrapes the engine every 15s.
- **Grafana** (port `3001`) auto-loads a "Synapse Memory" dashboard with:
  - memories indexed, engine up, embedding dimension, rate-limiter backend
  - request rate + p95 latency per endpoint
  - auth failures + rate-limit rejections
  - ingest/retrieval volume
  - DB pool utilization, packed tokens, retrieval candidates

## Quick start (Docker Compose)

```bash
# 1. Start the application stack (creates the shared network)
docker compose -f deploy/docker-compose.yml up -d

# 2. Start monitoring
docker compose -f deploy/observability/docker-compose.yml up -d

# Grafana: http://localhost:3001  (admin / admin)
# Prometheus: http://localhost:9090
```

If the shared network name does not resolve, run both stacks with the same project
name so the network prefix matches:

```bash
docker compose -p synapse -f deploy/docker-compose.yml up -d
docker compose -p synapse -f deploy/observability/docker-compose.yml up -d
```

## Standalone Prometheus (no Docker)

```bash
# Adjust prometheus/prometheus.yml target to "localhost:8008", then:
prometheus --config.file=deploy/observability/prometheus/prometheus.yml
```

## Metrics reference

See `docs/api-reference.md` → `GET /metrics`, and `docs/ops-runbook.md` for the
triage mapping (e.g. `synapse_db_pool_in_use/size`, `synapse_rate_limit_rejections_total`).
