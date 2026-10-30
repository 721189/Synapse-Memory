# Synapse Memory API Contract

**Engine version:** 1.2.0 — **HTTP API compat:** v1
**Base URL:** `http://<host>:8008` (engine); the gateway proxies `/api/*` to it.
**Auth:** `X-Synapse-API-Key` header (or `Authorization: Bearer <key>`) on all
endpoints except the unauthenticated telemetry probes (`/liveness`, `/readiness`,
`/health`, `/metrics`). Optional `X-Tenant-ID` header participates in tenant
resolution. Optional `X-Request-ID` is echoed back for end-to-end tracing.
**Success envelope:** `{"success": true, "data": {...}, "request_id": "<id>"}`
**Error envelope:** `{"success": false, "error": "<detail>", "request_id": "<id>"}` —
auth failures always return generic `"Authentication failed."` (HTTP 403);
detail stays in the audit log.

## Canonical vs alias paths

| Canonical path | Aliases | Method |
|---|---|---|
| `/api/memories` | `/api/memories/list` | GET |
| `/api/memories/create` | `/ingest`, `/api/memories/add` | POST |
| `/api/memories/query` | `/query`, `/api/retrieval/knapsack-pack` | POST |
| `/api/security/scrub` | `/api/security/scrub-pii` | POST |

Canonical paths are the documented interface; aliases are stable
compatibility shims returning identical envelopes.

## Permission matrix

| Endpoint | Required scope | Notes |
|---|---|---|
| `GET /liveness`, `GET /readiness`, `GET /health`, `GET /metrics` | none | Unauthenticated probes |
| `GET /api/memories` (+ alias) | `memories:read` | Tenant-scoped list |
| `POST /ingest`, `/api/memories/create`, `/api/memories/add` | `memories:write` | 201 on success |
| `POST /api/memories/bulk` | `memories:write` | max 100 items, 500KB text |
| `POST /query`, `/api/memories/query`, `/api/retrieval/knapsack-pack` | `memories:read` | Retrieval + packing; optional run correlation also requires `agent-runs:write` |
| `POST /feedback` | `memories:write` | Reinforcement signal |
| `DELETE /api/memories/{memory_id}` | `memories:delete` | Hard delete; ownership enforced |
| `POST /api/memories/{memory_id}/retract` | `memories:delete` | Soft-retract evidence; retains provenance; audited |
| `POST /api/memories/clear` | `memories:delete` | Real `deleted_count`; audited |
| `POST /api/security/gdpr-delete` | `memories:delete` | Right-to-erasure; audited |
| `POST /api/security/scrub` (+ alias) | `memories:read` | PII scrubber |
| `POST /api/auth/keys/create` | `admin` | Secret returned once |
| `GET /api/auth/keys` | `admin` | Never exposes secrets |
| `POST /api/auth/keys/revoke` | `admin` | Immediate effect |
| `POST /api/auth/keys/rotate` | `admin` | Revoke old + issue new |
| `GET /api/auth/audit` | `admin` | `limit` capped at 200 |
| `GET /api/auth/audit/export` | `admin` | `limit` capped at 20000 |
| `POST /api/auth/audit/prune` | `admin` | Body `{"older_than_days": 90}` |

Tenant resolution: non-admin principals are bound to their own `tenant_id` —
a mismatch is HTTP 403 `"Tenant mismatch."`. Admin principals must pass an
explicit `tenant_id` for cross-tenant ops (HTTP 400 otherwise). A dev-open
bypass exists ONLY for a fully unconfigured local setup (not production, no
`SYNAPSE_API_KEY`, no keys provisioned); once any auth is configured,
anonymous requests are 403.

## Agent run traces and retrieval provenance

Trace endpoints use explicit scopes. The legacy admin scope does not imply agent-trace access.

| Endpoint | Required scope | Behavior |
|---|---|---|
| POST /api/agent-runs | agent-runs:write | Create a run summary; idempotent for the same tenant/run ID |
| POST /api/agent-runs/{run_id}/events | agent-runs:write | Append one versioned event; reusing the same event ID and payload is idempotent |
| GET /api/agent-runs | agent-runs:read | List a bounded, tenant-scoped page of summaries |
| GET /api/agent-runs/{run_id} | agent-runs:read | Read one run summary |
| GET /api/agent-runs/{run_id}/events | agent-runs:read | Read chronological metadata-only events |
| DELETE /api/agent-runs/{run_id} | agent-runs:delete | Delete the run and its events for this tenant |

