# Memory provenance metadata (schema v7)

Schema v7 adds explicit provenance columns to each memory record. The fields are
available through the Python storage records and the authenticated memory
ingestion API.

| Field | Meaning | Default |
|---|---|---|
| `source_type` | Controlled source class: `user`, `tool`, `document`, `system`, `import`, or `unspecified` | `unspecified` |
| `source_id` | Opaque, caller-supplied source identifier; restricted to a safe identifier alphabet | null |
| `observed_at` | Unix timestamp at which the source fact was observed | ingestion time |
| `valid_from` / `valid_until` | Optional half-open validity interval [from, until) | unbounded |
| `trust_level` | Caller-declared classification: `unverified`, `low`, `medium`, `high`, or `authoritative` | `unverified` |
| `supersession_reason` | Reason recorded when a new memory supersedes an existing one | null |
| `retracted_at` / `retraction_reason` | Reserved persisted fields for explicit retraction workflows | null |

Example:

```python
memory_id, status, token_cost = manager.ingest_with_deduplication(
    content="The deployment window is Tuesday",
    category="fact",
    tenant_id="team-42",
    source_type="document",
    source_id="doc:release-calendar",
    observed_at=1_800_000_000,
    valid_from=1_799_000_000,
    valid_until=1_801_000_000,
    trust_level="high",
)
```

The trust value is an assertion made by the caller, not a cryptographic or
independent verification result. Do not put prompts, raw document text, names,
email addresses, credentials, or other sensitive content in `source_id`.

## Compatibility and current limits

- Existing callers remain compatible. Records created without provenance are
  explicitly marked `unspecified` / `unverified`; historical rows receive
  these defaults during the forward-only migration.
- SQLite and PostgreSQL use the same field names and defaults. PostgreSQL
  continues to apply the existing tenant RLS boundary; the new columns do not
  change tenant authorization.
- Validity timestamps are validated and persisted, but this schema-only
  increment does **not yet change retrieval ranking or exclude expired/future
  evidence**. That behavior requires the follow-up temporal retrieval work and
  tests before these fields can be treated as an enforcement mechanism.
- Retraction columns are persisted for forward compatibility; a tenant-scoped
  retraction API and deletion semantics are not part of this change.
- The migration is additive and forward-only. Existing v1–v6 migrations are
  unchanged.
