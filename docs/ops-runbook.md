# SynapseMemory Operations Runbook (Phase 7.4)

On-call guide for the production topology: split engine + gateway, external
pgvector PostgreSQL, migrate-then-run least-privilege roles, Redis-backed rate
limiting. Every command below mirrors the real code paths.

Related: `docs/production-hardening-baseline.md`, `SECURITY.md`,
`docs/backup-recovery.md`, `deploy/.env.example`,
`deploy/helm-chart/templates/NOTES.txt`.

## 1. Topology at a glance

- **Engine** (Python FastAPI, `:8008`): storage, retrieval, auth, rate
  limiting, metrics. `sdk/python/synapse_memory/api/fastapi_server.py`.
- **Gateway** (Node + React, `:3000`): dashboard + API proxy. `server.ts`.
- **PostgreSQL + pgvector**: EXTERNAL dependency (never deployed by the Helm
  chart). Engine connects via `DATABASE_URL`; gateway never touches the DB.
- **Redis** (optional): distributed rate limiting + query cache. Without it the
  engine degrades to a per-replica in-process window — requests never fail.
- **Fail-closed defaults**: DB outage → 503 (never fake success); missing
  schema with `SYNAPSE_APPLY_DB_SCHEMA=false` → refuse to start; demo routes
  off (`ENABLE_DEMO_ROUTES=false`).

## 2. Health model — which probe means what

| Probe | Target | Meaning |
|---|---|---|
| `GET /liveness` | engine | Process alive. DB-independent — stays OK during a DB outage. K8s `livenessProbe`. |
| `GET /readiness` | engine | DB + auth usable. 503 when pgvector is down. K8s `readinessProbe`; compose `healthcheck`. |
| `GET /health` | engine | Telemetry: backend, HNSW, RLS mode, memory count, provider. 503 when DB offline. |
| `GET /metrics` | engine | Prometheus text: requests, latency p50/p95, auth/429, ingest/retrieval, pool, packed tokens, `synapse_rate_limit_backend{backend="redis"|"in-process"}`. |
| `GET /api/health` | gateway | Gateway liveness. K8s `livenessProbe`. |
| `GET /api/ready` | gateway | 503 until engine + auth are healthy. K8s `readinessProbe`; compose `healthcheck`. |

Triage order: gateway `/api/ready` 503 → engine `/readiness` → `/health`
(`storage_backend`, `row_level_security`) → `/metrics`
(`synapse_db_pool_in_use/size`, `synapse_rate_limit_rejections_total`).

Envelope: successes `{success: true, data, request_id}`; failures
`{success: false, error, request_id}`. Pass `X-Request-ID` to trace end to end.

## 3. Deploy checklist (first install + every release)

1. **Secrets.** Compose requires separate root `SYNAPSE_API_KEY`,
   `SYNAPSE_ENCRYPTION_KEY`, bootstrap-superuser `POSTGRES_PASSWORD`,
   migration-role `SYNAPSE_ADMIN_DB_PASSWORD`, and runtime-role
   `SYNAPSE_APP_DB_PASSWORD`. Supply the API key for the selected provider
   (`GEMINI_API_KEY` for gemini or `OPENAI_API_KEY` for openai). In Kubernetes,
   the runtime Secret contains only the API key, encryption key, runtime DB
   password, optional Redis password and selected provider key. Never put
   bootstrap/migration credentials in the app Secret.
2. **Database.** Provision pgvector PostgreSQL (managed service or `pgvector/pgvector:pg16`) and enable volume encryption at rest. Compose uses `POSTGRES_USER=postgres` as bootstrap, `SYNAPSE_DB_ADMIN_ROLE=synapse_admin` for schema migration, and `SYNAPSE_DB_RUNTIME_ROLE=synapse_app` for requests.
   For 3,072-dimensional Gemini embeddings, the PostgreSQL `vector` extension must be **0.7.0 or newer** for halfvec HNSW support; verify with `SELECT extversion FROM pg_extension WHERE extname = 'vector';`.

   **Existing Compose volumes from before v1.3.1:** do not delete or recreate the PostgreSQL volume to make new defaults work. Older Compose defaults initialized the bootstrap superuser as `synapse_admin`; fresh installs now default to `postgres`. PostgreSQL only applies `POSTGRES_USER` during first-time volume initialization, so changing it does not create a new superuser. Preserve the original bootstrap username/password explicitly (for the old default, set `POSTGRES_USER=synapse_admin`), set `SYNAPSE_DB_ADMIN_ROLE=synapse_migrator` to a distinct migration role, provide a separate `SYNAPSE_ADMIN_DB_PASSWORD`, and back up both the database and `SYNAPSE_ENCRYPTION_KEY` before provisioning. Bootstrap and migration role names must differ.
