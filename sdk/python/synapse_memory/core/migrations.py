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
CURRENT_SCHEMA_VERSION = 3


@dataclass
class Migration:
    version: int
    name: str
    # Callables receive a cursor + a `p` placeholder ('?' for sqlite, '%s' for pg)
    sqlite: Optional[Callable[[Any], None]] = None
    postgres: Optional[Callable[[Any], None]] = None


def _add_column_if_missing(cur: Any, table: str, column: str, coltype: str) -> None:
    """Idempotent ADD COLUMN that works on both SQLite and PostgreSQL."""
    try:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, column),
        )
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
    except Exception:
        # SQLite has no information_schema; fall back to a no-op probe + add.
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
        except Exception:
            pass  # column already exists


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


MIGRATIONS: List[Migration] = [
    Migration(1, "embedding_metadata", _m1_embedding_metadata, _m1_embedding_metadata),
    Migration(2, "migration_ledger", _m2_migration_ledger, _m2_migration_ledger),
    Migration(3, "backfill_dimension", _m3_backfill_dimension, _m3_backfill_dimension),
]


def _applied_versions(cur: Any) -> set:
    try:
        cur.execute("SELECT version FROM schema_migrations;")
        rows = cur.fetchall()
        return {int(r[0] if isinstance(r, (tuple, list)) else r["version"]) for r in rows}
    except Exception:
        # Ledger table doesn't exist yet -> nothing applied.
        return set()


def run_migrations(get_connection: Callable[[], Any], is_postgres: bool, fail_closed: bool = False) -> int:
    """Apply all pending forward migrations. Returns count applied. Idempotent."""
    applied_total = 0
    conn = get_connection()
    # True only when the factory hands us a fresh (non-pooled) connection that
    # we own. PGVectorMemoryStore passes a bound method returning pooled
    # connections — closing those would destroy pool slots.
    owns_connection = not (
        hasattr(get_connection, "__self__") and hasattr(get_connection.__self__, "_pool")
    )
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
            if owns_connection:
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
