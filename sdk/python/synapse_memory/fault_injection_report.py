"""Build the deterministic fault-injection report with explicit evidence provenance.

This wrapper keeps the synthetic ten-scenario suite separate from the existing
LongMemEval-S, human-reviewed relevance, LLM-judge, and actual task-success
evidence streams. It also closes out the combined budget scenario with direct
time and tool-call admission checks.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

import synapse_memory.api.fastapi_server as server
from synapse_memory.fault_injection import run_fault_injection_suite
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.temporal import is_retrieval_eligible
from synapse_memory.runtime import (
    RunDeadlineExceeded,
    RunPolicy,
    RunToolCallLimitExceeded,
    SynapseRun,
)


class _FixtureEmbedder:
    dimension = 2
    model_name = "fault-injection-report"

    def embed_query(self, _text: str):
        return [1.0, 0.0]


def _storage_correction_scenario() -> bool:
    store = SQLiteMemoryStore(db_path=":memory:")
    manager = MemoryManager(
        store=store,
        embedder=cast(Any, _FixtureEmbedder()),
        max_record_limit=20,
    )
    tenant = "report-correction-fixture"
    old_id, old_status, _ = manager.ingest_with_deduplication(
        content="The release window is Tuesday.",
        category="fact",
        confidence=0.70,
        tenant_id=tenant,
        source_type="document",
        source_id="doc:old-release-window",
        trust_level="medium",
    )
    corrected_id, corrected_status, _ = manager.ingest_with_deduplication(
        content="Correction: the release window is Friday.",
        category="fact",
        confidence=0.95,
        tenant_id=tenant,
        source_type="document",
        source_id="doc:corrected-release-window",
        trust_level="high",
    )
    old_record = store.get_memory_by_id(old_id, tenant_id=tenant)
    corrected_record = store.get_memory_by_id(corrected_id, tenant_id=tenant)
    if old_record is None or corrected_record is None:
        store.close()
        return False
    as_of = max(time.time(), float(corrected_record["observed_at"]) + 1.0)
    success = (
        old_status == "CREATED"
        and corrected_status == "MERGED"
        and old_record["superseded_by"] == corrected_id
        and corrected_record["supersedes"] == old_id
        and not is_retrieval_eligible(old_record, as_of)
        and is_retrieval_eligible(corrected_record, as_of)
    )
    store.close()
    return bool(success)


def _protected_context_api_scenario(goal: str, token_budget: int, turns: int = 40) -> Dict[str, Any]:
    """Exercise the real /query protected-context assembly and trace path."""
    tenant_id = "fault-injection-" + uuid.uuid4().hex[:10]
    run_id = "fi-protected-context-" + uuid.uuid4().hex[:12]
    token, _ = server.security_manager.generate_api_key(
        name="fault-injection-protected-context",
        organization_id="org-fault-injection",
        project_id="project-fault-injection",
        tenant_id=tenant_id,
        scopes=[
            "memories:read",
            "agent-runs:read",
            "agent-runs:write",
            "agent-runs:delete",
        ],
        role="developer",
    )
    headers = {"X-Synapse-API-Key": token, "X-Tenant-ID": tenant_id}
    client = TestClient(server.app)

    created = client.post("/api/agent-runs", json={"run_id": run_id}, headers=headers)
    if created.status_code != 200:
        return {"api_assembly_verified": False, "failure": "run_create_failed"}

    started = client.post(
        f"/api/agent-runs/{run_id}/events",
        json={
            "schema_version": 1,
            "event_id": "evt-start-" + uuid.uuid4().hex,
            "run_id": run_id,
            "event_type": "run.started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_status": "running",
        },
        headers=headers,
    )
    if started.status_code != 200:
        return {"api_assembly_verified": False, "failure": "run_start_failed"}

    retained_turns = 0
    total_overshoot = 0
    response_token_costs = []
    with (
        patch.object(
            server.embedder,
            "embed_query",
            return_value=[0.1] * server.embedding_dimension,
        ),
        patch.object(server.store, "vector_search", return_value=[]),
        patch.object(server.store, "lexical_search", return_value=[]),
        patch.object(server.store, "get_all_memories", return_value=[]),
        patch.object(server, "ACTIVE_BACKEND", "sqlite"),
    ):
        for _turn in range(turns):
            response = client.post(
                "/query",
                json={
                    "prompt": "What is the task objective that must remain active?",
                    "maxTokens": token_budget,
                    "run_id": run_id,
                    "protected_constraints": [{"kind": "goal", "text": goal}],
                },
                headers=headers,
            )
            if response.status_code != 200:
                break
            data = response.json()["data"]
            if goal in data.get("fused_context", ""):
                retained_turns += 1
            constraint_tokens = int(data.get("protected_constraints_token_cost", 0))
            optional_tokens = sum(
                int(item.get("token_cost", 0))
                for item in data.get("injected_nodes", [])
            )
            total_overshoot = max(
                total_overshoot,
                constraint_tokens + optional_tokens - token_budget,
            )
            response_token_costs.append(constraint_tokens)

    event_response = client.get(
        f"/api/agent-runs/{run_id}/events?limit=100",
        headers=headers,
    )
    events = event_response.json().get("data", {}).get("events", []) if event_response.status_code == 200 else []
    retrieval_events = [
        event for event in events if event.get("event_type") == "memory.retrieval"
    ]
    traces_have_metadata_only = (
        len(retrieval_events) == turns
        and all(
            event.get("protected_constraints_count") == 1
            and isinstance(event.get("protected_constraints_token_cost"), int)
            for event in retrieval_events
        )
        and goal not in json.dumps(retrieval_events)
    )
    client.delete(f"/api/agent-runs/{run_id}", headers=headers)
    return {
        "api_assembly_verified": retained_turns == turns,
        "turns_requested": turns,
        "turns_completed": len(response_token_costs),
        "goal_retained_turns": retained_turns,
        "trace_events_confirmed": traces_have_metadata_only,
        "trace_event_count": len(retrieval_events),
        "max_token_overshoot": max(0, total_overshoot),
        "protected_token_costs_recorded": len(response_token_costs) == turns,
    }


def build_fault_injection_report() -> Dict[str, Any]:
    report = run_fault_injection_suite()

    tool_run = SynapseRun(
        run_id="fi-report-tool-budget",
        policy=RunPolicy(max_tool_calls=1),
    )
    tool_limit_fired = False
    with tool_run:
        tool_run.call_tool("first", {}, lambda: "ok")
        try:
            tool_run.call_tool("second", {}, lambda: "must-not-run")
        except RunToolCallLimitExceeded:
            tool_limit_fired = True

    deadline_run = SynapseRun(
        run_id="fi-report-deadline",
        policy=RunPolicy(timeout_seconds=1.0),
    )
    deadline_fired = False
    # Deterministic monotonic clock: first value starts the run, second is
    # beyond the deadline at the next admission check.
    with patch("synapse_memory.runtime.time.monotonic", side_effect=[100.0, 102.0]):
        with deadline_run:
            try:
                deadline_run.record_step()
            except RunDeadlineExceeded:
                deadline_fired = True

    correction_scenario = next(
        scenario for scenario in report["scenarios"]
        if scenario["scenario"] == "new-fact-supersedes-old-evidence"
    )
    storage_correction_confirmed = _storage_correction_scenario()
    correction_scenario["storage_supersession_confirmed"] = storage_correction_confirmed
    correction_scenario["passed"] = bool(
        correction_scenario["passed"] and storage_correction_confirmed
    )

    protected_scenario = next(
        scenario for scenario in report["scenarios"]
        if scenario["scenario"] == "protected-goal-retention-over-long-run"
    )
    api_assembly = _protected_context_api_scenario(
        goal=(
            "The objective is to update only the approved workspace and preserve "
            "the customer's existing production data."
        ),
        token_budget=512,
        turns=40,
    )
    protected_scenario.update(api_assembly)
    protected_scenario["passed"] = bool(
        protected_scenario["passed"]
        and api_assembly["api_assembly_verified"]
        and api_assembly["trace_events_confirmed"]
        and api_assembly["max_token_overshoot"] == 0
    )

    report["metrics"]["retrieval_evidence"].update({
        "protected_context_api_turns": api_assembly["turns_completed"],
        "protected_context_api_trace_events": api_assembly["trace_event_count"],
        "protected_context_api_max_overshoot_tokens": api_assembly["max_token_overshoot"],
    })

    budget_scenario = next(
        scenario for scenario in report["scenarios"]
        if scenario["scenario"] == "step-and-token-budget-enforcement"
    )
    budget_scenario["tool_limit_fired"] = tool_limit_fired
    budget_scenario["time_limit_fired"] = deadline_fired
    budget_scenario["passed"] = bool(
        budget_scenario["passed"] and tool_limit_fired and deadline_fired
    )

    report["metrics"]["budget_enforcement"].update({
        "tool_call_limit_enforced": tool_limit_fired,
        "cooperative_deadline_enforced": deadline_fired,
    })
    report["evidence_sources"] = {
        "synthetic_fault_injection": {
            "included": True,
            "fixture_count": len(report["scenarios"]),
            "status": "measured_in_this_report",
        },
        "longmemeval_s_evidence_retrieval": {
            "included": False,
            "status": "separate_ci_benchmark_artifact",
        },
        "developer_human_relevance_review": {
            "included": False,
            "status": "awaiting_independent_human_review",
        },
        "llm_judge_outputs": {
            "included": False,
            "status": "not_run",
        },
        "actual_end_to_end_task_success": {
            "included": False,
            "status": "not_measured_in_this_fixture",
        },
    }
    passed = sum(1 for scenario in report["scenarios"] if scenario["passed"])
    failed = len(report["scenarios"]) - passed
    report["summary"] = {
        "passed": passed,
        "failed": failed,
        "all_passed": failed == 0,
    }
    report["diagnostics"] = {
        "failed_scenarios": [
            scenario for scenario in report["scenarios"] if not scenario["passed"]
        ],
    }
    return report