3. **Roles — migrate-then-run (once per cluster):**
   ```bash
   export SYNAPSE_BOOTSTRAP_DATABASE_URL="postgresql://postgres:<bootstrap-password>@<host>/<db>"
   export SYNAPSE_MIGRATION_DATABASE_URL="postgresql://synapse_admin:<migration-password>@<host>/<db>"
   export SYNAPSE_API_KEY="<generated-root-api-key>"
   export SYNAPSE_ENCRYPTION_KEY="<generated-fernet-key>"
   export SYNAPSE_ADMIN_DB_PASSWORD="<migration-password>"
   export SYNAPSE_APP_DB_PASSWORD="<runtime-password>"
   python scripts/provision_db_roles.py \
     --runtime-role synapse_app --admin-role synapse_admin \
     --runtime-password "$SYNAPSE_APP_DB_PASSWORD" \
     --admin-password "$SYNAPSE_ADMIN_DB_PASSWORD" --apply-migrations
   python scripts/provision_db_roles.py --verify-only
   ```
   The bootstrap connection installs extensions and creates roles, schema DDL
   runs as the non-superuser migration role, and only `synapse_app` serves API
   traffic. Set `SYNAPSE_APPLY_DB_SCHEMA=false` for runtime traffic.
4. **Env parity.** Production must set: `NODE_ENV=production`,
   `SYNAPSE_STORAGE_BACKEND=pgvector`, `SYNAPSE_ALLOW_DB_FALLBACK=false`,
   `ENABLE_DEMO_ROUTES=false`, `REDIS_URL` (cross-replica limits),
   `RATE_LIMIT_PER_MIN` (default 120), `ALLOWED_ORIGINS`, `TOKEN_BUDGET_MAX`
   (default 1500, cap 8000).

## 4. Day-2 operations

### 4a. API key rotation (zero downtime)

`POST /api/auth/keys/rotate {key_id}` (scope `admin`): issues a new key with
the same metadata, then revokes the old. Revocation is immediate. List keys via
`GET /api/auth/keys` (hashes only, never plaintext).

### 4b. Encryption key rotation (no silent re-encryption)

Per `docs/backup-recovery.md §5`: deploy the new key as
`SYNAPSE_ENCRYPTION_KEY_NEXT`, run the re-encryption sweep (read each row with
the old key, write with the new — batched, same pattern as
`reembed_all_memories`), promote, keep the old key for one backup cycle, then
destroy. `content_plain` is re-derived from decrypted plaintext during the
sweep. **Back up the key separately from data; key loss = unrecoverable
ciphertext** (reads raise `RuntimeError`, fail safe).

### 4c. Backup / restore

Back up all three (else restore fails): Postgres data (`pg_dump -Fc`), the
exact `SYNAPSE_ENCRYPTION_KEY`, and the `schema_migrations` ledger (rides
inside the dump — expect v4: `SELECT version FROM schema_migrations;`).
Drill before every release:
`python scripts/backup_restore_drill.py` (SQLite, anywhere) and
`python scripts/backup_restore_drill.py --postgres` (+ client tools).
Exit `0` = pass, `1` = failed check, `2` = nothing runnable (never a silent
pass). Retention: `clear_memories(tenant_id)` / GDPR delete
(`POST /api/security/gdpr-delete`) delete only that tenant's rows and return
the real `deleted_count`; no background hard-delete job ships — retention
windows are operator policy.

### 4d. Rate limiting

