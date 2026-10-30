"""Machine-readable regression contract for deterministic agent fault injection."""

from synapse_memory.fault_injection import run_fault_injection_suite


def test_all_ten_fault_injection_scenarios_pass_and_report_limitations():
    report = run_fault_injection_suite()

    assert report["fixture"]["scenario_count"] == 10
    assert report["fixture"]["type"] == "synthetic_deterministic_fault_injection"
    assert report["fixture"]["uses_llm_judge"] is False
    assert report["summary"] == {"passed": 10, "failed": 0, "all_passed": True}
    assert all(scenario["passed"] for scenario in report["scenarios"])
    assert report["metrics"]["false_positive_stop_rate"] == 0.0
    assert report["metrics"]["budget_enforcement"]["max_overshoot_tokens"] == 0
    assert report["metrics"]["budget_enforcement"]["total_budget_overshoot_tokens"] == 0
    assert report["metrics"]["instrumentation"]["redaction_passed"] is True
    assert report["metrics"]["instrumentation"]["event_delivery_failures"] == 0
    assert report["metrics"]["instrumentation"]["events_persisted"] == (
        report["metrics"]["instrumentation"]["events_emitted"]
    )
    assert report["limitations"]
