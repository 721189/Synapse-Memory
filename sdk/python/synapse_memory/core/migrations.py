"""
Lightweight, dependency-free forward-only schema migrations for SynapseMemory.

Why not Alembic: the engine must stay importable and runnable with zero external
services (SQLite) and a minimal dependency footprint. This runner is versioned,
idempotent, and records applied versions in a `schema_migrations` ledger table so
a team can audit exactly which schema state a database is in. It supports both
SQLite (local/dev) and PostgreSQL (team/prod) via a tiny dialect shim.

Adding a migration:
  1. Append a Migration(version=N, name=..., sqlite=..., postgres=...) to MIGRATIONS.
  2. Bump CURRENT_SCHEMA_VERSION to N.
Never edit an already-released migration — add a new one.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

logger = logging.getLogger("SynapseMigrations")

# Ledger + metadata columns tracked on the memories table so embedding-model
# upgrades (e.g. 128-d hash -> 384-d MiniLM) are explicit, not silent.
CURRENT_SCHEMA_VERSION = 7


@dataclass
class Migration:
    version: int
    name: str
    # Callables receive a cursor + a `p` placeholder ('?' for sqlite, '%s' for pg)
    sqlite: Optional[Callable[[Any], None]] = None
    postgres: Optional[Callable[[Any], None]] = None


def _column_exists(cur: Any, table: str, column: str) -> bool:
    """Dialect-aware column existence probe (PostgreSQL then SQLite)."""
    try:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, column),
        )
        if cur.fetchone():
            return True
        return False
    except Exception:
        # SQLite has no information_schema; fall through to PRAGMA.
        pass
    try:
        cur.execute(f"PRAGMA table_info({table});")
        for row in cur.fetchall():
            if isinstance(row, dict):
                if row.get("name") == column:
                    return True
            elif len(row) > 1 and row[1] == column:
                return True
        return False
    except Exception:
        return False


def _add_column_if_missing(cur: Any, table: str, column: str, coltype: str) -> None:
    """Idempotent ADD COLUMN that works on both SQLite and PostgreSQL.

    Only a real duplicate-column condition counts as success. Any other failure
    — missing privileges, invalid DDL, lock timeout, schema drift — is re-raised
    so ``fail_closed=True`` aborts startup instead of recording a migration
    version for a column that was never created (F-12).
    """
    if _column_exists(cur, table, column):
        return
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
    except Exception:
        # Re-probe: a concurrent replica may have added it, in which case this
        # is genuinely done. Otherwise the failure is real and must propagate.
        if _column_exists(cur, table, column):
            return
        raise


def _m1_embedding_metadata(cur: Any) -> None:
    """v1: Track embedding dimension + model version per memory row."""
    _add_column_if_missing(cur, "memories", "embedding_dimension", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(cur, "memories", "model_version", "VARCHAR(64) NOT NULL DEFAULT 'unknown'")


def _m2_migration_ledger(cur: Any) -> None:
    """v2: Audit ledger of applied schema versions (created here, not via ADD COLUMN)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            applied_at DOUBLE PRECISION NOT NULL
        );
        """
    )


def _m3_backfill_dimension(cur: Any) -> None:
    """v3: Backfill embedding_dimension for rows written before tracking existed."""
    # Rows with dimension 0 keep 0 (unknown); a later re-embed pass fills them.
    cur.execute("UPDATE memories SET embedding_dimension = 0 WHERE embedding_dimension IS NULL;")


def _m4_content_plain_search_projection(cur: Any) -> None:
    """v4: Combined-protection search projection for field-encrypted content.

    Adds `content_plain` (searchable projection feeding the tsvector index).
    Existing rows backfill `content_plain` from `content` ONLY when that
    column still holds plaintext — i.e. rows written before P0-2 field
    encryption. Rows already holding Fernet ciphertext are left untouched
    (re-encrypting ciphertext would destroy recoverability); operators must
    run the documented key-rotation/re-encryption sweep for those.
    Detection heuristic: Fernet tokens are urlsafe-base64 starting with
    'gAAAAA'; anything else is treated as legacy plaintext.
    """
    _add_column_if_missing(cur, "memories", "content_plain", "TEXT NOT NULL DEFAULT ''")
    cur.execute(
        "UPDATE memories SET content_plain = content "
        "WHERE (content_plain IS NULL OR content_plain = '') "
        "AND content NOT LIKE 'gAAAAA%';"
    )


def _m5_supersession_chain(cur: Any) -> None:
    """v5: Deduplication supersession lineage (MERGE keeps both rows).

    `supersedes` points from the successor (new) row to the row it replaced;
    `superseded_by` points from the replaced (old) row to its successor.
    """
    _add_column_if_missing(cur, "memories", "supersedes", "VARCHAR(128)")
    _add_column_if_missing(cur, "memories", "superseded_by", "VARCHAR(128)")


def _m6_agent_run_traces_sqlite(cur: Any) -> None:
    """v6: Tenant-scoped run summaries and metadata-only event history (SQLite)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            tenant_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'created',
            started_at REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            event_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, run_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_run_events (
            tenant_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            event_type TEXT NOT NULL,
            timestamp REAL NOT NULL,
            run_status TEXT NOT NULL,
            step_index INTEGER,
            tool_call_index INTEGER,
            tool_name TEXT,
            tool_fingerprint VARCHAR(64),
            reason VARCHAR(128),
            error_type VARCHAR(128),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            recorded_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, run_id, sequence_number),
            UNIQUE (tenant_id, run_id, event_id),
            FOREIGN KEY (tenant_id, run_id)
                REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_updated "
        "ON agent_runs(tenant_id, updated_at DESC);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_tenant_run "
        "ON agent_run_events(tenant_id, run_id, sequence_number);"
    )