IDs run_id and event_id must match the pattern ^[A-Za-z0-9._:-]{1,128}$. Supported schema version is 1. Run creation accepts run_id, schema_version, and optional tenant_id/tenantId; normal principals remain bound to their key's tenant via resolve_tenant().

The event contract includes event_id, run_id, schema_version, event_type, timezone-aware timestamp, and run_status, plus optional step/tool metadata, a 64-character hexadecimal argument fingerprint, a short reason/error type, and retrieval metadata. Supported event types are run.started, step.started, tool_call.started, tool_call.succeeded, tool_call.failed, tool_call.blocked, run.limit_exceeded, run.finished, and memory.retrieval.

For memory.retrieval, provide a retrieval_id and up to 100 memory_evidence entries. Each entry supports memory_id, positive rank, allowlisted numeric score_components, non-negative token_cost, boolean packed, optional created_at, supersedes/superseded_by, source classification and opaque source ID, observed/validity timestamps, trust classification, and controlled supersession/retraction reason codes. The event may also include retrieval_as_of. The persistence layer rejects arbitrary evidence fields; prompts, queries, raw tool arguments, tool results, and memory content are not stored. A /query request with run_id automatically appends one memory.retrieval event and requires both memories:read and agent-runs:write; query calls without run_id remain unlinked and applications may continue to instrument events manually.

Event sequence numbers are assigned transactionally per run. Duplicate event IDs are accepted only when the persisted event fields match; conflicting reuse returns HTTP 409. Missing runs return 404; invalid payloads return 422; database failures return 503. Event listing defaults to 100 and caps at 500; run listing defaults to 50 and caps at 100. The API rate limiter uses a normalized run-route bucket so arbitrary run IDs do not create unlimited rate-limit entries.

On PostgreSQL, agent_runs and agent_run_events have ENABLE/FORCE RLS policies tied to app.current_tenant; every SQL operation also includes an explicit tenant predicate. SQLite uses tenant predicates in every operation. Schema migration v6 creates these tables. Apply migrations before booting a least-privilege PostgreSQL runtime role.

## Telemetry (no auth)

### GET /liveness
Process liveness, DB-independent. `{"status": "OK", "timestamp": <float>}`.
K8s `livenessProbe`. Stays OK during a DB outage by design.

### GET /readiness
DB + auth usability. `{"status": "READY", ...}` or HTTP 503 with `reason`.
K8s `readinessProbe`; compose `healthcheck`.

### GET /health
Service telemetry: `storage_backend`, `hnsw_enabled`, `row_level_security`
(`ENFORCED` on pgvector, `TENANT_INDEXED` on sqlite),
`total_indexed_memories`, `embedding_provider`, `engine_budget_solver`,
`auth_engine`. HTTP 503 when the DB is offline.

### GET /metrics
Prometheus text: `synapse_requests_total{path,status}`,
`synapse_request_latency_seconds{path,quantile}` (+ sum/count),
`synapse_auth_failures_total`, `synapse_rate_limit_rejections_total`,
`synapse_ingest_total{outcome}`, `synapse_retrieval_total{outcome}`,
`synapse_retrieval_candidates`, `synapse_context_tokens_packed`,
`synapse_memories_total{backend}`, `synapse_db_pool_in_use/size`,
`synapse_rate_limit_backend{backend}`, `synapse_up`. All values live.

## Memory operations

### GET /api/memories (alias: /api/memories/list)
Scope: `memories:read`. Tenant-scoped paginated list.

| Param | Default | Constraints |
|---|---|---|
| `tenant_id` | principal tenant | `^[A-Za-z0-9_\-\.]+$` |
| `category` | — | `^[a-z0-9_\-]+$` |
| `limit` | 50 | clamped 1–200 |
| `offset` | 0 | ≥ 0 |

Response `data`: `{memories, tenant_id, total, limit, offset,
storage_backend}`.

### POST /api/memories/create (aliases: /ingest, /api/memories/add)
Scope: `memories:write`. → HTTP 201. Tenant isolation, field-level
encryption, semantic deduplication, token-cost calculation.

