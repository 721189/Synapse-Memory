# Backup, Restore, Retention & Key Recovery (Phase 4 / 7.4)

Supported configuration: PostgreSQL + pgvector (prod), SQLite (dev only).

## 1. What to back up (all three, or restore fails)
1. **Postgres data** — `pg_dump -Fc` (preferred) or volume snapshot.
2. **Encryption key** — the exact `SYNAPSE_ENCRYPTION_KEY` (Fernet, urlsafe-base64).
   Without it, `content` ciphertext is unrecoverable. Reads fail safe (`RuntimeError`).
3. **Schema ledger** — `schema_migrations` table rides inside the dump; verify
   `SELECT version FROM schema_migrations;` after restore (expect v6).

## 2. Backup procedure
```bash
pg_dump -Fc -U synapse_admin -h <host> synapse_cognitive > synapse_$(date +%F).dump
# Store the dump AND the key in separate secret stores (never together in git).
```

## 3. Restore drill (clean environment)
```bash
createdb -U synapse_admin synapse_restored
pg_restore -U synapse_admin -d synapse_restored synapse_YYYY-MM-DD.dump
export DATABASE_URL=postgresql://synapse_admin:***@host:5432/synapse_restored
export SYNAPSE_ENCRYPTION_KEY='<restored key>'
# Engine boots, runs pending migrations, /readiness returns READY,
# and a spot-check read decrypts correctly.
```

## 3b. Automated drill (executable)
```bash
python scripts/backup_restore_drill.py             # SQLite — runs anywhere
python scripts/backup_restore_drill.py --postgres  # + pg_dump/pg_restore/psql
```
Exit code `0` = pass, `1` = a check failed, `2` = nothing runnable in this
environment (reported, never a silent pass). The drill backs up a live store,
restores it into a **clean** target, then verifies: row counts (overall and per
tenant), that content decrypts with the **same** key, that the
`schema_migrations` ledger is at the current version, and that a **wrong** key
fails safe (raises) instead of returning garbage. The PostgreSQL path shells out
to `pg_dump -Fc` / `pg_restore` and re-verifies via `psycopg2`; it SKIPs cleanly
when the client tools or `DATABASE_URL` are absent.

## 4. Retention & deletion
- `clear_memories(tenant_id)` deletes only that tenant's rows; returns the
  actual deleted count (verified by `deleted_count` in the API response).
- GDPR delete (`/api/security/gdpr-delete`) is the same path + audit log.
- No background hard-delete job ships; retention windows are operator policy.

## 5. Key rotation (no silent re-encryption)
1. Deploy new key as `SYNAPSE_ENCRYPTION_KEY_NEXT`.
2. Run the re-encryption sweep: read each row with old key, write with new key
   (same pattern as `reembed_all_memories`, batched + committed per batch).
3. Promote the new key, keep the old for one backup cycle, then destroy.
4. `content_plain` is re-derived from decrypted plaintext during the sweep.
