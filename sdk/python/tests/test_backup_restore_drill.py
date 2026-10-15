"""Tests for the executable backup/restore drill (scripts/backup_restore_drill.py).

Runs the SQLite drill end-to-end (backup -> clean restore -> decrypt with the
same key -> ledger check -> wrong-key fail-safe) and asserts the Postgres path
degrades to a reported SKIP rather than raising.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backup_restore_drill as drill  # noqa: E402
from synapse_memory.core.migrations import CURRENT_SCHEMA_VERSION  # noqa: E402


class TestBackupRestoreDrill(unittest.TestCase):
    def test_sqlite_drill_passes(self):
        workdir = tempfile.mkdtemp(prefix="drill-test-")
        evidence = drill.sqlite_drill(workdir)
        self.assertEqual(evidence["rows"], 6)
        self.assertEqual(evidence["tenants"], {"acme": 3, "globex": 3})

    def test_restored_ledger_is_current(self):
        workdir = tempfile.mkdtemp(prefix="drill-test-")
        evidence = drill.sqlite_drill(workdir)
        self.assertEqual(evidence["ledger"], CURRENT_SCHEMA_VERSION)

    def test_ledger_version_helper_reads_zero_for_empty_db(self):
        workdir = tempfile.mkdtemp(prefix="drill-test-")
        empty = os.path.join(workdir, "empty.db")
        import sqlite3

        sqlite3.connect(empty).close()
        self.assertEqual(drill.ledger_version(empty), 0)

    def test_postgres_drill_skips_without_dsn(self):
        self.assertFalse(drill.postgres_drill(""))

    def test_main_returns_zero_for_sqlite_only(self):
        self.assertEqual(drill.main([]), 0)


if __name__ == "__main__":
    unittest.main()