| Field | Required | Default | Constraints |
|---|---|---|---|
| `content` | Yes | — | 1–32000 chars, non-blank |
| `category` | No | `"interaction"` | `^[a-z0-9_\-]+$`, ≤64 chars |
| `confidence` | No | 0.95 | 0.0–1.0 |
| `tenant_id` (`tenantId` alias) | No | from principal | tenant pattern, ≤128 chars |
| `source_type` | No | `unspecified` | `unspecified|user|tool|document|system|import` |
| `source_id` | No | null | 1–256 character opaque ID, `^[A-Za-z0-9._:-]+# Synapse Memory API Contract

**Engine version:** 1.2.0 — **HTTP API compat:** v1
**Base URL:** `http://<host>:8008` (engine); the gateway proxies `/api/*` to it.
**Auth:** `X-Synapse-API-Key` header (or `Authorization: Bearer <key>`) on all
endpoints except the unauthenticated telemetry probes (`/liveness`, `/readiness`,
`/health`, `/metrics`). Optional `X-Tenant-ID` header participates in tenant
resolution. Optional `X-Request-ID` is echoed back for end-to-end tracing.
**Success envelope:** `{"success": true, "data": {...}, "request_id": "<id>"}`
**Error envelope:** `{"success": false, "error": "<detail>", "request_id": "<id>"}` —
auth failures always return generic `"Authentication failed."` (HTTP 403);
detail stays in the audit log.

## Canonical vs alias paths

| Canonical path | Aliases | Method |
|---|---|---|
| `/api/memories` | `/api/memories/list` | GET |
| `/api/memories/create` | `/ingest`, `/api/memories/add` | POST |
| `/api/memories/query` | `/query`, `/api/retrieval/knapsack-pack` | POST |
| `/api/security/scrub` | `/api/security/scrub-pii` | POST |

Canonical paths are the documented interface; aliases are stable
compatibility shims returning identical envelopes.

## Permission matrix

| Endpoint | Required scope | Notes |
|---|---|---|
| `GET /liveness`, `GET /readiness`, `GET /health`, `GET /metrics` | none | Unauthenticated probes |
| `GET /api/memories` (+ alias) | `memories:read` | Tenant-scoped list |
| `POST /ingest`, `/api/memories/create`, `/api/memories/add` | `memories:write` | 201 on success |
| `POST /api/memories/bulk` | `memories:write` | max 100 items, 500KB text |
| `POST /query`, `/api/memories/query`, `/api/retrieval/knapsack-pack` | `memories:read` | Retrieval + packing |
| `POST /feedback` | `memories:write` | Reinforcement signal |
| `DELETE /api/memories/{memory_id}` | `memories:delete` | Hard delete; ownership enforced |
| `POST /api/memories/{memory_id}/retract` | `memories:delete` | Soft-retract evidence; retains provenance; audited |
| `POST /api/memories/clear` | `memories:delete` | Real `deleted_count`; audited |
| `POST /api/security/gdpr-delete` | `memories:delete` | Right-to-erasure; audited |
| `POST /api/security/scrub` (+ alias) | `memories:read` | PII scrubber |
| `POST /api/auth/keys/create` | `admin` | Secret returned once |
| `GET /api/auth/keys` | `admin` | Never exposes secrets |
| `POST /api/auth/keys/revoke` | `admin` | Immediate effect |
| `POST /api/auth/keys/rotate` | `admin` | Revoke old + issue new |
| `GET /api/auth/audit` | `admin` | `limit` capped at 200 |
| `GET /api/auth/audit/export` | `admin` | `limit` capped at 20000 |
| `POST /api/auth/audit/prune` | `admin` | Body `{"older_than_days": 90}` |

Tenant resolution: non-admin principals are bound to their own `tenant_id` —
a mismatch is HTTP 403 `"Tenant mismatch."`. Admin principals must pass an
explicit `tenant_id` for cross-tenant ops (HTTP 400 otherwise). A dev-open
bypass exists ONLY for a fully unconfigured local setup (not production, no
`SYNAPSE_API_KEY`, no keys provisioned); once any auth is configured,
anonymous requests are 403.

## Agent run traces and retrieval provenance

Trace endpoints use explicit scopes. The legacy admin scope does not imply agent-trace access.

| Endpoint | Required scope | Behavior |
|---|---|---|
| POST /api/agent-runs | agent-runs:write | Create a run summary; idempotent for the same tenant/run ID |
| POST /api/agent-runs/{run_id}/events | agent-runs:write | Append one versioned event; reusing the same event ID and payload is idempotent |
| GET /api/agent-runs | agent-runs:read | List a bounded, tenant-scoped page of summaries |
| GET /api/agent-runs/{run_id} | agent-runs:read | Read one run summary |
| GET /api/agent-runs/{run_id}/events | agent-runs:read | Read chronological metadata-only events |
| DELETE /api/agent-runs/{run_id} | agent-runs:delete | Delete the run and its events for this tenant |

