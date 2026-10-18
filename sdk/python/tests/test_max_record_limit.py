"""Tests for the explicit MAX record limit (SYNAPSE_MAX_RECORD_LIMIT).

The historical default max_record_limit is 50. Operators must be able to
raise/lower it explicitly; ingesting more than the limit must prune down to
it rather than silently dropping older memories.
"""
import importlib
import os
import tempfile
import uuid
import unittest

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder

import synapse_memory.core.memory_manager as mm


def _make_manager(db_path, limit=None):
    store = SQLiteMemoryStore(db_path=db_path)
    embedder = SynapseEmbedder(provider="local")
    kwargs = {"store": store, "embedder": embedder}
    if limit is not None:
        kwargs["max_record_limit"] = limit
    return mm.MemoryManager(**kwargs), store


class TestMaxRecordLimitDefault(unittest.TestCase):
    def test_default_limit_is_fifty(self):
        self.assertEqual(mm.DEFAULT_MAX_RECORD_LIMIT, 50)

    def test_env_override_changes_default_limit(self):
        os.environ["SYNAPSE_MAX_RECORD_LIMIT"] = "120"
        try:
            importlib.reload(mm)
            self.assertEqual(mm.DEFAULT_MAX_RECORD_LIMIT, 120)
        finally:
            del os.environ["SYNAPSE_MAX_RECORD_LIMIT"]
            importlib.reload(mm)
            self.assertEqual(mm.DEFAULT_MAX_RECORD_LIMIT, 50)

    def test_invalid_env_falls_back_to_fifty(self):
        os.environ["SYNAPSE_MAX_RECORD_LIMIT"] = "not-a-number"
        try:
            importlib.reload(mm)
            self.assertEqual(mm.DEFAULT_MAX_RECORD_LIMIT, 50)
        finally:
            del os.environ["SYNAPSE_MAX_RECORD_LIMIT"]
            importlib.reload(mm)


class TestMaxRecordLimitEnforcement(unittest.TestCase):
    def setUp(self):
        self.db_path = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass

    def test_inserting_more_than_fifty_prunes_to_limit(self):
        manager, store = _make_manager(self.db_path, limit=50)
        try:
            created = 0
            for i in range(70):
                mem_id, action, _ = manager.ingest_with_deduplication(
                    content=f"distinct event log {i} payload-{uuid.uuid4().hex[:8]}",
                    category=f"cat-{i}",  # dedup is category-scoped; isolates the capacity limit
                    confidence=0.9,
                )
                if action == "CREATED":
                    created += 1
            self.assertEqual(created, 70)
            # 70 distinct memories ingested, but the 50-record cap prunes down to 50.
            remaining = store.get_all_memories(tenant_id="default")
            self.assertEqual(len(remaining), 50)
            self.assertEqual(store.count_memories(tenant_id="default"), 50)
            self.assertEqual(manager.max_record_limit, 50)
        finally:
            store.close()

    def test_explicit_limit_above_fifty_retains_all(self):
        manager, store = _make_manager(self.db_path, limit=120)
        try:
            for i in range(100):
                manager.ingest_with_deduplication(
                    content=f"distinct event log {i} payload-{uuid.uuid4().hex[:8]}",
                    category=f"cat-{i}",  # dedup is category-scoped; isolates the capacity limit
                    confidence=0.9,
                )
            # Above the old hard-coded 50 default: no pruning, nothing lost.
            remaining = store.get_all_memories(tenant_id="default")
            self.assertEqual(len(remaining), 100)
            self.assertEqual(manager.max_record_limit, 120)
        finally:
            store.close()

    def test_manager_honors_env_default_without_explicit_argument(self):
        os.environ["SYNAPSE_MAX_RECORD_LIMIT"] = "77"
        try:
            importlib.reload(mm)
            manager, store = _make_manager(self.db_path)
            try:
                self.assertEqual(manager.max_record_limit, 77)
            finally:
                store.close()
        finally:
            del os.environ["SYNAPSE_MAX_RECORD_LIMIT"]
            importlib.reload(mm)


if __name__ == "__main__":
    unittest.main()
