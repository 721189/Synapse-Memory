"""Temporal retrieval eligibility and explicit retraction regressions."""

import unittest

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.temporal import is_retrieval_eligible


def memory(
    memory_id,
    *,
    content="project deployment deadline",
    observed_at=50.0,
    valid_from=None,
    valid_until=None,
    retracted_at=None,
    superseded_by=None,
    tenant_id="tenant-a",
    embedding=None,
):
    return {
        "id": memory_id,
        "tenant_id": tenant_id,
        "content": content,
        "category": "fact",
        "confidence": 0.9,
        "created_at": 40.0,
        "last_accessed_at": 40.0,
        "access_count": 0,
        "token_cost": 20,
        "embedding": embedding or [1.0, 0.0],
        "feedback_multiplier": 1.0,
        "embedding_dimension": 2,
        "model_version": "test",
        "supersedes": None,
        "superseded_by": superseded_by,
        "source_type": "document",
        "source_id": "doc:test",
        "observed_at": observed_at,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "trust_level": "high",
        "supersession_reason": None,
        "retracted_at": retracted_at,
        "retraction_reason": "source_corrected" if retracted_at is not None else None,
    }


class TestTemporalEligibility(unittest.TestCase):
    def test_half_open_validity_window(self):
        record = {"observed_at": 10.0, "valid_from": 20.0, "valid_until": 30.0}
        self.assertFalse(is_retrieval_eligible(record, 19.999))
        self.assertTrue(is_retrieval_eligible(record, 20.0))
        self.assertTrue(is_retrieval_eligible(record, 29.999))
        self.assertFalse(is_retrieval_eligible(record, 30.0))

    def test_future_observation_is_not_evidence_yet(self):
        self.assertFalse(is_retrieval_eligible(
            {"created_at": 20.0, "observed_at": 101.0}, 100.0
        ))

    def test_legacy_rows_use_creation_time_as_observation_fallback(self):
        self.assertTrue(is_retrieval_eligible({"created_at": 90.0}, 100.0))
        self.assertFalse(is_retrieval_eligible({"created_at": 110.0}, 100.0))

    def test_superseded_and_retracted_records_are_never_eligible(self):
        self.assertFalse(is_retrieval_eligible({"observed_at": 10.0, "superseded_by": "mem-new"}, 100.0))
        self.assertFalse(is_retrieval_eligible({"observed_at": 10.0, "retracted_at": 20.0}, 100.0))

    def test_non_finite_as_of_is_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                is_retrieval_eligible({}, value)


class TestSQLiteTemporalRetrievalAndRetraction(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteMemoryStore(db_path=":memory:")
        self.store.insert_memory(memory("valid", valid_from=80.0, valid_until=120.0))
        self.store.insert_memory(memory("expired", valid_from=80.0, valid_until=100.0))
        self.store.insert_memory(memory("not-yet-valid", valid_from=101.0))
        self.store.insert_memory(memory("not-yet-observed", observed_at=101.0))
        self.store.insert_memory(memory("retracted", retracted_at=90.0))
        self.store.insert_memory(memory("superseded", superseded_by="mem-successor"))

    def test_temporal_filter_runs_before_dense_and_lexical_top_k(self):
        dense = self.store.vector_search([1.0, 0.0], top_k=20, tenant_id="tenant-a", temporal_as_of=100.0)
        lexical = self.store.lexical_search("project deployment", top_k=20, tenant_id="tenant-a", temporal_as_of=100.0)
        self.assertEqual({row["id"] for row in dense}, {"valid"})
        self.assertEqual({row["id"] for row in lexical}, {"valid"})

    def test_retraction_is_tenant_scoped_idempotent_and_preserves_record(self):
        self.assertFalse(self.store.retract_memory("valid", "tenant-b", "source_corrected", retracted_at=99.0))
        self.assertTrue(self.store.retract_memory("valid", "tenant-a", "source_corrected", retracted_at=99.0))
        self.assertFalse(self.store.retract_memory("valid", "tenant-a", "source_corrected", retracted_at=100.0))

        persisted = self.store.get_memory_by_id("valid", tenant_id="tenant-a")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["retracted_at"], 99.0)
        self.assertEqual(persisted["retraction_reason"], "source_corrected")
        visible = self.store.lexical_search("project deployment", tenant_id="tenant-a", temporal_as_of=100.0)
        self.assertNotIn("valid", {row["id"] for row in visible})


if __name__ == "__main__":
    unittest.main()