IDs run_id and event_id must match the pattern ^[A-Za-z0-9._:-]{1,128}$. Supported schema version is 1. Run creation accepts run_id, schema_version, and optional tenant_id/tenantId; normal principals remain bound to their key's tenant via resolve_tenant().

The event contract includes event_id, run_id, schema_version, event_type, timezone-aware timestamp, and run_status, plus optional step/tool metadata, a 64-character hexadecimal argument fingerprint, a short reason/error type, and retrieval metadata. Supported event types are run.started, step.started, tool_call.started, tool_call.succeeded, tool_call.failed, tool_call.blocked, run.limit_exceeded, run.finished, and memory.retrieval.

For memory.retrieval, provide a retrieval_id and up to 100 memory_evidence entries. Each entry supports memory_id, positive rank, allowlisted numeric score_components, non-negative token_cost, boolean packed, optional created_at, and optional supersedes/superseded_by IDs. The persistence layer rejects arbitrary evidence fields; prompts, queries, raw tool arguments, tool results, and memory content are not stored. Evidence is recorded only when the application explicitly instruments the retrieval pipeline; the API does not automatically attach all /query calls to a run.

Event sequence numbers are assigned transactionally per run. Duplicate event IDs are accepted only when the persisted event fields match; conflicting reuse returns HTTP 409. Missing runs return 404; invalid payloads return 422; database failures return 503. Event listing defaults to 100 and caps at 500; run listing defaults to 50 and caps at 100. The API rate limiter uses a normalized run-route bucket so arbitrary run IDs do not create unlimited rate-limit entries.

On PostgreSQL, agent_runs and agent_run_events have ENABLE/FORCE RLS policies tied to app.current_tenant; every SQL operation also includes an explicit tenant predicate. SQLite uses tenant predicates in every operation. Schema migration v6 creates these tables. Apply migrations before booting a least-privilege PostgreSQL runtime role.

## Telemetry (no auth)

### GET /liveness
Process liveness, DB-independent. `{"status": "OK", "timestamp": <float>}`.
K8s `livenessProbe`. Stays OK during a DB outage by design.

### GET /readiness
DB + auth usability. `{"status": "READY", ...}` or HTTP 503 with `reason`.
K8s `readinessProbe`; compose `healthcheck`.

### GET /health
Service telemetry: `storage_backend`, `hnsw_enabled`, `row_level_security`
(`ENFORCED` on pgvector, `TENANT_INDEXED` on sqlite),
`total_indexed_memories`, `embedding_provider`, `engine_budget_solver`,
`auth_engine`. HTTP 503 when the DB is offline.

### GET /metrics
Prometheus text: `synapse_requests_total{path,status}`,
`synapse_request_latency_seconds{path,quantile}` (+ sum/count),
`synapse_auth_failures_total`, `synapse_rate_limit_rejections_total`,
`synapse_ingest_total{outcome}`, `synapse_retrieval_total{outcome}`,
`synapse_retrieval_candidates`, `synapse_context_tokens_packed`,
`synapse_memories_total{backend}`, `synapse_db_pool_in_use/size`,
`synapse_rate_limit_backend{backend}`, `synapse_up`. All values live.

## Memory operations

### GET /api/memories (alias: /api/memories/list)
Scope: `memories:read`. Tenant-scoped paginated list.

| Param | Default | Constraints |
|---|---|---|
| `tenant_id` | principal tenant | `^[A-Za-z0-9_\-\.]+$` |
| `category` | — | `^[a-z0-9_\-]+$` |
| `limit` | 50 | clamped 1–200 |
| `offset` | 0 | ≥ 0 |

Response `data`: `{memories, tenant_id, total, limit, offset,
storage_backend}`.

### POST /api/memories/create (aliases: /ingest, /api/memories/add)
Scope: `memories:write`. → HTTP 201. Tenant isolation, field-level
encryption, semantic deduplication, token-cost calculation.

| Field | Required | Default | Constraints |
|---|---|---|---|
| `content` | Yes | — | 1–32000 chars, non-blank |
| `category` | No | `"interaction"` | `^[a-z0-9_\-]+$`, ≤64 chars |
| `confidence` | No | 0.95 | 0.0–1.0 |
 |
