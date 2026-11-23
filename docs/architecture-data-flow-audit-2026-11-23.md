# Architecture and Data-Flow Audit

**Audit date:** 2026-11-23  
**Repository ref:** `main` at `adbe273e98f6221e33c1cb318352ed03a9171799`  
**Scope:** Python SDK, FastAPI engine, SQLite/PostgreSQL storage, authentication/audit, memory retrieval, agent-run telemetry, migrations, and the existing React dashboard.

> This is a source-code inspection, not an independent penetration test, deployment review, or compliance assessment. Statements below distinguish implemented behavior from recommendations and items that still require runtime verification.

## 1. Executive summary

The repository already contains substantially more than the earlier planning documents describe. In particular, the current main branch has a versioned, tenant-scoped agent-run trace store, API endpoints for run/event lifecycle, metadata allow-listing, idempotent event replay, PostgreSQL row-level security, and corresponding tests. The roadmap's older statement that no end-to-end run ledger exists is therefore stale and should be updated.

The main architectural layers are:

1. **SDK/runtime:** memory ingestion, retrieval and lifecycle management; optional `SynapseRun` execution guard and event model.
2. **API:** FastAPI authentication/scope enforcement, tenant resolution, memory and agent-run endpoints, rate limiting, request metrics.
3. **Persistence:** SQLite for local/small deployments; PostgreSQL + pgvector for production-oriented vector search, with forward-only migrations and RLS.
4. **Presentation/operations:** Node gateway and React dashboard, deployment manifests, Prometheus/Grafana configuration, and operational documentation.

The most important unresolved data-protection concern confirmed in source is that PostgreSQL's lexical-search projection (`content_plain`) and its derived full-text index are plaintext by design when `SYNAPSE_STORE_SEARCHABLE_PLAINTEXT=true` (the documented default). The authoritative `content` field is encrypted, but this is not equivalent to encrypting all memory-derived data.

## 2. High-level architecture

```text
Application / Agent
  |-- Python SDK: MemoryManager / SynapseRun
  |       |-- Embedder + hybrid retrieval
  |       |-- SQLiteMemoryStore OR PGVectorMemoryStore
  |       |-- SynapseRun metadata events (in-process)
  |
  |-- HTTP client
          |
          v
FastAPI engine (fastapi_server.py)
  |-- API-key validation + scope checks
  |-- resolve_tenant()
  |-- memory / retrieval endpoints
  |-- /api/agent-runs endpoints
  |-- rate limiting + request metrics
          |
          v
RunTraceStore + Memory Store
  |-- agent_runs / agent_run_events
  |-- memories + provenance / supersession
  |-- auth keys + security audit records
          |
          v
PostgreSQL + pgvector (RLS) OR SQLite

Node gateway / React dashboard
  |-- proxies configured engine URL
  |-- displays memory and run-inspection views
```

This diagram describes repository boundaries; it does not claim that every SDK call is automatically traced. `SynapseRun` is opt-in and guards only calls explicitly routed through it.

## 3. Request, identity, and tenant flow

### API authentication

- `require_scope()` extracts the API key from `X-Synapse-API-Key` or Authorization bearer input.
- `SecurityManager.validate_api_key()` validates the key and required scope; failures return a generic authentication response.
- `resolve_tenant()` binds ordinary principals to their own tenant. Cross-tenant selection is reserved for the canonical `platform_admin` role; a legacy `admin` scope alone does not widen tenant access.
- Route dependencies use distinct scopes, including `memories:read/write/delete`, `agent-runs:read/write/delete`, and administrative scopes.

**Evidence:** `sdk/python/synapse_memory/api/fastapi_server.py` (`require_scope`, `resolve_tenant`, memory and agent-run routes); `sdk/python/synapse_memory/core/auth.py` (role normalization, delegation and key validation).

### Storage tenant boundary

- Application queries supply the effective tenant explicitly.
- PostgreSQL migrations enable and force RLS on both `agent_runs` and `agent_run_events`, with policies bound to `app.current_tenant`.
- The run-trace store sets tenant context for PostgreSQL sessions and includes tenant predicates in queries.
- SQLite uses tenant-scoped keys/queries at the application layer; it does not provide PostgreSQL-style RLS.

