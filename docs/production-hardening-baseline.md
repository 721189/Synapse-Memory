# Production-Hardening Baseline Report

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

## Remaining gaps → status
1. Live pgvector integration suite → CI job exists; needs green run.
2. npm/docker/helm binary validation → npm `lint` (tsc --noEmit) and `build` (vite + esbuild) PASS locally; `docker build` + `helm template` remain CI-only (no local daemon/helm). Needs the green CI run to close.
3. ~~Migration idempotency + backup/restore drill~~ → DONE: v4 idempotency test + executable drill `scripts/backup_restore_drill.py` (SQLite verified locally: rows/ledger/wrong-key fail-safe; Postgres path gated on client tools); 5 tests.
4. ~~Rate-limit distribution~~ → DONE: `core/rate_limiter.py` (Redis sorted-set sliding window + in-process fallback); `REDIS_URL` wired in compose/Helm/`.env.example`; `/metrics` exposes `synapse_rate_limit_backend`; 14 unit tests.
5. SDK publish + quickstart (P2-1) → DONE this batch (`SynapseClient`, versioned 1.2.0, quickstart doc).
6. Benchmark corpus (P2-2) → DONE this batch (`tests/test_retrieval_eval.py`, measured-only bench script).
7. Release artifacts (P2-4) → DONE this batch (CHANGELOG, version map, SECURITY update).
8. ~~P0-5 runtime-role proof~~ → DONE this batch (see above). **Residual (tracked, not fixed):**
   the RLS admin path is a session-GUC sentinel, so a process holding the runtime DSN can set
   `app.current_tenant='__synapse_admin__'` and bypass RLS at the database layer. The application
   layer blocks it (`resolve_tenant`), but RLS alone does not contain a compromised app process.
   Fix = role-gated admin path inside the policy; deferred because it cannot be verified without a
   live PostgreSQL server in this environment. Documented in `SECURITY.md`.
9. ~~Phase 7.3 load/resilience tests~~ → DONE this batch: `tests/test_load_resilience.py`
   (17 offline tests — concurrency, DB interruption, slow provider, worker/DB restart,
   large batches, incl. HTTP bursts / 100-item bulk / gateway outage via TestClient).
   Phase 7.4 ops runbook → DONE (see docs/ops-runbook.md).
10. ~~Helm `Chart.yaml` inert `postgresql:` values subtree~~ → DONE this batch (P1-2 residual):
    PostgreSQL is now a documented EXTERNAL dependency (values subtree removed, operator
    checklist in `values.yaml` + `templates/NOTES.txt`, `applyDbSchema` toggle); gateway
    `PYTHON_ENGINE_URL` derived from the release fullname + `config.pythonPort`; kubelet
    `$(VAR)` expansion ordering fixed (`DB_PASSWORD`→`DATABASE_URL`, `REDIS_PASSWORD`→`REDIS_URL`);
    env parity with compose (`REDIS_URL`, `RATE_LIMIT_PER_MIN`, `ALLOWED_ORIGINS`,
    `GEMINI_API_KEY`, `SYNAPSE_APPLY_DB_SCHEMA`). 18 offline contract tests
    (`tests/test_helm_chart.py`); `bandit`/`flake8`/`mypy`/`pytest` all clean (133 passed, 14 skipped).