| `observed_at` | No | ingestion time | finite Unix timestamp ≥ 0 |
| `valid_from` / `valid_until` | No | unbounded | finite Unix timestamps; if both supplied, `valid_until > valid_from` |
| `trust_level` | No | `unverified` | `unverified|low|medium|high|authoritative` |

Response `data`: `{id, status: "SUCCESS_<action>", token_cost, tenant_id}`.
Trust is caller-declared metadata, not an independent authenticity check. Do not put prompts, PII, secrets, or raw source text in `source_id`; use an opaque identifier.


### POST /api/memories/bulk
Scope: `memories:write`. Team backfills: `items` (1–100, per-item limits as
above) plus optional `tenant_id` (`tenantId` alias). Whole call rejected with
HTTP 413 past 500KB total text. Blank items are skipped, not errors.
Response `data`: `{tenant_id, ingested, results: [{id, status, token_cost}]}`.

### DELETE /api/memories/{memory_id}
Scope: `memories:delete`. Ownership enforced — a principal cannot delete
another tenant's node. Optional `tenant_id` query param.
Response `data`: `{status: "DELETED", id}`. Idempotent: success envelope even
when the row did not exist.

### POST /api/memories/{memory_id}/retract
Scope: `memories:delete`. Body: `{"reason":"source_corrected"}`, where the reason is a controlled lowercase snake_case code (1–128 chars). Retraction is tenant-scoped, idempotent, audited, and preserves the memory row/provenance lineage. The record is excluded from retrieval immediately.
Response `data`: `{id, status: "RETRACTED"|"ALREADY_RETRACTED", retracted_at, retraction_reason}`.

### POST /api/memories/clear
Scope: `memories:delete`. Optional body `{"tenant_id": ...}`.
Response `data`: `{status: "CLEARED", tenant_id, deleted_count}` — the real
affected-row count. Audited as `MEMORIES_CLEAR`.

## Retrieval

### POST /api/memories/query (aliases: /query, /api/retrieval/knapsack-pack)
Scope: `memories:read`. Hybrid vector + lexical RRF fusion, Ebbinghaus decay
(`S = R × D`), 0/1-knapsack token-budget packing.

| Field | Required | Default | Constraints |
|---|---|---|---|
| `prompt` | No* | null | 1–8000 chars |
| `query` | No* | null | alias for `prompt`, 1–8000 chars |
| `max_token_budget` (`maxTokens` alias) | No | 1500 | 50–8000 |
| `category` | No | null | `^[a-z0-9_\-]+$`, ≤64 chars |
| `tenant_id` (`tenantId` alias) | No | from principal | tenant pattern, ≤128 chars |
| `as_of` (`asOf` alias) | No | request time | finite Unix timestamp ≥ 0 |
| `run_id` | No | null | active tenant-scoped run ID; requires `agent-runs:write` |

Retrieval filters future-observed, not-yet-valid, expired, superseded, and retracted memories **before** dense/lexical ranking and top-K selection. Validity uses the half-open interval `valid_from <= as_of < valid_until`; legacy rows without `observed_at` use `created_at`. The response includes `retrieval_as_of` so an evaluation or replay can reproduce the time boundary.

\* At least one of `prompt`/`query` should be non-empty; an empty query
returns an empty packed context, not an error.

Response `data`: `{fused_context, injected_nodes_count, injected_nodes,
active_storage_backend, retrieval_as_of, retrieval_id}`. When `run_id` is
provided, `/query` appends a metadata-only `memory.retrieval` event to that
active run (rank, scores, token cost, packed flag, memory IDs, and safe
provenance only); query text and memory contents are not recorded. Packed nodes preserve `retrieval_score` and
`decay_multiplier`; access counts increment per node; every packed response
respects the token budget.

## Reinforcement

### POST /feedback
Scope: `memories:write`. `memory_id` (1–256 chars, required),
`feedback_type` (`"positive"`|`"negative"`, required), optional `tenant_id`.
404 when the node is not in the caller's tenant.
Response: `{id, feedback_status: "PROCESSED", old_confidence,
new_confidence, tenant_id}`.


## Enterprise security — scope `admin` on all

