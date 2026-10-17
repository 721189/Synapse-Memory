# Changelog

## Unreleased
- **Packaging:** distribution renamed to `synapse-memory-engine` on PyPI (the
  `synapse-memory` name is owned by an unrelated project). The import package
  stays `synapse_memory`. Root `pyproject.toml` is now the
  `synapse-memory-workspace` tooling manifest; the published SDK lives in
  `sdk/python`. New tag-triggered publish workflow builds, fresh-venv-verifies
  and trusted-publishes the wheel (see `docs/publishing.md`).

## 1.2.0 — Production-hardening release (2026-10-15)
- NULL-safe hybrid RRF (`COALESCE` per channel) + lexical param-count fix.
- Combined-protection field encryption (`content` ciphertext + `content_plain`
  search projection); v4 migration backfills legacy rows only.
- Decay composition `S=R*D` preserved through `/query` (`retrieval_score` + `decay_multiplier`).
- Gateway fail-closed: liveness vs readiness, accurate 401/403/429/5xx, no fake successes.
- Thread-safe pooling (`ThreadedConnectionPool`, timeout, rollback-on-return).
- Helm split engine/gateway workloads + services; secrets out of ConfigMap.
- Helm P1-2 residual fixed: PostgreSQL is a DOCUMENTED external dependency (the inert
  `postgresql:` values subtree is gone; no undeclared chart dependencies); the gateway's
  `PYTHON_ENGINE_URL` is derived from the release fullname (the hardcoded
  `synapse-memory-engine` hostname did not match the Service that `helm install synapse`
  actually renders); kubelet `$(VAR)` expansion ordering fixed (`DB_PASSWORD`/`REDIS_PASSWORD`
  now precede their consumers — previously the literal `$(DB_PASSWORD)` shipped as the DSN
  password and fail-closed startup crash-looped); engine env parity with Compose
  (`REDIS_URL`, `RATE_LIMIT_PER_MIN`, `ALLOWED_ORIGINS`, `GEMINI_API_KEY`,
  `SYNAPSE_APPLY_DB_SCHEMA` — distributed rate limiting actually activates in k8s now);
  ports/URL track `config.pythonPort`; operator `NOTES.txt` checklist. 18 offline
  contract tests (`tests/test_helm_chart.py`).
- Phase 7.3 load & resilience suite (`tests/test_load_resilience.py`, 17 offline tests):
  parallel writers/readers lose nothing; a DB interruption raises fail-fast (no fake
  success, no partial rows) and the store recovers; slow embedding providers degrade
  latency without retry storms and repeated queries hit the embed cache; worker
  crash-restart preserves every row with idempotent migrations and `integrity_check ok`;
  1000-row corpus + tenant/category matrix keep search quality and isolation; HTTP
  read/write bursts, 100-item bulk (auto-prune bound ≤ 50 per tenant), and a full store
  outage on the real gateway (ingest 500s fail-closed, `/liveness` stays DB-independent,
  instant recovery).
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
| Python SDK (`synapse-memory-engine`) | 1.2.0 | `SynapseClient`, `__version__ = "1.2.0"` |
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
