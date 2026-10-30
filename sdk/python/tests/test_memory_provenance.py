"""Versioned memory provenance metadata persistence and validation."""

import unittest

from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore


class FixedEmbedder:
    dimension = 2
    model_name = "test-fixed"

    def embed_query(self, _text):
        return [1.0, 0.0]


class TestMemoryProvenance(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteMemoryStore(db_path=":memory:")
        self.manager = MemoryManager(
            store=self.store,
            embedder=FixedEmbedder(),
            max_record_limit=20,
        )

    def test_explicit_provenance_round_trips(self):
        memory_id, status, _ = self.manager.ingest_with_deduplication(
            content="The deployment window is Tuesday",
            category="fact",
            tenant_id="provenance-test",
            source_type="document",
            source_id="doc:release-calendar",
            observed_at=1_800_000_000.0,
            valid_from=1_799_000_000.0,
            valid_until=1_801_000_000.0,
            trust_level="high",
        )

        self.assertEqual(status, "CREATED")
        memory = self.store.get_memory_by_id(memory_id, tenant_id="provenance-test")
        self.assertIsNotNone(memory)
        self.assertEqual(memory["source_type"], "document")
        self.assertEqual(memory["source_id"], "doc:release-calendar")
        self.assertEqual(memory["observed_at"], 1_800_000_000.0)
        self.assertEqual(memory["valid_from"], 1_799_000_000.0)
        self.assertEqual(memory["valid_until"], 1_801_000_000.0)
        self.assertEqual(memory["trust_level"], "high")
        self.assertIsNone(memory["supersession_reason"])
        self.assertIsNone(memory["retracted_at"])

    def test_legacy_call_gets_explicit_unknown_provenance(self):
        memory_id, _, _ = self.manager.ingest_with_deduplication(
            content="Legacy caller did not provide provenance",
            category="fact",
            tenant_id="provenance-test",
        )
        memory = self.store.get_memory_by_id(memory_id, tenant_id="provenance-test")
        self.assertEqual(memory["source_type"], "unspecified")
        self.assertEqual(memory["trust_level"], "unverified")
        self.assertIsNotNone(memory["observed_at"])
        self.assertIsNone(memory["valid_from"])
        self.assertIsNone(memory["valid_until"])

    def test_invalid_validity_window_is_rejected_before_write(self):
        with self.assertRaisesRegex(ValueError, "valid_until"):
            self.manager.ingest_with_deduplication(
                content="Invalid time window",
                category="fact",
                tenant_id="provenance-test",
                valid_from=200.0,
                valid_until=100.0,
            )
        self.assertEqual(self.store.get_all_memories(tenant_id="provenance-test"), [])

    def test_source_id_rejects_free_form_content(self):
        with self.assertRaisesRegex(ValueError, "source_id"):
            self.manager.ingest_with_deduplication(
                content="Do not persist arbitrary source text as an identifier",
                category="fact",
                tenant_id="provenance-test",
                source_type="document",
                source_id="customer@example.com",
            )
        self.assertEqual(self.store.get_all_memories(tenant_id="provenance-test"), [])


if __name__ == "__main__":
    unittest.main()