`RATE_LIMIT_PER_MIN` (default 120, window `RATE_LIMIT_WINDOW_SECONDS` default
60s) covers mutating + heavy retrieval paths (`/ingest`, `/api/memories*`,
`/query`, `/api/retrieval*`); rejections are HTTP 429 and increment
`synapse_rate_limit_rejections_total`. With reachable `REDIS_URL`, a Redis
sorted-set Lua sliding window enforces limits across ALL replicas; on Redis
outage the engine falls back to the in-process window (cluster-wide degrades
to per-replica — never a full outage). Confirm via
`synapse_rate_limit_backend{backend="redis"|"in-process"}` in `/metrics`.

### 4e. Audit + PII

`GET /api/auth/audit?limit=` (tenant-scoped; admins use the `*` wildcard),
`GET /api/auth/audit/export?since=&limit=` (bounded ≤20000, SIEM-ready),
`POST /api/auth/audit/prune {"older_than_days": 90}` (default 90d). Auth
failures return generic `"Authentication failed."` (detail stays in the audit
log). Scrub untrusted prompts via `POST /api/security/scrub` before indexing.

## 5. Incident playbooks

| Symptom | Likely cause | Action |
|---|---|---|
| Engine boot crash-loop, `SYNAPSE_APPLY_DB_SCHEMA=false` + missing `memories` | Schema never migrated as migration role | Run §3 step 3 as `synapse_admin`, re-verify, restart. Never flip to `SYNAPSE_ALLOW_DB_FALLBACK=true` in prod (silent in-memory = data loss). |
| `GET /readiness` 503, `/liveness` OK | pgvector down / DSN wrong / pool exhausted | Check `/health` reason, `DATABASE_URL` secret, `synapse_db_pool_in_use/size` in `/metrics`; failover DB, restart engine. Gateway `/api/ready` stays 503 by design until engine recovers. |
| Literal `$(DB_PASSWORD)` in DSN / auth failures after Helm deploy | Env ordering edited (kubelet expands only earlier vars) | `DB_PASSWORD` must precede `DATABASE_URL`; `REDIS_PASSWORD` before `REDIS_URL` in `deployment.yaml`. Restore ordering, redeploy. |
| Gateway 404 `{simulated: true}` on infra/licensing/dreaming/graph routes | Expected: demo routes gated off | Not an incident. Enable locally only with `ENABLE_DEMO_ROUTES=true`. |
| 429 spike on one tenant | Key-level burst (limit is per key+path) | Check `synapse_rate_limit_rejections_total{...}`; confirm Redis backend active for multi-replica; raise `RATE_LIMIT_PER_MIN` or re-key the workload. |
| Cross-tenant read suspicion | App bug or leaked runtime DSN (known RLS limitation) | Rotate keys (§4a); audit `GET /api/auth/audit`; verify `resolve_tenant` rejects mismatches (403 `Tenant mismatch`); treat runtime DSN as tenant-data-sensitive per `SECURITY.md`. Emergency: revoke keys, fence the DB network, restore from §4c backup. |

## 6. Scaling notes

- Engine/gateway scale independently (separate Deployments; HPA
  `minReplicas 3 / maxReplicas 15`, CPU+memory 80% in `values.yaml`).
- Multi-replica rate limiting REQUIRES `REDIS_URL` — without it each replica
  enforces its own window (effective limit = `RATE_LIMIT_PER_MIN` × replicas).
- Knapsack budget: `TOKEN_BUDGET_MAX` (cap 8000); bulk ingest ≤100 items /
  500KB per call; list endpoints paginate (`limit` ≤200).
- Load/resilience evidence: `sdk/python/tests/test_load_resilience.py`
  (17 offline tests — concurrency, DB outage fail-fast + recovery, slow
  provider, restart durability, 1000-row/100-item bulk). Re-run before
  capacity changes.

5. **Verify.** Engine `GET /readiness` → `READY`; gateway `GET /api/ready` →
   200; `GET /metrics` shows `synapse_up 1` and the expected
   `synapse_rate_limit_backend{backend="redis"}` (or `"in-process"`).
