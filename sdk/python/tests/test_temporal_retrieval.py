"""Temporal retrieval eligibility and explicit retraction regressions."""

import unittest
from unittest.mock import patch

from synapse_memory.client import SynapseClient
from synapse_memory.core import pgvector_db
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.run_trace_store import RunTraceStore
from synapse_memory.runtime import RunEventType, RunStatus
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


class TestPythonClientTemporalMethods(unittest.TestCase):
    def test_retrieve_forwards_as_of_and_returns_effective_timestamp(self):
        client = SynapseClient()
        try:
            with patch.object(client, "_request", return_value={
                "fused_context": "current evidence",
                "injected_nodes_count": 1,
                "injected_nodes": [],
                "active_storage_backend": "sqlite",
                "retrieval_as_of": 100.0,
                "retrieval_id": "ret_test",
            }) as request:
                result = client.retrieve("project deployment", as_of=100.0, run_id="run_123")
            self.assertEqual(request.call_args.args[2]["asOf"], 100.0)
            self.assertEqual(request.call_args.args[2]["run_id"], "run_123")
            self.assertEqual(result.retrieval_as_of, 100.0)
            self.assertEqual(result.retrieval_id, "ret_test")
        finally:
            client.close()

    def test_retract_memory_uses_idempotent_authenticated_mutation(self):
        client = SynapseClient()
        try:
            with patch.object(client, "_request", return_value={"status": "RETRACTED"}) as request:
                result = client.retract_memory("mem_123", "source_corrected")
            self.assertEqual(result["status"], "RETRACTED")
            self.assertEqual(request.call_args.args[0:2], ("POST", "/api/memories/mem_123/retract"))
            self.assertEqual(request.call_args.args[2], {"reason": "source_corrected"})
            self.assertTrue(request.call_args.kwargs["retry_safe"])
        finally:
            client.close()

    def test_retraction_reason_is_a_controlled_code(self):
        client = SynapseClient()
        try:
            with self.assertRaises(ValueError):
                client.retract_memory("mem_123", "Sensitive customer details")
        finally:
            client.close()


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
        for value in (float("nan"), float("inf"), True, -1.0):
            with self.assertRaises(ValueError):
                is_retrieval_eligible({}, value)


    def test_run_trace_persists_allowlisted_temporal_evidence_without_content(self):
        store = SQLiteMemoryStore(db_path=":memory:")
        traces = RunTraceStore(store)
        traces.create_run("tenant-a", "run-temporal")
        traces.append_event("tenant-a", "run-temporal", {
            "event_id": "evt-start",
            "run_id": "run-temporal",
            "event_type": RunEventType.RUN_STARTED.value,
            "timestamp": "2026-10-30T12:00:00Z",
            "run_status": RunStatus.RUNNING.value,
        })
        traces.append_event("tenant-a", "run-temporal", {
            "event_id": "evt-retrieval",
            "run_id": "run-temporal",
            "event_type": RunEventType.MEMORY_RETRIEVAL.value,
            "timestamp": "2026-10-30T12:00:01Z",
            "run_status": RunStatus.RUNNING.value,
            "retrieval_id": "ret-temporal",
            "retrieval_as_of": 100.0,
            "memory_evidence": [{
                "memory_id": "mem-source",
                "rank": 1,
                "score_components": {"relevance_score": 0.02, "fused_score": 0.02},
                "token_cost": 15,
                "packed": True,
                "created_at": 50.0,
                "source_type": "document",
                "source_id": "doc:123",
                "observed_at": 50.0,
                "valid_from": 80.0,
                "valid_until": 120.0,
                "trust_level": "high",
            }],
        })
        events = traces.list_events("tenant-a", "run-temporal")
        retrieval = events[1]
        self.assertEqual(retrieval["retrieval_id"], "ret-temporal")
        self.assertEqual(retrieval["retrieval_as_of"], 100.0)
        self.assertEqual(retrieval["memory_evidence"][0]["source_type"], "document")
        self.assertEqual(retrieval["memory_evidence"][0]["valid_until"], 120.0)
        self.assertNotIn("content", retrieval["memory_evidence"][0])
        self.assertNotIn("query", retrieval)

class _CaptureCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params):
        self.sql = sql
        self.params = tuple(params)

    def fetchall(self):
        return []


class _CaptureConnection:
    def __init__(self):
        self.cursor_instance = _CaptureCursor()

    def cursor(self, **_kwargs):
        return self.cursor_instance


class TestPostgresTemporalQueryContract(unittest.TestCase):
    def test_hybrid_sql_filters_both_candidate_channels_before_top_k(self):
        store = object.__new__(PGVectorMemoryStore)
        store._connected = True
        store.dimension = 3
        connection = _CaptureConnection()
        store._get_connection = lambda: connection
        store._set_tenant_context = lambda _cursor, _tenant: None
        store._return_connection = lambda _connection: None

        with patch.object(pgvector_db, "HAS_PSYCOPG2", True):
            result = store.hybrid_search_sql(
                query="project deployment",
                query_vec=[0.1, 0.2, 0.3],
                top_k=7,
                category="fact",
                tenant_id="tenant-a",
                candidate_pool=11,
                temporal_as_of=100.0,
            )

        self.assertEqual(result, [])
        sql = connection.cursor_instance.sql
        params = connection.cursor_instance.params
        self.assertEqual(sql.count("retracted_at IS NULL"), 2)
        self.assertEqual(sql.count("COALESCE(observed_at, created_at) <= %s"), 2)
        self.assertEqual(sql.count("(valid_from IS NULL OR valid_from <= %s)"), 2)
        self.assertEqual(sql.count("(valid_until IS NULL OR valid_until > %s)"), 2)
        self.assertEqual(
            params,
            (
                "[0.1,0.2,0.3]", 100.0, 100.0, 100.0, "fact",
                "[0.1,0.2,0.3]", 11, "project deployment", "project deployment",
                100.0, 100.0, 100.0, "fact", "project deployment", 11, 7,
            ),
        )


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
