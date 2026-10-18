# Security Policy

## Reporting a Vulnerability

We take the security of the Synapse Cognitive Substrate seriously. If you discover or suspect a security vulnerability, **please do not open a public GitHub issue**. Publicly disclosing flaws puts all downstream production deployments at risk.

Instead, please report vulnerabilities through one of our private channels:

1. **GitHub Private Vulnerability Reporting (Preferred)**: Navigate to the [Security Advisories page](https://github.com/721189/Synapse-Memory/security/advisories) and select **"Report a vulnerability"** to open a confidential discussion directly with the core maintainers.
2. **Dedicated Security Contact**: Send a detailed report directly to:
   - **`singhshivam20009@gmail.com`**
   - For sensitive cryptographic or multi-tenant isolation vulnerabilities, please include reproduction steps, expected vs. actual behavior, and affected versions.

### What to Include
To help us triage and resolve the issue quickly, please include:
- A description of the vulnerability and its potential impact.
- Step-by-step reproduction instructions or a minimal Proof-of-Concept (PoC) script.
- Affected versions, OS, Python/Node runtime environment, and dependency tree.
- Any suggested mitigations or candidate patches.

### Response Timelines
- **Initial Acknowledgment**: Within 24–48 hours.
- **Triage & Severity Assessment**: Within 5 business days.
- **Coordinated Patch & Public Advisory**: Target within 30 days of confirmed reproduction, following standard Coordinated Vulnerability Disclosure (CVD) practices.

## Supported Versions

| Version | Supported | Security Updates |
| :--- | :--- | :--- |
| `1.2.x` (SDK + API v1 + chart app 1.2.0) | Yes | Active patches & immediate hotfixes |
| `0.1.x` | Limited | Critical CVE fixes only |
| `< 0.1.0` | No | End of Life (upgrade immediately) |

## Security Architecture & Best Practices

1. **Cryptographic Key Management**:
   - Never commit `SYNAPSE_ENCRYPTION_KEY` to source control.
   - Use dedicated KMS/Vault providers (AWS KMS, GCP Cloud KMS, HashiCorp Vault) to inject 32-byte Fernet keys into containers at runtime.
   - Ensure secure file permissions (`chmod 600 .synapse_key`) when using local filesystem fallback keys.
   - Back up the key separately from data; key loss = unrecoverable ciphertext (see `docs/backup-recovery.md`).
2. **Multi-Tenant Memory Isolation (PostgreSQL — proven, not assumed)**:
   - In shared cluster deployments, always enforce tenant boundary verification in API gateways.
   - The engine applies `ENABLE` + `FORCE ROW LEVEL SECURITY` and a `tenant_isolation_policy`
     on `memories`. RLS is permissive-by-role: a query is filtered by
     `app.current_tenant`, and an empty context returns **zero rows** (fail closed).
   - **Required production topology — "migrate-then-run":** create the roles once with
     `scripts/provision_db_roles.py`, run the schema migration step as the migration role,
     then run the engine as a DML-only runtime role:
     ```bash
     # 1. Roles (run once, per cluster)
     DATABASE_URL=postgresql://bootstrap@host/db \
       python scripts/provision_db_roles.py \
         --runtime-role synapse_app --admin-role synapse_admin \
         --runtime-password "$SYNAPSE_APP_DB_PASSWORD" \
         --admin-password "$SYNAPSE_ADMIN_DB_PASSWORD"
     # 2. Schema + migrations, as the migration role
     DATABASE_URL=postgresql://synapse_admin@host/db PYTHONPATH=sdk/python \
       python -c "from synapse_memory.core.pgvector_db import PGVectorMemoryStore; \
                  PGVectorMemoryStore(connection_string='$DATABASE_URL', fail_closed=True)"
     # 3. Re-run the script so the runtime role is granted DML on the new tables
     # 4. Fail the release if the invariant is broken
     python scripts/provision_db_roles.py --verify-only
     ```
   - The runtime role is verified to be **NOT a superuser, NOT `BYPASSRLS`, NOT the table
     owner, has no schema `CREATE`/`TRUNCATE`/`REFERENCES`/`TRIGGER`**, and is **not a member
     of the migration role**. The script exits non-zero if any of that is untrue.
   - Because a non-owner role cannot run DDL, the engine must be started with
     `SYNAPSE_APPLY_DB_SCHEMA=false`. It then refuses to start if `memories` is missing
     rather than silently falling back to in-memory storage.
   - **Row-Level Security (RLS) Policy Mechanics (v1.3.1):**
     The PostgreSQL RLS policy `tenant_isolation_policy` on `memories` enforces strict tenant isolation:
     `tenant_id = NULLIF(current_setting('app.current_tenant', true), '') AND tenant_id IS NOT NULL`
     with identical `USING` and `WITH CHECK` clauses.
     - **Reads (`SELECT`):** Rows belonging to other tenants (or all rows if `app.current_tenant` is empty/unset) are filtered out by the `USING` clause, returning zero unauthorized rows.
     - **Writes (`INSERT`/`UPDATE`):** Any attempt to insert or update a row with a mismatched `tenant_id` violates the `WITH CHECK` clause and fails closed by raising SQLSTATE `42501` (`InsufficientPrivilege`).
     - **Administrative Cross-Tenant Access:** Intentional cross-tenant administrative operations (e.g. maintenance `clear_memories()`, corpus re-embedding) require both that the database session user is the configured admin role (`current_user = '<admin_role>'`) AND that an explicit bypass setting is enabled (`current_setting('app.admin_bypass_rls', true) = 'on'`). A non-empty tenant GUC alone never grants cross-tenant access. Runtime roles (`synapse_app`) are structurally denied from using this bypass.
     - **Limitations:** RLS enforcement relies on the runtime connection having `FORCE ROW LEVEL SECURITY`, `NOSUPERUSER`, `NOBYPASSRLS`, and not owning the `memories` table. The engine validates these structural invariants on startup when `apply_schema=false`.
   - Isolation is regression-tested with the **actual runtime role** (not an admin/DBA), over raw
     SQL and through the engine store: cross-tenant reads, missing context, cross-tenant
     writes, DDL/`TRUNCATE` denial, and tenant-scoped delete. See
     `tests/test_pgvector_integration.py::TestRuntimeRoleIsolation` plus the offline
     provisioning-contract tests in `tests/test_db_role_provisioning.py`.
3. **Data Protection Model (combined protection)**:
   | Layer | `content` column | `content_plain` | tsvector / embeddings | Volume/Database |
   |---|---|---|---|---|
   | Storage | Fernet field-encrypted (AES-128-CBC + HMAC-SHA256) | Stored plaintext search projection (written alongside `content` at ingest; readable by design for lexical/FTS search) | Readable search derivative (tsvector built from `content_plain`; embedding vectors) | Full disk / managed-DB encryption |
   | Access | Requires key (KMS/Vault / local key file) | Always readable (search projection) | Always readable | Operator-enforced |
   | Use case | Regulated data at rest | Full-text & vector search only | Obfuscated retrieval | At-rest coverage |
   | Notes | `content_plain` is stored plaintext — database dumps and backups expose it; protect with DB access controls + volume encryption, not field encryption | | | |
   - Volume/database encryption must be enabled by the operator for at-rest coverage.
   - **Database dump exposure:** `pg_dump` / logical backups expose `content_plain`, `search_vector`, and `embedding` in readable form; only `content` is Fernet ciphertext. Treat dumps as regulated-data artifacts: encrypt at rest, restrict restore access, and never ship dumps to untrusted environments. Encryption keys must be backed up separately from data (key loss = unrecoverable ciphertext; see `docs/backup-recovery.md`).
4. **Model & Ingestion Sanitization**:
   - Run untrusted third-party prompts through the built-in PII and prompt injection quarantine scrubber (`/api/security/scrub`) before indexing into cognitive graphs.
5. **Operational Security**:
   - Auth failures return generic messages (no internal reason leakage); audit log retains detail.
   - Rate limiting is in-process by default; set `REDIS_URL` for cluster-wide enforcement across replicas.
   - Rotate API keys via create → migrate → revoke; revoke takes effect immediately.

