"""Report contract keeps synthetic, benchmark, judged, and task-success evidence separate."""

from synapse_memory.fault_injection_report import build_fault_injection_report


def test_fault_injection_report_covers_all_ten_scenarios_and_separates_evidence():
    report = build_fault_injection_report()

    assert report["fixture"]["scenario_count"] == 10
    assert report["summary"] == {"passed": 10, "failed": 0, "all_passed": True}
    assert all(scenario["passed"] for scenario in report["scenarios"])
    assert report["metrics"]["budget_enforcement"]["tool_call_limit_enforced"] is True
    assert report["metrics"]["budget_enforcement"]["cooperative_deadline_enforced"] is True
    assert report["metrics"]["tenant_isolation"]["cross_tenant_memory_read_denied"] is True
    assert report["metrics"]["tenant_isolation"]["cross_tenant_trace_read_denied"] is True
    assert report["metrics"]["retrieval_evidence"]["protected_context_api_turns"] == 40
    assert report["metrics"]["retrieval_evidence"]["protected_context_api_trace_events"] == 40
    assert report["metrics"]["true_negative"] == 1
    assert report["metrics"]["false_positive_stop_rate"] == 0.0
    assert report["evidence_sources"]["synthetic_fault_injection"]["included"] is True
    assert report["evidence_sources"]["longmemeval_s_evidence_retrieval"]["included"] is False
    assert report["evidence_sources"]["developer_human_relevance_review"]["status"] == (
        "awaiting_independent_human_review"
    )
    assert report["evidence_sources"]["llm_judge_outputs"]["status"] == "not_run"
    assert report["evidence_sources"]["actual_end_to_end_task_success"]["status"] == (
        "not_measured_in_this_fixture"
    )
