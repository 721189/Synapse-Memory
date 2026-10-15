# Changelog

## 1.2.0 — Production-hardening release (2026-10-15)
- NULL-safe hybrid RRF (`COALESCE` per channel) + lexical param-count fix.
- Combined-protection field encryption (`content` ciphertext + `content_plain`
  search projection); v4 migration backfills legacy rows only.
- Decay composition `S=R*D` preserved through `/query` (`retrieval_score` + `decay_multiplier`).
- Gateway fail-closed: liveness vs readiness, accurate 401/403/429/5xx, no fake successes.
- Thread-safe pooling (`ThreadedConnectionPool`, timeout, rollback-on-return).
- Helm split engine/gateway workloads + services; in-cluster engine URL; secrets out of ConfigMap.
- Strict API validation limits; generic auth-failure messages.
- Live `/metrics` (requests, latency, auth/429, ingest/retrieval, pool, packed tokens).
- Distributed rate limiting (`REDIS_URL` opt-in; in-process fallback, `RATE_LIMIT_WINDOW_SECONDS`).
- Least-privilege PostgreSQL roles: `scripts/provision_db_roles.py` creates/repairs a DML-only
  runtime role (non-owner, `NOBYPASSRLS`) + a migration role and fails closed unless the
  invariant verifies. New `SYNAPSE_APPLY_DB_SCHEMA=false` "migrate-then-run" engine mode;
  the engine refuses to start when the schema is missing instead of using in-memory storage.
  Tenant isolation is now regression-tested with the real runtime role.
- `SynapseClient` SDK (typed, retries, envelope-aware); version map below.
- Retrieval eval corpus + measured-only benchmarks; backup/recovery + quickstart docs.

### Compatibility
| Component | Version | Notes |
|---|---|---|
| Python SDK (`synapse-memory`) | 1.2.0 | `SynapseClient`, `__version__ = "1.2.0"` |
| HTTP API compat | v1 | canonical `{success, data, request_id}` envelope; `/api/*` aliases stable |
| Engine / gateway images | 1.2.0 pair | must deploy matching tags |
| Helm chart | 0.1.0 (app 1.2.0) | chart version ≠ app version; see Chart.yaml |
| Schema | v4 ledger | forward-only; v4 adds `content_plain` |

### Known limitations
- Rate limiting is in-process by default; set `REDIS_URL` to enforce limits
  across all engine replicas (Redis sorted-set sliding window).
- Embeddings + tsvector are readable derivatives (field encryption covers `content`).
- Benchmarks in `bench_suite.py` include projections; `test_retrieval_eval.py` is measured-only.

## 0.1.0 — Initial prototype
- Pre-hardening baseline; do not use for production.
