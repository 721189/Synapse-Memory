"""Tests for the forward-only schema migration runner (C1)."""

import os
import tempfile
import unittest
import sqlite3

from synapse_memory.core.migrations import run_migrations, CURRENT_SCHEMA_VERSION, MIGRATIONS


class TestMigrations(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # Pre-create the base memories table (as the store would) minus the new columns.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                access_count INTEGER NOT NULL,
                token_cost INTEGER NOT NULL,
                embedding TEXT,
                feedback_multiplier REAL NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _columns(self, table):
        conn = self._get_conn()
        try:
            cur = conn.execute(f"PRAGMA table_info({table});")
            return {row[1] for row in cur.fetchall()}
        finally:
            conn.close()

    def test_migrations_add_metadata_columns_and_ledger(self):
        applied = run_migrations(self._get_conn, is_postgres=False)
        self.assertGreaterEqual(applied, 2, "expected ledger + metadata migrations to apply")

        cols = self._columns("memories")
        self.assertIn("embedding_dimension", cols)
        self.assertIn("model_version", cols)
        self.assertTrue({
            "source_type", "source_id", "observed_at", "valid_from", "valid_until",
            "trust_level", "supersession_reason", "retracted_at", "retraction_reason",
        } <= cols)
        conn = self._get_conn()
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("agent_runs", tables)
            self.assertIn("agent_run_events", tables)
            run_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
            event_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_run_events)").fetchall()}
            self.assertTrue({"tenant_id", "run_id", "schema_version", "status", "event_count"} <= run_columns)
            self.assertTrue({
                "tenant_id", "run_id", "sequence_number", "event_id",
                "event_type", "timestamp", "metadata_json",
            } <= event_columns)
        finally:
            conn.close()

        # Ledger table exists with all versions recorded.
        conn = self._get_conn()
        try:
            versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations;").fetchall()}
        finally:
            conn.close()
        self.assertEqual(versions, {m.version for m in MIGRATIONS})

    def test_migrations_are_idempotent(self):
        first = run_migrations(self._get_conn, is_postgres=False)
        second = run_migrations(self._get_conn, is_postgres=False)
        self.assertEqual(second, 0, "re-running migrations must apply nothing new")
        self.assertGreaterEqual(first, 2)

    def test_current_version_constant_matches_registry(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, MIGRATIONS[-1].version)
        self.assertEqual(CURRENT_SCHEMA_VERSION, 7)


if __name__ == "__main__":
    unittest.main()
