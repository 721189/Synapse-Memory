# Production-Hardening Baseline Report

- Branch: `production-hardening`
- Commit: `576f05b818a000e56c481eb8967b53f7d90cba44` (+ working-tree P0 fixes below)
- Date (UTC): 2026-10-14

## Test evidence
- `PYTHONPATH=sdk/python pytest sdk/python/tests -q --ignore=test_pgvector_integration.py`
  - Before P0 changes: 54 passed
  - After P0 changes: **61 passed** (added 3 decay-composition tests + 4 RRF/encryption regression tests)
- `test_pgvector_integration.py` requires a live pgvector container (`DATABASE_URL=postgresql://synapse_test:synapse_test@localhost:5432/synapse_test`); not run in this environment — recorded as open baseline item per Phase 1.
- Gateway `npm run lint/build`, `docker build` (engine/gateway), Compose smoke, and Helm validation remain open baseline items (no Node/Docker toolchain in this environment).

## P0 fixes applied (Phase 2)
- P0-1 hybrid RRF: `COALESCE(1.0/(60+d.rank),0.0)+COALESCE(1.0/(60+l.rank),0.0)`; fixed lexical param-count bug (`params=[query]` + `[query, top_k]` tail).
- P0-2 encryption (combined protection): `content` = Fernet ciphertext, `content_plain` = searchable projection for tsvector; all read paths decrypt via `_decrypt_row`; wrong key raises (fail safe); embeddings + tsvector documented as readable derivatives.
- P0-3 gateway fail-closed: `/api/health` = liveness only; new `/api/ready` = 503 when engine down; list/add/clear/chat return 401/403/503 accurately in production, no fake successes, no demo-data mixing.
- P0-4 decay: `S(m,q,t)=R(m,q)*D(m,t)` via `temporal_multiplier()` + `combine_scores()`; `/query` preserves `retrieval_score` and adds `decay_multiplier`.
- P0-5 isolation: unchanged RLS design verified by existing integration tests (needs live PG run in CI); no code change — admin sentinel + `SET LOCAL` + rollback-on-return already present.
- P0-6 pooling: `ThreadedConnectionPool` (fallback Simple), `connect_timeout=5`, rollback before `putconn`.

## Threat model (P0-2 decision)
| Layer | Protection | Notes |
|---|---|---|
| Volume / managed Postgres encryption | Protects everything at rest (ciphertext, embeddings, tsvector) | Must be enabled by operator; verify with provider |
| Field-level (`content` Fernet) | Authoritative content unreadable without `SYNAPSE_ENCRYPTION_KEY` | Key via env/file-mount/KMS; never logged/returned; rotation = re-encrypt via `reembed`-style sweep (procedure TBD Phase 4) |
| Searchable derivatives | `content_plain`, `search_vector` tsvector, `embedding` HNSW remain readable by design | Required for BM25 + vector search; do NOT claim "all stored data encrypted" |
| Key loss | Reads raise `RuntimeError`, fail safe | Backup must include key custody; unrecoverable without key |

## Open items (honest gaps)
1. Live pgvector integration suite run (needs container).
2. `npm ci/lint/build`, both `docker build`, Compose smoke, Helm render/deploy.
3. Migration idempotency test on old schema + backup/restore drill (Phase 4).
4. Rate-limit distribution claim: still in-process; either wire Redis or document single-instance (Phase 3D).
