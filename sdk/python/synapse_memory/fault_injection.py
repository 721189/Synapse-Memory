"""Deterministic synthetic fault-injection harness for Synapse agent runs.

Run with:
    PYTHONPATH=sdk/python python -m synapse_memory.fault_injection --output artifacts/agent-reliability.json

This fixture suite is intentionally synthetic. It measures guard behavior and
instrumentation overhead; it is not a production reliability percentage or an
LLM-judge/real-world task-success benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from synapse_memory.core.auth import APIKeyRecord, can_assign_memory_trust
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.memory_manager import estimate_token_cost_upper_bound
from synapse_memory.core.run_trace_store import RunNotFoundError, RunTraceStore
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.temporal import is_retrieval_eligible
from synapse_memory.runtime import (
    PostconditionFailed,
    RepeatedToolCallError,
    RunEventType,
    RunPolicy,
    RunStatus,
    RunStepLimitExceeded,
    SynapseRun,
)


def _principal(scopes: List[str]) -> APIKeyRecord:
    return APIKeyRecord(
        key_id="fixture-key",
        key_hash="",
        name="fault-injection-fixture",
        organization_id="org-fixture",
        project_id="project-fixture",
        tenant_id="tenant-fixture",
        scopes=scopes,
        role="developer",
        is_active=True,
        expires_at=None,
        created_at=0.0,
        last_used_at=None,
    )


def _scenario(name: str, passed: bool, **details: Any) -> Dict[str, Any]:
    return {"scenario": name, "passed": bool(passed), **details}


def run_fault_injection_suite() -> Dict[str, Any]:
    """Execute ten repeatable local scenarios and return a machine-readable report."""
    scenarios: List[Dict[str, Any]] = []
    detections: List[Dict[str, bool]] = []

    # 1. Repeated identical failures are blocked before a third execution.
    repeated_attempts = 0

    def always_fails(**_kwargs: Any) -> None:
        nonlocal repeated_attempts
        repeated_attempts += 1
        raise ValueError("synthetic tool failure")

    repeated_run = SynapseRun(
        run_id="fi-repeated-failure",
        policy=RunPolicy(max_repeated_failures=2),
    )
    blocked = False
    with repeated_run:
        for _ in range(2):
            try:
                repeated_run.call_tool("lookup", {"record_id": "r-1"}, always_fails)
            except ValueError:
                pass
        try:
            repeated_run.call_tool("lookup", {"record_id": "r-1"}, always_fails)
        except RepeatedToolCallError:
            blocked = True
    scenarios.append(_scenario(
        "repeated-identical-tool-failures",
        blocked and repeated_attempts == 2 and repeated_run.status is RunStatus.LIMIT_EXCEEDED,
        attempts=repeated_attempts,
        expected_max_attempts=2,
        blocked_before_third_execution=blocked,
    ))
    detections.append({"expected": True, "detected": blocked})

    # 2. A transient error is retried only after explicit replay-safety opt-in.
    safe_attempts = 0

    def transient_then_succeed(**_kwargs: Any) -> Dict[str, str]:
        nonlocal safe_attempts
        safe_attempts += 1
        if safe_attempts == 1:
            raise ConnectionError("fixture transient read failure")
        return {"status": "ready"}

    safe_run = SynapseRun(
        run_id="fi-safe-retry",
        policy=RunPolicy(max_tool_calls=3, max_retry_attempts=2),
    )
    with safe_run:
        safe_result = safe_run.call_tool_with_retry(
            "read_status",
            {"resource_id": "res-1"},
            transient_then_succeed,
            retry_safe=True,
            retry_on=(ConnectionError,),
        )
    retry_events = sum(
        event.event_type is RunEventType.TOOL_CALL_RETRYING for event in safe_run.events
    )
    scenarios.append(_scenario(
        "explicit-safe-transient-retry",
        safe_attempts == 2 and safe_result == {"status": "ready"} and retry_events == 1,
        attempts=safe_attempts,
        retry_ceiling=safe_run.policy.max_retry_attempts,
        retry_events=retry_events,
        succeeded=safe_run.status is RunStatus.SUCCEEDED,
    ))

    # 3. A write that may have committed before a timeout is not replayed.
    side_effects: List[str] = []

    def ambiguous_write(**_kwargs: Any) -> None:
        side_effects.append("write-may-have-committed")
        raise TimeoutError("acknowledgement lost")

    ambiguous_run = SynapseRun(run_id="fi-ambiguous-write")
    with ambiguous_run:
        try:
            ambiguous_run.call_tool_with_retry(
                "create_invoice",
                {"invoice_id": "inv-1"},
                ambiguous_write,
                retry_safe=False,
            )
        except TimeoutError:
            pass
    retry_blocked = any(
        event.event_type is RunEventType.TOOL_CALL_RETRY_BLOCKED
        for event in ambiguous_run.events
    )
    scenarios.append(_scenario(
        "ambiguous-non-idempotent-write",
        len(side_effects) == 1 and retry_blocked,
        side_effect_count=len(side_effects),
        retry_blocked=retry_blocked,
        unguarded_naive_retry_reference_attempts=2,
    ))
    detections.append({"expected": True, "detected": retry_blocked})

    # 4. A required goal remains in every context assembly across a long run.
    required_goal = (
        "The objective is to update only the approved workspace and preserve "
        "the customer's existing production data."
    )
    token_budget = 512
    reserved_tokens = estimate_token_cost_upper_bound(
        "## Protected task constraints\n" + required_goal + "\n\n---\n\n"
    )
    packer = KnapsackPacker(token_budget=token_budget)
    memories = [
        {
            "id": f"memory-{index}",
            "content": f"Optional project fact {index}",
            "token_cost": 24,
            "relevance_score": 0.99 - index * 0.03,
        }
        for index in range(12)
    ]
    long_run = SynapseRun(
        run_id="fi-long-run-goal",
        policy=RunPolicy(max_steps=80, timeout_seconds=30.0),
    )
    retained_every_turn = True
    max_budget_overshoot = 0
    evidence_relevant = {"memory-0", "memory-1"}
    relevant_packed: set[str] = set()
    last_packed: List[Dict[str, Any]] = []
    with long_run:
        for _turn in range(40):
            packed = packer.pack(
                memories,
                token_budget=token_budget,
                reserved_token_cost=reserved_tokens,
            )
            packed_cost = sum(int(item["token_cost"]) for item in packed)
            max_budget_overshoot = max(
                max_budget_overshoot, reserved_tokens + packed_cost - token_budget
            )
            ids = {item["id"] for item in packed}
            relevant_packed = evidence_relevant.intersection(ids)
            context = required_goal + "\n" + "\n".join(item["content"] for item in packed)
            retained_every_turn = retained_every_turn and required_goal in context
            last_packed = packed
            long_run.record_step()
        long_run.record_memory_retrieval(
            [
                {
                    "id": item["id"],
                    "rank": index + 1,
                    "relevance_score": item["relevance_score"],
                    "token_cost": item["token_cost"],
                    "packed": item["id"] in {row["id"] for row in last_packed},
                }
                for index, item in enumerate(memories)
            ],
            packed_memory_ids=[item["id"] for item in last_packed],
            retrieval_id="fi-long-run-retrieval",
        )
    scenarios.append(_scenario(
        "protected-goal-retention-over-long-run",
        retained_every_turn and max_budget_overshoot == 0,
        turns=40,
        protected_tokens_reserved=reserved_tokens,
        context_token_budget=token_budget,
        max_budget_overshoot=max_budget_overshoot,
        retained_relevant_evidence=len(relevant_packed),
        expected_relevant_evidence=len(evidence_relevant),
    ))

    # 5. Corrected evidence supersedes the older assertion at retrieval time.
    old_fact = {
        "observed_at": 100.0,
        "superseded_by": "memory-corrected",
        "valid_from": 90.0,
    }
    corrected_fact = {
        "observed_at": 150.0,
        "supersedes": "memory-old",
        "valid_from": 140.0,
    }
    old_excluded = not is_retrieval_eligible(old_fact, 200.0)
    new_eligible = is_retrieval_eligible(corrected_fact, 200.0)
    scenarios.append(_scenario(
        "new-fact-supersedes-old-evidence",
        old_excluded and new_eligible,
        old_evidence_excluded=old_excluded,
        corrected_evidence_eligible=new_eligible,
    ))
    detections.append({"expected": True, "detected": old_excluded})

    # 6. Instruction-like user content cannot self-promote its trust level.
    untrusted_writer = _principal(["memories:read", "memories:write"])
    trusted_writer = _principal(["memories:read", "memories:write", "memories:trust"])
    trust_blocked = not can_assign_memory_trust(untrusted_writer, "authoritative")
    trusted_allowed = can_assign_memory_trust(trusted_writer, "authoritative")
    scenarios.append(_scenario(
        "untrusted-instruction-cannot-elevate-memory-trust",
        trust_blocked and trusted_allowed,
        ordinary_writer_blocked=trust_blocked,
        explicit_trust_scope_allowed=trusted_allowed,
    ))
    detections.append({"expected": True, "detected": trust_blocked})

    # 7. Tenant B cannot inspect tenant A's trace.
    sqlite = SQLiteMemoryStore(db_path=":memory:", fail_closed=True)
    traces = RunTraceStore(sqlite)
    traces.create_run("tenant-a", "fi-tenant-run")
    traces.append_event("tenant-a", "fi-tenant-run", {
        "schema_version": 1,
        "event_id": "fi-tenant-start",
        "run_id": "fi-tenant-run",
        "event_type": RunEventType.RUN_STARTED.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_status": RunStatus.RUNNING.value,
    })
    cross_tenant_denied = False
    try:
        traces.get_run("tenant-b", "fi-tenant-run")
    except RunNotFoundError:
        cross_tenant_denied = True
    scenarios.append(_scenario(
        "cross-tenant-trace-read-denied",
        cross_tenant_denied,
        tenant_a_run_created=True,
        tenant_b_read_denied=cross_tenant_denied,
    ))
    detections.append({"expected": True, "detected": cross_tenant_denied})

    # 8. Step and token budgets stop work before overrun.
    budget_run = SynapseRun(
        run_id="fi-budget-limit",
        policy=RunPolicy(max_steps=1, max_tool_calls=1),
    )
    step_limit_fired = False
    with budget_run:
        budget_run.record_step()
        try:
            budget_run.record_step()
        except RunStepLimitExceeded:
            step_limit_fired = True
    budget_reservation = 400
    budget_total = 512
    packed_under_remaining = packer.pack(
        memories,
        token_budget=budget_total,
        reserved_token_cost=budget_reservation,
    )
    packed_total = budget_reservation + sum(int(item["token_cost"]) for item in packed_under_remaining)
    budget_overshoot = max(0, packed_total - budget_total)
    scenarios.append(_scenario(
        "step-and-token-budget-enforcement",
        step_limit_fired and budget_overshoot == 0,
        step_limit_fired=step_limit_fired,
        token_budget=budget_total,
        protected_reservation=budget_reservation,
        total_tokens_estimated=packed_total,
        token_budget_overshoot=budget_overshoot,
    ))
    detections.append({"expected": True, "detected": step_limit_fired and budget_overshoot == 0})

    # 9. A success response is rejected when a separate postcondition fails.
    verification_run = SynapseRun(run_id="fi-postcondition")
    verification_failed = False
    with verification_run:
        try:
            verification_run.call_tool_verified(
                "publish",
                {"release_id": "release-fixture"},
                lambda **_kwargs: {"api_status": "accepted", "persisted": False},
                postcondition=lambda result: result["persisted"] is True,
            )
        except PostconditionFailed:
            verification_failed = True
    verification_event = any(
        event.event_type is RunEventType.TOOL_CALL_VERIFICATION_FAILED
        for event in verification_run.events
    )
    scenarios.append(_scenario(
        "tool-success-with-failed-postcondition",
        verification_failed and verification_event and verification_run.status is RunStatus.FAILED,
        reported_tool_success_event=any(
            event.event_type is RunEventType.TOOL_CALL_SUCCEEDED
            for event in verification_run.events
        ),
        independent_verification_failed=verification_event,
        terminal_status=verification_run.status.value,
    ))
    detections.append({"expected": True, "detected": verification_failed and verification_event})

    # 10. Cancellation/interruption produces a terminal aborted state.
    cancelled_run = SynapseRun(run_id="fi-cancelled")
    cancellation_propagated = False
    try:
        with cancelled_run:
            raise asyncio.CancelledError("synthetic cancellation")
    except asyncio.CancelledError:
        cancellation_propagated = True
    cancellation_clear = (
        cancellation_propagated
        and cancelled_run.status is RunStatus.ABORTED
        and cancelled_run.events[-1].event_type is RunEventType.RUN_FINISHED
        and cancelled_run.events[-1].reason == "aborted"
    )
    scenarios.append(_scenario(
        "cancellation-leaves-clear-terminal-state",
        cancellation_clear,
        cancellation_propagated=cancellation_propagated,
        terminal_status=cancelled_run.status.value,
        terminal_event_reason=cancelled_run.events[-1].reason,
    ))
    detections.append({"expected": True, "detected": cancellation_clear})

    # Record representative instrumentation/storage overhead separately from
    # functional pass/fail: these wall-clock values are machine-specific.
    plain_count = 80
    plain_started = time.perf_counter_ns()
    for value in range(plain_count):
        value + 1
    plain_ns = time.perf_counter_ns() - plain_started

    instrumented_run = SynapseRun(
        run_id="fi-instrumentation-overhead",
        policy=RunPolicy(max_steps=plain_count + 5, max_tool_calls=plain_count + 5),
    )
    instrumented_started = time.perf_counter_ns()
    with instrumented_run:
        for value in range(plain_count):
            instrumented_run.call_tool(
                "local_noop",
                {"index": value, "secret": "fixture-secret-redaction-probe"},
                lambda index, secret: index + 1,
            )
    instrumented_ns = time.perf_counter_ns() - instrumented_started
    trace_repr = json.dumps([event.to_dict() for event in instrumented_run.events], sort_keys=True)
    redaction_passed = "fixture-secret-redaction-probe" not in trace_repr

    persisted_events = 0
    trace_delivery_failures = 0
    try:
        traces.create_run("tenant-a", instrumented_run.run_id)
        for event in instrumented_run.events:
            traces.append_event("tenant-a", instrumented_run.run_id, event.to_dict())
            persisted_events += 1
    except Exception:
        trace_delivery_failures += 1

    serialized_event_bytes = sum(
        len(json.dumps(event.to_dict(), sort_keys=True).encode("utf-8"))
        for event in instrumented_run.events
    )
    try:
        sqlite.close()
    except Exception:
        pass

    true_positive = sum(1 for item in detections if item["expected"] and item["detected"])
    false_negative = sum(1 for item in detections if item["expected"] and not item["detected"])
    false_positive = sum(1 for item in detections if not item["expected"] and item["detected"])
    safe_retry_stopped = safe_run.status is not RunStatus.SUCCEEDED
    metrics = {
        "labeled_guard_cases": len(detections),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "detection_precision": (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive else 0.0
        ),
        "detection_recall": (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative else 0.0
        ),
        "safe_control_cases": 1,
        "false_positive_stop_count": int(safe_retry_stopped),
        "false_positive_stop_rate": 1.0 if safe_retry_stopped else 0.0,
        "budget_enforcement": {
            "max_overshoot_tokens": max_budget_overshoot,
            "long_run_turns": 40,
            "total_budget_overshoot_tokens": budget_overshoot,
        },
        "verification_outcomes": {
            "verified": 0,
            "failed": int(verification_event),
            "unknown": 1,
        },
        "retrieval_evidence": {
            "expected_relevant_memories": len(evidence_relevant),
            "retrieved_and_packed_relevant_memories": len(relevant_packed),
            "retained_relevant_recall": len(relevant_packed) / len(evidence_relevant),
            "protected_context_tokens_reserved": reserved_tokens,
        },
        "instrumentation": {
            "tool_calls": plain_count,
            "baseline_elapsed_ms": plain_ns / 1_000_000,
            "instrumented_elapsed_ms": instrumented_ns / 1_000_000,
            "incremental_elapsed_ms": (instrumented_ns - plain_ns) / 1_000_000,
            "events_emitted": len(instrumented_run.events),
            "serialized_trace_bytes": serialized_event_bytes,
            "events_persisted": persisted_events,
            "event_delivery_failures": trace_delivery_failures,
            "redaction_passed": redaction_passed,
        },
    }
    return {
        "report_schema_version": 1,
        "suite": "synapse-agent-reliability-fault-injection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "fixture": {
            "scenario_count": len(scenarios),
            "type": "synthetic_deterministic_fault_injection",
            "uses_llm_judge": False,
            "uses_external_services": False,
        },
        "summary": {
            "passed": sum(1 for item in scenarios if item["passed"]),
            "failed": sum(1 for item in scenarios if not item["passed"]),
            "all_passed": all(item["passed"] for item in scenarios),
        },
        "scenarios": scenarios,
        "metrics": metrics,
        "baseline_references": {
            "repeated_failure_unwrapped_attempts": 3,
            "ambiguous_write_naive_replay_attempts": 2,
            "unverified_tool_success_can_disagree_with_postcondition": True,
        },
        "limitations": [
            "All fault cases use synthetic local tools, fixtures, and tenant records.",
            "The fixture set is not representative of production traffic or agent frameworks.",
            "Precision and recall describe this small labeled scenario set only; they are not real-world rates.",
            "The local SQLite trace path does not replace PostgreSQL RLS integration tests.",
            "Timing and storage measurements are specific to this runner and are not service-level objectives.",
            "Human-reviewed relevance, LongMemEval-S, LLM-judge outputs, and actual task success are separate evidence sources.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()
    report = run_fault_injection_suite()
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report["summary"]["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