def _m6_agent_run_traces_postgres(cur: Any) -> None:
    """v6: Tenant-scoped run summaries and event history protected by forced RLS."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            tenant_id VARCHAR(128) NOT NULL,
            run_id VARCHAR(128) NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(32) NOT NULL DEFAULT 'created',
            started_at DOUBLE PRECISION NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            event_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, run_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_run_events (
            tenant_id VARCHAR(128) NOT NULL,
            run_id VARCHAR(128) NOT NULL,
            sequence_number BIGINT NOT NULL,
            event_id VARCHAR(128) NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            event_type VARCHAR(64) NOT NULL,
            timestamp DOUBLE PRECISION NOT NULL,
            run_status VARCHAR(32) NOT NULL,
            step_index INTEGER,
            tool_call_index INTEGER,
            tool_name VARCHAR(128),
            tool_fingerprint VARCHAR(64),
            reason VARCHAR(128),
            error_type VARCHAR(128),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            recorded_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (tenant_id, run_id, sequence_number),
            UNIQUE (tenant_id, run_id, event_id),
            FOREIGN KEY (tenant_id, run_id)
                REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_updated "
        "ON agent_runs(tenant_id, updated_at DESC);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_tenant_run "
        "ON agent_run_events(tenant_id, run_id, sequence_number);"
    )
    # RLS is the database boundary; every app query additionally supplies tenant_id.
    for table, policy in (
        ("agent_runs", "agent_runs_tenant_policy"),
        ("agent_run_events", "agent_run_events_tenant_policy"),
    ):
        cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        cur.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        cur.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        cur.execute(
            f"""
            CREATE POLICY {policy} ON {table}
            FOR ALL
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                AND tenant_id IS NOT NULL
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                AND tenant_id IS NOT NULL
            );
            """
        )


def _m7_memory_provenance(cur: Any) -> None:
    """v7: Explicit source, temporal validity, trust, and retraction metadata."""
    columns = (
        ("source_type", "VARCHAR(64) NOT NULL DEFAULT 'unspecified'"),
        ("source_id", "VARCHAR(256)"),
        ("observed_at", "DOUBLE PRECISION"),
        ("valid_from", "DOUBLE PRECISION"),
        ("valid_until", "DOUBLE PRECISION"),
        ("trust_level", "VARCHAR(32) NOT NULL DEFAULT 'unverified'"),
        ("supersession_reason", "VARCHAR(128)"),
        ("retracted_at", "DOUBLE PRECISION"),
        ("retraction_reason", "VARCHAR(128)"),
    )
    for column, coltype in columns:
        _add_column_if_missing(cur, "memories", column, coltype)


MIGRATIONS: List[Migration] = [
    Migration(1, "embedding_metadata", _m1_embedding_metadata, _m1_embedding_metadata),
    Migration(2, "migration_ledger", _m2_migration_ledger, _m2_migration_ledger),
    Migration(3, "backfill_dimension", _m3_backfill_dimension, _m3_backfill_dimension),
    Migration(4, "content_plain_search_projection", _m4_content_plain_search_projection, _m4_content_plain_search_projection),
    Migration(5, "supersession_chain", _m5_supersession_chain, _m5_supersession_chain),
    Migration(6, "agent_run_traces", _m6_agent_run_traces_sqlite, _m6_agent_run_traces_postgres),
    Migration(7, "memory_provenance", _m7_memory_provenance, _m7_memory_provenance),
]


def _applied_versions(cur: Any) -> set:
    try:
        cur.execute("SELECT version FROM schema_migrations;")
        rows = cur.fetchall()
        return {int(r[0] if isinstance(r, (tuple, list)) else r["version"]) for r in rows}
    except Exception:
        # Ledger table doesn't exist yet -> nothing applied.
        return set()


def run_migrations(
    get_connection: Callable[[], Any],
    is_postgres: bool,
    fail_closed: bool = False,
    close_on_exit: bool = True,
    owns_connection: bool = True,
) -> int:
    """Apply all pending forward migrations. Returns count applied. Idempotent.

    Args:
        get_connection: Callable returning a fresh connection for this run.
        is_postgres: Dialect to apply (SQLite vs PostgreSQL).
        fail_closed: Raise on migration failure instead of skipping.
        close_on_exit: If True (default), the connection is released when the run
            finishes. Set False only when the caller keeps the connection alive
            and reuses it afterwards, e.g. SQLiteMemoryStore caching its
            schema-init ':memory:' connection.
        owns_connection: If True (default), treat the returned connection as
            freshly-owned (non-pooled) and close it in the finally block. Set
            False when the connection belongs to an external pool that must be
            returned, not closed. Existing callers can rely on the default True
            (fresh connection). PGVectorMemoryStore passes a pooled connection
            and sets this False.

        The connection is closed only when ``owns_connection`` AND
        ``close_on_exit`` are both True. Otherwise it is handed back through the
        factory owner's ``_return_connection`` hook (when present) or closed as
        a last-resort fallback.
    """
    applied_total = 0
    conn = get_connection()
    should_close = owns_connection and close_on_exit
    try:
        cur = conn.cursor()
        # The ledger (v2) must exist before we can read it; bootstrap directly.
        _m2_migration_ledger(cur)
        conn.commit()

        done = _applied_versions(cur)
        for migration in MIGRATIONS:
            if migration.version in done:
                continue
            fn = migration.postgres if is_postgres else migration.sqlite
            if fn is None:
                continue
            try:
                fn(cur)
                cur.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s);"
                    if is_postgres
                    else "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                    (migration.version, migration.name, time.time()),
                )
                conn.commit()
                applied_total += 1
                logger.info("Applied migration v%d: %s", migration.version, migration.name)
            except Exception as e:
                conn.rollback()
                if fail_closed:
                    raise RuntimeError(f"Migration v{migration.version} failed: {e}") from e
                logger.warning("Migration v%d (%s) skipped: %s", migration.version, migration.name, e)
    finally:
        try:
            if should_close:
                conn.close()
            else:
                # Return pooled connections to the pool instead of closing.
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    owner = getattr(get_connection, "__self__", None)
                    if owner is not None:
                        owner._return_connection(conn)
                    else:
                        conn.close()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception:
            pass
    return applied_total