### POST /api/auth/keys/create
Issues an API key; the secret `api_key` is returned **once** at creation.
`name` (1–128 chars, required). Optional: `organization_id` / `project_id`
(id pattern, ≤128 chars, default `org_default` / `proj_default`),
`tenant_id` (id pattern or `"*"`, default `"default"`),
`scopes` (default `["memories:read","memories:write"]`, 1–16 items),
`role` (`admin|developer|service|viewer`, default `developer`),
`ttl_days` (1–3650, default 365).
Response: `{status: "CREATED", api_key, key_id, name, scopes, tenant_id,
expires_at}`.

### GET /api/auth/keys
Key metadata only — never secrets. Organization-scoped unless `org_root`.

### POST /api/auth/keys/revoke
Body `{key_id}` (1–128 chars). Immediate effect.
Response: `{status: "REVOKED"|"NOT_FOUND", key_id}`.

### POST /api/auth/keys/rotate
Body `{key_id}`. Revokes the old key, issues a new one with the same metadata
(zero-downtime rotation; non-idempotent — each call issues a fresh secret).
Response: `{status: "ROTATED", old_key_id, api_key, key_id, expires_at}`.

### GET /api/auth/audit
Tenant-scoped audit trail, `limit` capped at 200. Response: `{audit_logs}`.

### GET /api/auth/audit/export
SIEM-ready export. Params: `since` (float timestamp), `limit` (capped 20000).
Response: `{audit_logs, exported}`.

### POST /api/auth/audit/prune
Body `{"older_than_days": 90}` (must be a number). Audited itself as
`AUDIT_RETENTION_PRUNE`. Response: `{deleted, older_than_days}`.

## Security utilities

### POST /api/security/scrub (alias: /api/security/scrub-pii)
Scope: `memories:read`. Body `{"text": "..."}` (400 when missing). Returns
`{detectedPII, scrubbedText, redactionCount, encryptionCMEK}` without echoing
raw sensitive input. Redacts emails, phones, cloud API keys, SSNs, cards,
Aadhaar groups, and India PANs.

### POST /api/security/gdpr-delete
Scope: `memories:delete`. Body `{"tenant_id" | "tenantId": ...}`. Deletes
only that tenant's rows, returns the real `deleted_count`, audited as
`GDPR_RIGHT_TO_BE_FORGOTTEN_DELETE`.
Response: `{status: "SUCCESS", tenant_id, deleted_count, timestamp,
audit_logged: true}`.

## Validation, pagination, rate limits, retries

- **Validation failures** → HTTP 422 (Pydantic field detail) or HTTP 400 for
  semantic checks (empty content, non-numeric retention window).
- **Pagination** — list endpoints take `limit`/`offset` and return `total`;
  key/audit listings are bounded server-side (200 / 20000 caps).
- **Rate limits** — mutating + heavy retrieval paths (`/ingest`,
  `/api/memories*`, `/query`, `/api/retrieval*`): **120 req/min** per client
  key + path (tunable via `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_WINDOW_SECONDS`).
  Redis sorted-set window when `REDIS_URL` is set, per-process otherwise.
  429 envelope: `{"success": false, "error": "Rate limit exceeded. Retry after
  60s.", "request_id": "..."}`.
- **Idempotency** — safe to retry: `DELETE` is idempotent; bulk ingest skips
  blanks; feedback recomputes from stored confidence; only key rotation is
  non-idempotent (rotate once, then distribute).
- **Retry guidance** — retry 429 (after 60s) and 503 (backoff); do NOT
  blind-retry 400/403/404/413/422 (permanent). Pass `X-Request-ID` to trace
  gateway → engine.
- **Error taxonomy** — 400 bad request · 401/403 auth (generic message) ·
  404 unknown id · 413 bulk oversize · 422 schema violation · 429 rate limit ·
  503 DB unavailable. No stack traces or credentials in error payloads.

## Security notes

- `SYNAPSE_ENCRYPTION_KEY` never logged or returned.
- Tenant isolation via PostgreSQL RLS (pgvector) + `resolve_tenant()`.
  Cross-tenant resource-ID access is rejected (403).
- Fail-closed: engine refuses to start with missing schema
  (`SYNAPSE_APPLY_DB_SCHEMA=false` + unmigrated DB); DB outages are 503,
  never fake success.
- Demo routes off in production (`ENABLE_DEMO_ROUTES=false`).
- CORS allowlist via `ALLOWED_ORIGINS` (`ALLOW_ALL_CORS=true` is dev-only).
