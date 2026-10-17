# OWASP API Security Review — Synapse Memory 1.2.0

Assessment baseline against the OWASP API Security Top 10 (2023). This is a
threat checklist used during hardening, not a claim of certification or
compliance. Each item maps to the actual control in this repo.

## API1 Broken Object-Level Authorization (BOLA)

- **Control:** every resource-specific operation resolves ownership through
  `resolve_tenant()` (`fastapi_server.py`) before touching the store.
  `DELETE /api/memories/{id}`, `POST /feedback`, `GET /api/memories` all bind
  the resource lookup to the caller's tenant.
- **DB layer:** PostgreSQL RLS `tenant_isolation_policy` + `FORCE ROW LEVEL
  SECURITY` filters by `app.current_tenant`; explicit `tenant_id = %s`
  predicates on top.
- **Evidence:** `test_pgvector_integration.py::TestRuntimeRoleIsolation`
  (cross-tenant read/write rejection, raw-SQL isolation), load tests assert
  cross-tenant invisibility on a 400-row 2-tenant matrix.
- **Residual:** RLS admin path is role-gated. `PGVectorMemoryStore` still sets
  `app.current_tenant = ''` when admin access is requested, but the policy's
  admin bypass is now `current_user = '<admin_role>'` instead of a GUC sentinel.
  Raising `app.current_tenant` (or any `app.*` GUC) can no longer bypass RLS —
  only a session whose database `current_user` is the configured admin role is
  exempt. App layer remains authoritative for tenant binding (`resolve_tenant`).
  Fix applied in v1.3.0.

## API2 Broken Authentication

- **Control:** `require_scope()` validates every protected endpoint; keys are
  hashed at rest; `validate_api_key` checks scope, tenant binding, expiry,
  revocation. Auth failures return generic `"Authentication failed."` (403) —
  no reason leakage; detail goes to the audit log. `X-Synapse-API-Key` or
  `Authorization: Bearer` accepted; secrets never in URLs or logs.
- **Lifecycle:** create → migrate → revoke (immediate) → rotate (revoke +
  reissue, same metadata). TTL 1–3650 days.
- **Evidence:** auth contract tests (missing/invalid/expired/revoked/
  insufficient-scope/tenant-mismatch); `/metrics`
  `synapse_auth_failures_total`.

## API3 Broken Object Property-Level Authorization

- **Control:** Pydantic schemas whitelist every field; `tenant_id` is resolved
  server-side from the principal, never trusted from the body for non-admins.
  Admin-only fields (key creation metadata, audit export) sit behind the
  `admin` scope. `ttl_days`, `role`, `scopes` are range/pattern-bounded.
- **Mass-assignment:** extra body fields are ignored by Pydantic defaults;
  no endpoint merges raw request dicts into privileged records.

## API4 Unrestricted Resource Consumption

- **Control:** content ≤32000 chars; query ≤8000; bulk ≤100 items + 500KB
  total (413); `max_token_budget` capped 1–8000 (packer hard cap 8000); list
  `limit` clamped 1–200; audit export ≤20000; rate limit 120 req/min per
  key+path (Redis-distributed when `REDIS_URL` set, in-process otherwise,
  429 + metric).
- **Evidence:** `test_load_resilience.py` (1000-row corpus, 100-item bulk,
  HTTP read/write bursts); contract tests for oversize payloads.

## API5 Broken Function-Level Authorization

- **Control:** scope matrix enforced by `require_scope` per route —
  `memories:read` (list/query/scrub), `memories:write` (ingest/feedback),
  `memories:delete` (delete/clear/gdpr-delete), `admin` (key + audit
  management). See `docs/api-reference.md` permission matrix.
- **Admin surface:** key create/list/revoke/rotate, audit read/export/prune
  are `admin`-only; key listing is organization-scoped. Documented in
  `docs/api-reference.md`.


## API6 Unrestricted Access to Sensitive Business Flows

- **Assessment:** bulk ingest, key rotation, GDPR delete, audit prune are the
  sensitive flows. Each is scope-gated, rate-limited, and audited
  (`MEMORIES_CLEAR`, `GDPR_RIGHT_TO_BE_FORGOTTEN_DELETE`,
  `AUDIT_RETENTION_PRUNE`). GDPR delete returns the real `deleted_count`.
- **Note:** no CAPTCHA/step-up auth ships — operator should front
  destructive admin flows with their IdP/MFA at the gateway layer.

## API7 Server-Side Request Forgery (SSRF)

- **Assessment:** the engine makes no outbound requests driven by caller URLs.
  Embedding providers are operator-configured (`SynapseEmbedder`), not
  per-request URLs. Gateway proxies only to the configured engine address
  (Helm-derived `PYTHON_ENGINE_URL`, compose service name) — not to
  caller-supplied hosts.
- **Action:** when adding webhooks/callbacks later, enforce an allowlist +
  no-link-local egress policy.

## API8 Security Misconfiguration

- **Controls:** fail-closed defaults (`FAIL_CLOSED_DB`, `IS_PRODUCTION`
  guards); demo routes gated (`ENABLE_DEMO_ROUTES=false` in prod); CORS
  allowlist via `ALLOWED_ORIGINS` (`ALLOW_ALL_CORS=true` dev-only); secrets
  via env/Secret mounts, never ConfigMap; DB creds excluded from error
  payloads and logs; non-root container users; dev deps pruned from images;
  SHA-pinned GitHub Actions; Trivy image scans; TruffleHog secret scan.
- **Operator duty:** enable volume/managed-DB encryption at rest; terminate
  TLS at ingress; set `ALLOWED_ORIGINS` to the real dashboard origin.

## API9 Improper Inventory Management

- **Control:** `docs/api-reference.md` is the single canonical inventory —
  every engine route, alias, scope, schema bound, and error code, generated
  from a line-by-line audit of `fastapi_server.py`. Gateway exposes
  `/api/health` (liveness) vs `/api/ready` (engine-backed readiness) so
  probes never confuse the inventory.
- **Action:** re-audit the doc on every release that adds a route; CI does
  not yet diff routes against the doc (recommended follow-up job).

## API10 Unsafe Consumption of APIs

- **Assessment:** the engine consumes Redis (rate limiter, optional) and
  PostgreSQL (pgvector) — both operator-provisioned, not third-party APIs.
  Redis outage degrades to in-process limiting (never fails requests);
  unreachable Redis at startup falls back with a warning, never a crash.
  Embedding-provider errors surface as 503 — never fake success.
- **Action:** validate TLS + auth on `REDIS_URL` / `DATABASE_URL` in
  production; pin provider SDK versions (see `pyproject.toml`).

## Out of scope / deferred

- Independent penetration test before storing regulated data (recommended,
  not yet commissioned).
- Route-vs-doc drift detection in CI (recommended job).
- Role-gated RLS admin path (implementing v1.3.0 fix recorded in `SECURITY.md`).
- Step-up auth / MFA for destructive admin flows (operator IdP concern).
