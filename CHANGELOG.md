# Changelog

## Unreleased

## 1.3.1 — release candidate 2026-10-27

- **Post-release correctness fixes (pending PyPI publication):** support the shipped
  3,072-dimensional embedding width using a pgvector-supported halfvec HNSW
  expression index and matching query expression; refuse implicit model-width
  changes when existing embeddings have not been re-embedded; add an isolated,
  synthetic-vector live integration test.
- **SDK retry safety:** retry idempotent GET/HEAD/OPTIONS requests and honour
  `Retry-After`; do not automatically replay POST requests (including retrieval,
  which updates access metadata) after ambiguous network failures or transient
  5xx responses without a proven idempotency contract.
- **Deployment diagnostics:** capture Compose logs and container state even when
  initial stack startup fails; provide semantic embedding-provider values in
  config-validation CI.
- **Release metadata:** align the client, legacy setup metadata and OpenAPI
  version with the package and expand the version-consistency gate.
- **Least-privilege migration guard:** verify the migration DSN actually uses the
  designated non-superuser role rather than the bootstrap credential.
- **Tenant input compatibility:** accept snake_case and documented camelCase
  aliases so tenant selections are not silently ignored.
- **Gateway data hygiene:** prevent production demo-memory fallbacks and duplicate
  plaintext in the gateway process-local store.
## 1.3.0 — released 2026-10-26
- **Security audit F-01â€¦F-09 remediation.**
  - **F-01 (P0, privilege escalation):** all authorization now runs through one
    canonical effective-role path (`normalize_role` â†’ `effective_role`). A stored
    `admin` role or the `admin` scope no longer implies cross-tenant access:
    `tenant_selection_allowed()` requires `platform_admin`, and `scope_satisfied()`
    treats `admin` as a memory-domain superuser only (never `auth:keys:*` or
    `security:audit`). The key-creation route now passes `requested_scopes` to
    `can_create_api_key()`, roles/scopes are closed allow-lists at mint time, and
    `generate_api_key()` refuses to persist non-canonical roles or unknown scopes.
    The create-key schema can express the canonical roles. Regression suite:
    `tests/test_f01_auth_escalation.py` (route-level, cache-miss + cache-hit).
  - **F-02:** `/liveness` is registered and DB-independent; probe contract tests in
    `tests/test_f02_f07_probes.py`.
  - **F-03:** dropped the unsatisfiable `types-cryptography>=42` dev pin
    (`cryptography` ships inline types); CI dependency install no longer fails.
  - **F-04:** clean-start is reproducible: new `db-provision` compose service
    provisions the runtime role, applies migrations as the admin role
    (`provision_db_roles.py --apply-migrations`) and verifies the invariant before
    the engine starts; Helm defaults are now the least-privilege arrangement
    (`dbUser: synapse_app`, `applyDbSchema: "false"`); quick start documents the
    four required secrets and the provisioning step.
  - **F-05:** `content_plain` is documented as a plaintext search projection
    (README no longer claims full encryption at rest); new
    `SYNAPSE_STORE_SEARCHABLE_PLAINTEXT=false` stores no plaintext at all, and
    `tests/test_f05_f06_data_at_rest.py` pins both behaviours.
  - **F-06:** the non-semantic deterministic provider is labelled as such
    (`embedding_is_semantic` on `/readiness`) and refused in production unless
    `SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING=true`.
  - **F-07:** `/readiness` performs a bounded live storage check, so a database
    lost after startup returns 503 instead of READY.
  - **F-08:** metric labels use route templates, unknown paths bucket to `other`,
    and series are TTL-pruned with a hard cardinality ceiling.
  - **F-09:** every gateway route handler is wrapped so async rejections reach a
    terminal error middleware; an unreachable engine becomes a consistent 503
    (never fabricated data) and the fetch timeout is cleared in `finally`.
- **License:** relicensed from MIT to proprietary ("All Rights Reserved",
  Copyright (c) 2026 Shivam Singh). Previously distributed MIT copies retain
  their MIT rights; new versions require permission (see `LICENSE`).
- **Security (RLS admin path):** the PostgreSQL row-level-security policy no
  longer relies on a `__synapse_admin__` GUC sentinel that any session could
  set. Policies are now gated on true role membership
  (`current_user = '<admin_role>'` with `pg_has_role` verification), and the
  tenant context is left unset for admins so the admin path is explicit and
  auditable. New live regression test:
  `test_admin_access_requires_admin_role_membership`.
