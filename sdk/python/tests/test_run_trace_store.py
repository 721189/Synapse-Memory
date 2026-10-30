"""Tenant-isolation and lifecycle tests for persistent agent run traces."""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from synapse_memory.core.run_trace_store import (
    RunConflictError,
    RunNotFoundError,
    RunTraceStore,
)
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.runtime import (
    RunEventType, RunPolicy, RunStatus, RunToolCallLimitExceeded, SynapseRun,
)


class TestRunTraceStore(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.backend = SQLiteMemoryStore(self.db_path, fail_closed=True)
        self.traces = RunTraceStore(self.backend)

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass

    def _completed_run(self, run_id="trace-run-01"):
        run = SynapseRun(run_id=run_id, policy=RunPolicy(max_steps=8, max_tool_calls=2))
        with run:
            run.record_memory_retrieval(
                [
                    {
                        "id": "memory-01",
                        "rank": 1,
                        "relevance_score": 0.91,
                        "retrieval_score": 0.88,
                        "token_cost": 17,
                        "created_at": 1_760_000_000.0,
                        "supersedes": "memory-old",
                        "content": "PRIVATE_MEMORY_CONTENT_MUST_NOT_BE_RECORDED",
                        "query": "PRIVATE_QUERY_MUST_NOT_BE_RECORDED",
                    },
                    {
                        "id": "memory-02",
                        "rank": 2,
                        "relevance_score": 0.43,
                        "token_cost": 19,
                        "created_at": 1_759_000_000.0,
                    },
                ],
                packed_memory_ids=["memory-01"],
                retrieval_id="retrieval-01",
            )
            run.call_tool("lookup_customer", {"customer_id": "cust-01"}, lambda **kwargs: "private result")
        return run

    def _persist(self, tenant_id, run):
        self.traces.create_run(tenant_id, run.run_id)
        results = [
            self.traces.append_event(tenant_id, run.run_id, event.to_dict())
            for event in run.events
        ]
        return results

    def test_persists_ordered_events_and_metadata_only_retrieval_evidence(self):
        run = self._completed_run()
        self._persist("tenant-alpha", run)

        summary = self.traces.get_run("tenant-alpha", run.run_id)
        events = self.traces.list_events("tenant-alpha", run.run_id, limit=100)

        self.assertEqual(summary["status"], RunStatus.SUCCEEDED.value)
        self.assertEqual(summary["event_count"], len(run.events))
        self.assertEqual([e["sequence_number"] for e in events], list(range(1, len(run.events) + 1)))
        retrieval = next(e for e in events if e["event_type"] == RunEventType.MEMORY_RETRIEVAL.value)
        self.assertEqual(retrieval["retrieval_id"], "retrieval-01")
        self.assertEqual(retrieval["memory_evidence"][0]["memory_id"], "memory-01")
        self.assertTrue(retrieval["memory_evidence"][0]["packed"])
        self.assertFalse(retrieval["memory_evidence"][1]["packed"])
        self.assertEqual(retrieval["memory_evidence"][0]["score_components"]["relevance_score"], 0.91)

        serialized = repr(events)
        self.assertNotIn("PRIVATE_MEMORY_CONTENT_MUST_NOT_BE_RECORDED", serialized)
        self.assertNotIn("PRIVATE_QUERY_MUST_NOT_BE_RECORDED", serialized)
        self.assertNotIn("private result", serialized)
        self.assertNotIn("cust-01", serialized)
        self.assertNotIn("customer_id", serialized)

    def test_create_run_and_event_replay_are_idempotent(self):
        run = self._completed_run("trace-idempotent")
        first_create = self.traces.create_run("tenant-alpha", run.run_id)
        second_create = self.traces.create_run("tenant-alpha", run.run_id)
        self.assertTrue(first_create["created"])
        self.assertFalse(second_create["created"])

        first_event = run.events[0].to_dict()
        inserted = self.traces.append_event("tenant-alpha", run.run_id, first_event)
        replayed = self.traces.append_event("tenant-alpha", run.run_id, first_event)
        self.assertFalse(inserted["duplicate"])
        self.assertTrue(replayed["duplicate"])
        self.assertEqual(inserted["sequence_number"], replayed["sequence_number"])

    def test_reusing_event_id_for_different_event_is_rejected(self):
        run = self._completed_run("trace-conflict")
        self.traces.create_run("tenant-alpha", run.run_id)
        first_event = run.events[0].to_dict()
        self.traces.append_event("tenant-alpha", run.run_id, first_event)

        modified = dict(first_event)
        modified["run_status"] = RunStatus.FAILED.value
        with self.assertRaises(RunConflictError):
            self.traces.append_event("tenant-alpha", run.run_id, modified)

    def test_tenant_boundary_applies_to_reads_and_deletes(self):
        run = self._completed_run("trace-tenant-boundary")
        self._persist("tenant-alpha", run)

        with self.assertRaises(RunNotFoundError):
            self.traces.get_run("tenant-beta", run.run_id)
        with self.assertRaises(RunNotFoundError):
            self.traces.list_events("tenant-beta", run.run_id)
        self.assertFalse(self.traces.delete_run("tenant-beta", run.run_id))
        self.assertEqual(self.traces.get_run("tenant-alpha", run.run_id)["event_count"], len(run.events))

    def test_terminal_run_rejects_new_non_duplicate_events(self):
        run = self._completed_run("trace-terminal")
        self._persist("tenant-alpha", run)
        late_event = {
            "schema_version": 1,
            "event_id": "late-event",
            "run_id": run.run_id,
            "event_type": RunEventType.STEP_STARTED.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_status": RunStatus.RUNNING.value,
            "step_index": 99,
        }
        with self.assertRaises(RunConflictError):
            self.traces.append_event("tenant-alpha", run.run_id, late_event)

    def test_arbitrary_retrieval_content_is_rejected_by_persistence_contract(self):
        run = self._completed_run("trace-untrusted-metadata")
        self.traces.create_run("tenant-alpha", run.run_id)
        event = next(e.to_dict() for e in run.events if e.event_type is RunEventType.MEMORY_RETRIEVAL)
        event["memory_evidence"][0]["content"] = "must not be stored"
        with self.assertRaises(ValueError):
            self.traces.append_event("tenant-alpha", run.run_id, event)

    def test_tool_call_limit_events_persist_until_terminal_finish(self):
        run = SynapseRun(
            run_id="trace-limit-events",
            policy=RunPolicy(max_tool_calls=1),
        )
        with run:
            run.call_tool("first_tool", {}, lambda: "ok")
            with self.assertRaises(RunToolCallLimitExceeded):
                run.call_tool("blocked_tool", {}, lambda: "must-not-run")

        self.assertEqual(run.status, RunStatus.LIMIT_EXCEEDED)
        self._persist("tenant-alpha", run)
        events = self.traces.list_events("tenant-alpha", run.run_id)
        types = [event["event_type"] for event in events]
        self.assertIn(RunEventType.TOOL_CALL_BLOCKED.value, types)
        self.assertIn(RunEventType.LIMIT_EXCEEDED.value, types)
        self.assertEqual(types[-1], RunEventType.RUN_FINISHED.value)
        self.assertEqual(self.traces.get_run("tenant-alpha", run.run_id)["status"], "limit_exceeded")

    def test_duplicate_retrieval_event_with_changed_evidence_is_conflict(self):
        run = self._completed_run("trace-evidence-conflict")
        self.traces.create_run("tenant-alpha", run.run_id)
        retrieval = next(
            event.to_dict()
            for event in run.events
            if event.event_type is RunEventType.MEMORY_RETRIEVAL
        )
        self.traces.append_event("tenant-alpha", run.run_id, run.events[0].to_dict())
        self.traces.append_event("tenant-alpha", run.run_id, retrieval)
        changed = dict(retrieval)
        changed["memory_evidence"] = [dict(item) for item in retrieval["memory_evidence"]]
        changed["memory_evidence"][0]["score_components"] = {"relevance_score": 0.01}
        with self.assertRaises(RunConflictError):
            self.traces.append_event("tenant-alpha", run.run_id, changed)

    def test_retry_and_postcondition_events_persist_as_metadata_only(self):
        calls = 0
        secret = "PRIVATE_TOOL_RESULT_SHOULD_NOT_ENTER_TRACE"

        def transient_tool(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("temporary connection failure")
            return {"receipt": secret}

        run = SynapseRun(
            run_id="trace-retry-verify",
            policy=RunPolicy(max_steps=10, max_tool_calls=4, max_retry_attempts=2),
        )
        with run:
            run.call_tool_with_retry(
                "read_receipt",
                {"receipt_id": "receipt-1"},
                transient_tool,
                retry_safe=True,
                retry_on=(ConnectionError,),
            )
            run.call_tool_verified(
                "verify_write",
                {"resource_id": "resource-1"},
                lambda **_kwargs: {"accepted": True, "persisted": True},
                postcondition=lambda result: result["persisted"] is True,
            )

        self._persist("tenant-alpha", run)
        events = self.traces.list_events("tenant-alpha", run.run_id)
        types = [event["event_type"] for event in events]
        self.assertIn(RunEventType.TOOL_CALL_RETRYING.value, types)
        self.assertIn(RunEventType.TOOL_CALL_VERIFICATION_PASSED.value, types)
        serialized = repr(events)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("receipt_id", serialized)
        self.assertNotIn("resource_id", serialized)

    def test_delete_removes_run_and_events(self):
        run = self._completed_run("trace-delete")
        self._persist("tenant-alpha", run)
        self.assertTrue(self.traces.delete_run("tenant-alpha", run.run_id))
        with self.assertRaises(RunNotFoundError):
            self.traces.get_run("tenant-alpha", run.run_id)
        self.assertFalse(self.traces.delete_run("tenant-alpha", run.run_id))


if __name__ == "__main__":
    unittest.main()
