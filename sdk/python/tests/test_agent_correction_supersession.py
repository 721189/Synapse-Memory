"""Storage-backed correction/supersession regression for reliability fixtures."""

import time

from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.temporal import is_retrieval_eligible


class FixedEmbedder:
    dimension = 2
    model_name = "fault-injection-fixed"

    def embed_query(self, _text):
        return [1.0, 0.0]


def test_new_correction_supersedes_and_excludes_old_evidence():
    store = SQLiteMemoryStore(db_path=":memory:")
    manager = MemoryManager(store=store, embedder=FixedEmbedder(), max_record_limit=20)
    tenant = "correction-fixture"

    old_id, old_status, _ = manager.ingest_with_deduplication(
        content="The customer rollout date is Tuesday.",
        category="fact",
        confidence=0.70,
        tenant_id=tenant,
        source_type="document",
        source_id="doc:old-schedule",
        trust_level="medium",
    )
    corrected_id, corrected_status, _ = manager.ingest_with_deduplication(
        content="Correction: the customer rollout date is Friday.",
        category="fact",
        confidence=0.95,
        tenant_id=tenant,
        source_type="document",
        source_id="doc:corrected-schedule",
        trust_level="high",
    )

    old_record = store.get_memory_by_id(old_id, tenant_id=tenant)
    corrected_record = store.get_memory_by_id(corrected_id, tenant_id=tenant)
    assert old_status == "CREATED"
    assert corrected_status == "MERGED"
    assert old_record["superseded_by"] == corrected_id
    assert corrected_record["supersedes"] == old_id

    as_of = max(time.time(), corrected_record["observed_at"] + 1.0)
    assert is_retrieval_eligible(old_record, as_of) is False
    assert is_retrieval_eligible(corrected_record, as_of) is True

    store.close()
