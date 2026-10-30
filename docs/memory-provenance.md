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
| `retracted_at` / `retraction_reason` | Soft-retraction time and controlled reason code; retained for audit lineage | null |

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
independent verification result. At the HTTP API boundary, ordinary
`memories:write` credentials may set only `unverified` or `low`; assigning
`medium`, `high`, or `authoritative` requires the separate `memories:trust`
scope. The legacy `admin` scope does not imply trust elevation. User/tool text
cannot promote its own provenance by containing instructions that claim
authority. Do not put prompts, raw document text, names, email addresses,
credentials, or other sensitive content in `source_id`.

## Compatibility and current limits

- Existing callers remain compatible. Records created without provenance are
  explicitly marked `unspecified` / `unverified`; historical rows receive
  these defaults during the forward-only migration.
- SQLite and PostgreSQL use the same field names and defaults. PostgreSQL
  continues to apply the existing tenant RLS boundary; the new columns do not
  change tenant authorization.
- Retrieval applies the half-open interval `valid_from <= as_of < valid_until`.
  Evidence with an observation timestamp later than `as_of`, a retraction
  timestamp, or a supersession successor is excluded before dense/lexical
  ranking and context packing. Legacy rows use `created_at` as their
  observation-time fallback.
- The query API accepts optional `asOf` (Unix seconds). Without it, the
  server evaluates validity at request time and returns `retrieval_as_of` in
  the response. The Python client exposes this as `retrieve(..., as_of=...)`.
- Use the authenticated `POST /api/memories/{memory_id}/retract` endpoint
  with `memories:delete` scope and a controlled lowercase reason code. It is
  tenant-scoped, idempotent, audited, and preserves the memory row and its
  provenance lineage. Use the existing hard-delete route only when actual
  erasure is intended.
- Trust remains caller-declared metadata, not independent verification. A
  source's trust label does not override validity bounds or retraction.
- The migration is additive and forward-only. Existing v1–v6 migrations are
  unchanged.