**Verification still required:** run the live PostgreSQL integration suite using the actual least-privilege runtime role and confirm both trace tables are covered in that CI job, not only memory rows.

## 4. Memory data lifecycle and storage

### Ingestion and retrieval

The FastAPI engine constructs an embedder and `MemoryManager`, then selects SQLite or PostgreSQL based on configuration. The manager performs embedding, tenant-scoped deduplication/supersession, insertion and capacity pruning. Retrieval combines lexical/dense retrieval, temporal eligibility, decay and token-budget packing.

Provenance metadata includes source type/ID, observation and validity timestamps, caller-declared trust level, supersession lineage and retraction fields. Trust elevation is separately scoped; a trust label is not independent verification.

### At-rest representation

| Data | PostgreSQL behavior | SQLite behavior / note |
|---|---|---|
| Authoritative memory content | Fernet-encrypted in `content` | Fernet-encrypted in `content` |
| Embedding | Stored in vector column for search; inspect deployment/database encryption separately | Encrypted serialized embedding in the inspected insert path |
| Lexical search projection | `content_plain` is plaintext by default when searchable plaintext is enabled; can be disabled, sacrificing lexical/FTS matches | Backend-specific schema and query path; do not assume PostgreSQL's projection behavior applies |
| Full-text index | `search_vector` is derived from plaintext searchable content | No equivalent PostgreSQL tsvector/RLS guarantee |
| Provenance and identifiers | Metadata columns; may reveal relationships and activity | Metadata columns; may reveal relationships and activity |

**Do not claim that all stored data is encrypted.** Database/volume encryption, backups, key custody and access control remain operator responsibilities. Disabling PostgreSQL searchable plaintext reduces lexical search capability; it does not remove the need to review embeddings, metadata, logs and backups.

## 5. Agent-run execution and telemetry

### Runtime contract

`SynapseRun` is an opt-in synchronous guard. It records metadata-only events and supports step/tool-call limits, cooperative deadlines, repeated-failure detection, retry policy and deterministic postcondition checks. It cannot intercept arbitrary framework calls or forcibly interrupt a synchronous tool that is already running.

### Persistence and API

- `POST /api/agent-runs` creates a tenant-bound run.
- `POST /api/agent-runs/{run_id}/events` appends versioned events.
- `GET /api/agent-runs` and run/event detail routes provide bounded reads.
- `DELETE /api/agent-runs/{run_id}` deletes a run; event rows cascade from the run.
- The API requires separate `agent-runs:read/write/delete` scopes.
- Migration v6 creates the two trace tables, tenant-scoped keys, event uniqueness and indexes; PostgreSQL adds forced RLS.
- The store allow-lists event fields and retrieval-evidence fields, rejects arbitrary payload keys, validates IDs/timestamps/scores, caps retrieval evidence at 100 records and serialized retrieval metadata at 64 KiB, and makes event-ID replay idempotent while rejecting conflicting replays.

**Important boundary:** the run/event API and store are implemented, but callers must explicitly upload events. The source does not justify a claim that all agent framework activity is automatically captured.

### Trace content and privacy

The persistence contract excludes prompts, raw tool arguments/results, memory content and arbitrary payloads. Retrieval evidence stores identifiers, ranks, scores, token cost, packed status and selected provenance metadata. These fields are still potentially sensitive metadata: memory IDs, tool names, tenant IDs and timestamps can reveal workflow structure.

**Recommended default:** redact payloads at source; persist metadata only; avoid customer identifiers in tool names and source IDs; keep trace access tenant-scoped and audited; document deletion semantics. For an initial pilot, adopt a configurable **30-day maximum retention** for run metadata, with shorter customer-configured windows where appropriate. This is a recommendation, not an implemented automatic expiry policy. The inspected API exposes explicit deletion; no scheduled trace-retention worker was identified.

## 6. Logs, metrics, and security audit

Three distinct mechanisms exist and should not be conflated:

1. **Application structured logs:** `core/observability.py` emits JSON events and wraps operations with duration/failure logging. The ingestion decorator logs `str(exception)`; this can expose sensitive exception text if upstream exceptions contain user data, identifiers or secrets. Treat this as a redaction review item.
2. **Operational HTTP metrics:** FastAPI tracks bounded route/status request counts, latency samples, auth failures, rate-limit outcomes and memory operation counters. These are operational aggregates, not a durable event ledger.
3. **Security audit records:** the security manager records key/auth/security actions; admin endpoints expose reads/exports and pruning. The API includes an audit-prune path with a documented default 90-day window. This is distinct from agent-run trace retention.

The current memory manager also emits structured deduplication and pruning events with tenant and memory identifiers. Review logger configuration, retention and access in each deployment; source-level metadata minimization does not by itself guarantee safe log sinks.

## 7. Schema, migration, and rollback seams

- Schema changes are versioned and forward-only through `core/migrations.py`; v6 adds agent-run and event tables.
- PostgreSQL supports a migrate-then-run topology: a migration role applies DDL, while a least-privilege runtime role validates and uses the existing schema.
- The memory store has explicit encryption/searchability trade-offs and database role assumptions; these must be preserved in future migrations.
- Trace deletion is relationally coupled through a foreign key with `ON DELETE CASCADE`.
- No automatic reverse migration is defined. Rollback planning should use a tested backup/restore procedure and a release-specific compatibility plan rather than ad-hoc destructive DDL.

**Before any new trace schema change:** update SQLite and PostgreSQL migrations together, add migration/idempotency tests, verify RLS under the real runtime role, and test upgrade from the oldest supported schema plus restore behavior.

## 8. Existing vs. missing vs. unverified

| Area | Status | Evidence / remaining work |
|---|---|---|
| API-key authentication and scopes | Implemented | Auth dependency, role/scope tests; re-run current CI |
| Tenant resolution | Implemented | Central resolver + scoped store queries |
| PostgreSQL RLS for memory and run traces | Implemented in migrations | Verify live runtime-role CI coverage for both trace tables |
| Memory encryption | Partially implemented by design | Authoritative content encrypted; searchable derivatives may be plaintext |
| Provenance and temporal validity | Implemented | Schema v7 docs and retrieval tests |
| Agent run guard | Implemented, opt-in | `runtime.py`; cooperative timeout only |
| Durable run/event ledger | Implemented | `RunTraceStore`, API routes, migration v6 and tests |
| Payload redaction | Implemented at trace-store contract | Does not automatically sanitize general application logs |
| Trace retention | Manual deletion endpoint | No scheduled expiry worker identified; define and implement policy |
| Security audit retention | Prune endpoint exists | Verify deployment scheduling and policy configuration |
| Full production security assurance | Not established | Independent review/penetration test and deployment-specific checks remain outstanding |
| Automatic framework-wide tracing | Not implemented / not claimed | Only explicitly instrumented `SynapseRun` activity is guarded |

## 9. Recommended next actions

1. Correct stale statements in `docs/agent-reliability-product-roadmap.md` that say the run ledger/trace contract does not exist.
2. Add a focused test proving PostgreSQL RLS isolation for both trace tables using the provisioned non-owner runtime role.
3. Review and redact exception text in `instrument_operation` before it reaches logs; add tests with synthetic secret-bearing exceptions.
4. Specify and implement configurable trace retention (initial recommendation: maximum 30 days), including safe batch deletion and observability.
5. Document the exact searchable-plaintext, embedding, backup and log-sink trade-offs in operator-facing security guidance.
6. Verify gateway/API integration and deployment configuration against the current main branch; do not infer it from the README alone.

## 10. Source files inspected

- `sdk/python/synapse_memory/api/fastapi_server.py`
- `sdk/python/synapse_memory/runtime.py`
- `sdk/python/synapse_memory/core/auth.py`
- `sdk/python/synapse_memory/core/run_trace_store.py`
- `sdk/python/synapse_memory/core/observability.py`
- `sdk/python/synapse_memory/core/memory_manager.py`
- `sdk/python/synapse_memory/core/pgvector_db.py`
- `sdk/python/synapse_memory/core/sqlite_db.py`
- `sdk/python/synapse_memory/core/migrations.py`
- `sdk/python/tests/test_run_trace_store.py`
- `docs/security-review.md`
- `docs/memory-provenance.md`
- `docs/production-hardening-baseline.md`
- `docs/agent-reliability-product-roadmap.md`