- **Benchmark:** honest head-to-head benchmark harness against Mem0
  (`sdk/python/benchmark/bench_suite.py::run_mem0_benchmark`) measuring p50/p95/p99
  ingest+query latency, recall@k, and cross-tenant isolation leakage on matched
  corpora with deterministic embeddings. Skips gracefully when `mem0` is not
  installed so the suite stays dependency-optional.
  **Reference (single measured run: corpus 1,000 / needles 5 / top_k 10 / queries
  50 / seed 42, deterministic shared embedder, mem0 2.2.1):**
  synapse ingest p50 115.2 / p95 438.7 / p99 1196.1 ms (1,005 inserts; SQLite +
  Fernet encryption incl.) Â· synapse query p50 360.4 ms Â· mem0 ingest p50 12.4 /
  p95 38.4 / p99 237.5 ms (mean inflated by lazy warm-up; measured-only) Â· recall
  @10 78 % Â· MRR 0.744 Â· cross-tenant leaks 0 (both engines) Â· knapsack tokens
  saved 71.1 mean. Full record: `sdk/python/benchmark/benchmark_results.json`
  (`mem0_head_to_head` key). Honest by design: no LLM API keys, no projections,
  identical shared embeddings, no apples-to-oranges comparison (see
  `sdk/python/benchmark/README.md`).

- **Example:** complete `examples/project_aware_coding_assistant/` FastAPI app â€”
  project-scoped coding-assistant backend where every project is a Synapse-Memory
  tenant. Documents, snippets, and cross-project links live in app-owned SQLite
  tables; semantic memories go through `MemoryManager.ingest_with_deduplication`
  with `tenant_id=project.id`. Includes a 38-check `smoke_test.py` (TestClient)
  covering CRUD, dedup-merge, tenant isolation, search, and cascade deletes.
- **Packaging:** distribution renamed to `synapse-memory-engine` on PyPI (the
  `synapse-memory` name is owned by an unrelated project). The import package
  stays `synapse_memory`. Root `pyproject.toml` is now the
  `synapse-memory-workspace` tooling manifest; the published SDK lives in
  `sdk/python`. New tag-triggered publish workflow builds, fresh-venv-verifies
  and trusted-publishes the wheel (see `docs/publishing.md`).

## 1.2.0 â€” Production-hardening release (2026-10-15)
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
  now precede their consumers â€” previously the literal `$(DB_PASSWORD)` shipped as the DSN
  password and fail-closed startup crash-looped); engine env parity with Compose
  (`REDIS_URL`, `RATE_LIMIT_PER_MIN`, `ALLOWED_ORIGINS`, `GEMINI_API_KEY`,
  `SYNAPSE_APPLY_DB_SCHEMA` â€” distributed rate limiting actually activates in k8s now);
  ports/URL track `config.pythonPort`; operator `NOTES.txt` checklist. 18 offline
  contract tests (`tests/test_helm_chart.py`).
- Phase 7.3 load & resilience suite (`tests/test_load_resilience.py`, 17 offline tests):
  parallel writers/readers lose nothing; a DB interruption raises fail-fast (no fake
  success, no partial rows) and the store recovers; slow embedding providers degrade
  latency without retry storms and repeated queries hit the embed cache; worker
  crash-restart preserves every row with idempotent migrations and `integrity_check ok`;
  1000-row corpus + tenant/category matrix keep search quality and isolation; HTTP
  read/write bursts, 100-item bulk (auto-prune bound â‰¤ 50 per tenant), and a full store
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
| Helm chart | 0.1.0 (app 1.2.0) | chart version â‰  app version; see Chart.yaml |
| Schema | v4 ledger | forward-only; v4 adds `content_plain` |

### Known limitations
- Rate limiting is in-process by default; set `REDIS_URL` to enforce limits
  across all engine replicas (Redis sorted-set sliding window).
- Embeddings + tsvector are readable derivatives (field encryption covers `content`).
- Benchmarks in `bench_suite.py` include projections; `test_retrieval_eval.py` is measured-only.

## 0.1.0 â€” Initial prototype
- Pre-hardening baseline; do not use for production.
