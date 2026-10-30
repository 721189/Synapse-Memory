"""Build the deterministic fault-injection report with explicit evidence provenance.

This wrapper keeps the synthetic ten-scenario suite separate from the existing
LongMemEval-S, human-reviewed relevance, LLM-judge, and actual task-success
evidence streams. It also closes out the combined budget scenario with direct
time and tool-call admission checks.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch
import time

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
        embedder=_FixtureEmbedder(),
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
