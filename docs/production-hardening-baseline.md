# Production-Hardening Baseline Report

> **Current-status correction — 2026-10-27:** this file began as a historical hardening log. Its earlier “all closed” and “main GREEN” entries are not a current release sign-off. The published SDK v1.3.0 exists, but the latest reviewed `main` CI run ([40322653869](https://github.com/721189/Synapse-Memory/actions/runs/40322653869), commit `0dc97c9`) failed the Compose deployment smoke and split-image Compose config validation. A follow-up branch is addressing high-dimensional HNSW indexing, strict schema verification, bootstrap extension/schema creation, deployment diagnostics, safe SDK retries, and release metadata. Do not infer production readiness until the full CI and fresh-deployment smoke pass.

- Branch: `production-hardening`
- Commits: `576f05b` (base) → `996dfc6` (P0) → `93e44c3` (P1/P2 batch)
- Date (UTC): 2026-10-14

## Test evidence
- `PYTHONPATH=sdk/python pytest sdk/python/tests -q`
  - Base: 54 → P0: 61 → P1/P2: 70 → rate-limit: 90 → P0-5 runtime-role: 115 → P1-2 Helm: 133 →
    **Phase 7.3 load/resilience: 150 passed, 14 skipped**
  - New: 3 decay-composition + 4 RRF/encryption + 7 validation/migration/metrics + 14 rate-limiter
    + 19 least-privilege role-provisioning + 7 runtime-role RLS isolation + 18 Helm chart
    + 17 load/resilience tests
- The 14 skips are environment-gated: 7 Postgres integration tests without `DATABASE_URL`, and the
  7 new runtime-role isolation tests without `SYNAPSE_APP_DATABASE_URL` (both run in the CI
  `python-pgvector-integration-tests` job).
- `flake8 sdk/python/synapse_memory scripts` → clean. `mypy` → clean (26 files).
  `bandit sdk/python/synapse_memory scripts` → clean.
- Gateway `npm run lint/build`, `docker build` (engine/gateway), Compose smoke, Helm render: covered by new CI jobs; need a green CI run to close Phase 1 fully.

## Fixes applied (Phases 1–4, 7–8 partial)
- P0-1 RRF COALESCE + lexical param-count fix.
- P0-2 combined-protection field encryption (`content` ciphertext + `content_plain` search projection); v4 migration backfills legacy rows only.
- P0-3 gateway fail-closed (liveness vs readiness, no fake successes, no demo mixing in prod).
- P0-4 `S=R*D` decay composition preserved through `/query`.
- P0-5 Proven with the actual runtime role: `scripts/provision_db_roles.py` creates/repairs
  a least-privilege DML-only runtime role (NON-owner, NOBYPASSRLS, NOSUPERUSER, no schema
  CREATE) plus a migration role, and exits non-zero unless the invariant verifies. Engine
  gained the "migrate-then-run" topology (`apply_schema=False` / `SYNAPSE_APPLY_DB_SCHEMA`)
  and now refuses to start when the schema is missing instead of degrading to in-memory.
  New tests: `tests/test_db_role_provisioning.py` (19 offline contract tests) and
  `tests/test_pgvector_integration.py::TestRuntimeRoleIsolation` (7 live tests, run in CI as
  the real runtime role). CI pgvector job now provisions roles → migrates as the migration
  role → re-provisions (idempotency) → `--verify-only` gate → runs the suite with both DSNs.
- P0-6 `ThreadedConnectionPool` + timeout + rollback-on-return; `pool_stats()` for metrics.
- P1-1 gateway image copies `demo/`; compose healthchecks on `/readiness` + `/api/ready`.
- P1-2 Helm split engine/gateway workloads + services; in-cluster engine URL; DATABASE_URL moved out of ConfigMap into container env; resource blocks per workload.
- P1-3 strict Pydantic validation on all schemas; generic auth-failure messages.
- P1-4 migration v4 + idempotency test.
- P2-3 live `/metrics` (requests, latency p50/p95, auth/429, ingest/retrieval, pool, packed tokens).
- P2-4 CI: split-image build + Trivy, Helm lint/template/parse, TruffleHog secret scan.

## Threat model (P0-2 decision)
| Layer | Protection | Notes |
|---|---|---|
| Volume / managed Postgres encryption | Protects everything at rest (ciphertext, embeddings, tsvector) | Must be enabled by operator; verify with provider |
| Field-level (`content` Fernet) | Authoritative content unreadable without `SYNAPSE_ENCRYPTION_KEY` | Key via env/file-mount/KMS; never logged/returned; rotation via re-encryption sweep (see `docs/backup-recovery.md`) |
| Searchable derivatives | `content_plain`, `search_vector` tsvector, `embedding` HNSW remain readable by design | Required for BM25 + vector search; do NOT claim "all stored data encrypted" |
| Key loss | Reads raise `RuntimeError`, fail safe | Backup must include key custody; unrecoverable without key |

## Historical closure checklist (not a current production sign-off)
1. ~~Live pgvector integration suite~~ → GREEN: `python-pgvector-integration-tests` CI job passes on `main` (run #42, commit `dfd073a` → `2f8f32a` on main). `query_candidates` added to `PGVectorMemoryStore`; `lexical_search` psycopg2 param order fixed; runtime-role RLS isolation tests pass through the real runtime DSN.
2. ~~npm/docker/helm binary validation~~ → GREEN: npm `lint`/`build` pass locally; `docker build` (engine + gateway images) and `helm template`/lint pass in CI. CI-only binaries (no local daemon/helm) — covered by split-image + Helm CI jobs.
3. ~~Migration idempotency + backup/restore drill~~ → DONE: v4 idempotency test + executable drill `scripts/backup_restore_drill.py` (SQLite verified locally); Postgres path gated on client tools.
4. ~~Rate-limit distribution~~ → DONE: `core/rate_limiter.py` (Redis sorted-set sliding window + in-process fallback); `REDIS_URL` wired in compose/Helm/`.env.example`; `/metrics` exposes `synapse_rate_limit_backend`; 14 unit tests.
5. SDK publish + quickstart (P2-1) → a `SynapseClient` exists and SDK v1.3.0 was published on 2026-10-26; current client/engine release metadata is being aligned and a follow-up patch remains unpublished.
6. ~~Benchmark corpus (P2-2)~~ → DONE (`tests/test_retrieval_eval.py`, measured-only bench script).
7. ~~Release artifacts (P2-4)~~ → DONE (CHANGELOG, version map, SECURITY update).
8. ~~P0-5 runtime-role proof~~ → DONE (`scripts/provision_db_roles.py`, `test_db_role_provisioning.py` 19 offline contract tests, `TestRuntimeRoleIsolation` 7 live tests). **Fixed in v1.3.0:** RLS admin path is now role-gated via `current_user = '<admin_role>'` inside `tenant_isolation_policy`; settable `app.*` GUCs can no longer bypass RLS.
9. ~~Phase 7.3 load/resilience tests~~ → DONE: `tests/test_load_resilience.py` (17 offline tests). Phase 7.4 ops runbook → DONE (`docs/ops-runbook.md`).
10. ~~Helm `Chart.yaml` inert `postgresql:` values subtree~~ → DONE (P1-2 residual): external PostgreSQL dependency, 18 offline Helm contract tests (`tests/test_helm_chart.py`).

## New artifacts (this finalization batch)
- `docs/api-reference.md` — OWASP API9 canonical inventory: every route, alias, scope, schema bound, error code, pagination, rate limits, retry guidance.
- `docs/security-review.md` — OWASP API Security Top 10 (2023) assessment with controls + residuals per category (API1–API10).
- `docs/branch-protection.md` — required GitHub branch-protection rule settings for `main` + `production-hardening`.
- `docs/performance-baseline.md` — measured local baseline (SQLite) + 5-step process to set production SLOs; no hardcoded p95/p99 promises.
- `CODEOWNERS` — `@721189` owns all paths; feeds CODEOWNERS review in branch protection.

## Historical CI status

The green results below refer to the historical finalization run from October 2026 and must not be used as a current badge. The latest main run reviewed on 2026-10-27 is recorded in the status correction at the top of this document. Current CI and a fresh-start deployment smoke are release gates.

