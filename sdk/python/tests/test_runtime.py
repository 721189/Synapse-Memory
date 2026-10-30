"""Regression tests for the opt-in agent run guard."""
from datetime import timezone

import pytest

from synapse_memory.runtime import (
    RepeatedToolCallError,
    PostconditionFailed,
    RunDeadlineExceeded,
    RunPolicy,
    RunStateError,
    RunStatus,
    RunStepLimitExceeded,
    RunToolCallLimitExceeded,
    RunEventType,
    SynapseRun,
)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_steps": 0},
        {"max_steps": True},
        {"max_tool_calls": 0},
        {"max_repeated_failures": -1},
        {"max_retry_attempts": 0},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
    ],
)
def test_policy_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        RunPolicy(**kwargs)


def test_run_records_metadata_and_returns_tool_result():
    with SynapseRun(run_id="test-run", policy=RunPolicy(max_steps=3, max_tool_calls=2)) as run:
        result = run.call_tool("sum", {"left": 2, "right": 3}, lambda left, right: left + right)

    assert result == 5
    assert run.status is RunStatus.SUCCEEDED
    assert run.steps == 1
    assert run.tool_calls == 1
    assert [event.event_type for event in run.events] == [
        RunEventType.RUN_STARTED,
        RunEventType.STEP_STARTED,
        RunEventType.TOOL_CALL_STARTED,
        RunEventType.TOOL_CALL_SUCCEEDED,
        RunEventType.RUN_FINISHED,
    ]
    assert all(event.run_id == "test-run" for event in run.events)
    assert all(event.timestamp.tzinfo == timezone.utc for event in run.events)
    assert run.events[-1].reason == "succeeded"


def test_events_do_not_contain_raw_tool_arguments_or_error_message():
    secret = "customer-secret-8472"

    def failing_tool(**kwargs):
        raise RuntimeError(f"request contained {secret}")

    with SynapseRun() as run:
        with pytest.raises(RuntimeError, match="customer-secret-8472"):
            run.call_tool("lookup", {"customer": secret}, failing_tool)

    serialized = repr(run.events)
    assert secret not in serialized
    failed = next(event for event in run.events if event.event_type is RunEventType.TOOL_CALL_FAILED)
    assert failed.error_type == "RuntimeError"
    assert failed.tool_fingerprint
    # A handled tool exception does not fail the overall run; its event remains visible.
    assert run.status is RunStatus.SUCCEEDED


def test_uncaught_exception_marks_run_failed():
    run = SynapseRun()
    with pytest.raises(RuntimeError, match="run failed"):
        with run:
            raise RuntimeError("run failed")
    assert run.status is RunStatus.FAILED


def test_repeated_equivalent_failed_calls_are_blocked_before_execution():
    attempts = 0

    def failing_tool(**kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("transient failure")

    with SynapseRun(policy=RunPolicy(max_repeated_failures=2)) as run:
        for _ in range(2):
            with pytest.raises(ValueError):
                run.call_tool("write", {"id": 7, "value": "x"}, failing_tool)
        with pytest.raises(RepeatedToolCallError):
            run.call_tool("write", {"value": "x", "id": 7}, failing_tool)

    assert attempts == 2
    assert run.status is RunStatus.LIMIT_EXCEEDED
    assert any(event.reason == "repeated_tool_call_failures" for event in run.events)


def test_different_arguments_have_distinct_fingerprints():
    calls = []

    with SynapseRun() as run:
        run.call_tool("lookup", {"id": 1}, lambda **kwargs: calls.append(kwargs))
        run.call_tool("lookup", {"id": 2}, lambda **kwargs: calls.append(kwargs))

    fingerprints = [
        event.tool_fingerprint
        for event in run.events
        if event.event_type is RunEventType.TOOL_CALL_STARTED
    ]
    assert len(fingerprints) == 2
    assert fingerprints[0] != fingerprints[1]
    assert calls == [{"id": 1}, {"id": 2}]


def test_success_resets_consecutive_failure_count():
    calls = 0

    def flaky_tool(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1 or calls == 3:
            raise ValueError("failed")
        return "ok"

    with SynapseRun(policy=RunPolicy(max_repeated_failures=2)) as run:
        with pytest.raises(ValueError):
            run.call_tool("flaky", {"id": 1}, flaky_tool)
        assert run.call_tool("flaky", {"id": 1}, flaky_tool) == "ok"
        with pytest.raises(ValueError):
            run.call_tool("flaky", {"id": 1}, flaky_tool)

    assert run.status is RunStatus.SUCCEEDED
    assert calls == 3


def test_step_limit_rejects_work_before_tool_execution():
    executed = False

    with SynapseRun(policy=RunPolicy(max_steps=1)) as run:
        run.record_step()

        def tool(**kwargs):
            nonlocal executed
            executed = True

        with pytest.raises(RunStepLimitExceeded):
            run.call_tool("never", {}, tool)

    assert not executed
    assert run.status is RunStatus.LIMIT_EXCEEDED


def test_tool_call_limit_rejects_work_before_second_tool_execution():
    executed = []

    with SynapseRun(policy=RunPolicy(max_tool_calls=1)) as run:
        run.call_tool("first", {}, lambda: executed.append("first"))
        with pytest.raises(RunToolCallLimitExceeded):
            run.call_tool("second", {}, lambda: executed.append("second"))

    assert executed == ["first"]
    assert run.status is RunStatus.LIMIT_EXCEEDED


def test_deadline_is_checked_before_starting_more_work(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("synapse_memory.runtime.time.monotonic", lambda: clock[0])

    run = SynapseRun(policy=RunPolicy(timeout_seconds=1))
    with run:
        clock[0] = 102.0
        with pytest.raises(RunDeadlineExceeded):
            run.record_step()

    assert run.status is RunStatus.TIMED_OUT
    assert any(event.reason == "timeout" for event in run.events)


def test_guard_requires_active_run_and_can_only_be_entered_once():
    run = SynapseRun()
    with pytest.raises(RunStateError):
        run.record_step()

    with run:
        pass

    with pytest.raises(RunStateError):
        run.__enter__()


def test_non_json_arguments_are_rejected_before_tool_execution():
    executed = False

    with SynapseRun() as run:
        with pytest.raises(TypeError):
            run.call_tool("bad", {"value": object()}, lambda **kwargs: executed)

    assert not executed
    assert run.status is RunStatus.SUCCEEDED


def test_non_finite_arguments_are_rejected_before_tool_execution():
    with SynapseRun() as run:
        with pytest.raises(ValueError, match="non-finite"):
            run.call_tool("bad", {"value": float("nan")}, lambda **kwargs: None)

    assert run.status is RunStatus.SUCCEEDED


def test_events_are_returned_as_immutable_snapshot():
    with SynapseRun() as run:
        snapshot = run.events
        run.record_step()

    assert snapshot == (snapshot[0],)
    assert isinstance(run.events, tuple)


def test_memory_retrieval_event_captures_only_allowlisted_evidence():
    secret_content = "THIS_CONTENT_MUST_NEVER_BE_SERIALIZED"
    secret_query = "THIS_QUERY_MUST_NEVER_BE_SERIALIZED"

    with SynapseRun(run_id="retrieval-run") as run:
        retrieval_id = run.record_memory_retrieval(
            [
                {
                    "id": "memory-1",
                    "rank": 1,
                    "relevance_score": 0.91,
                    "retrieval_score": 0.88,
                    "token_cost": 21,
                    "created_at": 1_760_000_000.0,
                    "supersedes": "memory-old",
                    "content": secret_content,
                    "query": secret_query,
                },
                {"id": "memory-2", "rank": 2, "relevance_score": 0.23, "token_cost": 15},
            ],
            packed_memory_ids=["memory-1"],
            retrieval_id="retrieval-1",
        )

    event = next(e for e in run.events if e.event_type is RunEventType.MEMORY_RETRIEVAL)
    payload = event.to_dict()
    assert retrieval_id == "retrieval-1"
    assert payload["schema_version"] == 1
    assert payload["retrieval_id"] == retrieval_id
    assert payload["memory_evidence"][0]["memory_id"] == "memory-1"
    assert payload["memory_evidence"][0]["packed"] is True
    assert payload["memory_evidence"][1]["packed"] is False
    assert payload["memory_evidence"][0]["score_components"]["relevance_score"] == 0.91
    assert secret_content not in repr(payload)
    assert secret_query not in repr(payload)


def test_memory_retrieval_rejects_duplicate_memory_ids_and_invalid_scores():
    with SynapseRun() as run:
        with pytest.raises(ValueError, match="unique"):
            run.record_memory_retrieval([{"id": "same"}, {"id": "same"}])

    with SynapseRun() as run:
        with pytest.raises(ValueError, match="finite"):
            run.record_memory_retrieval([{"id": "memory-1", "relevance_score": float("nan")}])


def test_memory_retrieval_rejects_more_than_one_hundred_candidates():
    with SynapseRun() as run:
        with pytest.raises(ValueError, match="at most 100"):
            run.record_memory_retrieval([{"id": f"memory-{index}"} for index in range(101)])


def test_run_id_must_match_persistence_contract():
    with pytest.raises(ValueError, match="run_id"):
        SynapseRun(run_id="invalid run id")


def test_safe_transient_retry_requires_opt_in_and_respects_attempt_ceiling():
    attempts = 0

    def transient_then_success(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("upstream connection reset")
        return {"receipt": "committed"}

    with SynapseRun(policy=RunPolicy(max_tool_calls=3, max_retry_attempts=2)) as run:
        result = run.call_tool_with_retry(
            "read_status",
            {"operation_id": "op-001"},
            transient_then_success,
            retry_safe=True,
            retry_on=(ConnectionError,),
        )

    assert result == {"receipt": "committed"}
    assert attempts == 2
    assert run.tool_calls == 2
    assert run.status is RunStatus.SUCCEEDED
    assert any(event.event_type is RunEventType.TOOL_CALL_RETRYING for event in run.events)
    assert sum(
        event.event_type is RunEventType.TOOL_CALL_STARTED for event in run.events
    ) == 2


def test_ambiguous_non_idempotent_write_timeout_is_never_replayed():
    side_effects = []

    def write_then_lose_response(**_kwargs):
        side_effects.append("write may have committed")
        raise TimeoutError("response lost after request was accepted")

    with SynapseRun() as run:
        with pytest.raises(TimeoutError):
            run.call_tool_with_retry(
                "create_payment",
                {"payment_id": "pay-001"},
                write_then_lose_response,
                retry_safe=False,
            )

    assert len(side_effects) == 1
    assert run.tool_calls == 1
    assert any(
        event.event_type is RunEventType.TOOL_CALL_RETRY_BLOCKED
        and event.reason == "retry_not_safe"
        for event in run.events
    )
    assert not any(event.event_type is RunEventType.TOOL_CALL_RETRYING for event in run.events)


def test_retry_configuration_cannot_exceed_policy_or_bypass_idempotency():
    calls = []

    with SynapseRun(policy=RunPolicy(max_retry_attempts=2)) as run:
        with pytest.raises(ValueError, match="idempotent"):
            run.call_tool_with_retry(
                "write",
                {},
                lambda: calls.append("executed"),
                retry_safe=False,
                max_attempts=2,
            )
        with pytest.raises(ValueError, match="ceiling"):
            run.call_tool_with_retry(
                "read",
                {},
                lambda: calls.append("executed"),
                retry_safe=True,
                max_attempts=3,
            )

    assert calls == []


def test_tool_success_is_not_task_success_until_postcondition_passes():
    with SynapseRun() as run:
        with pytest.raises(PostconditionFailed):
            run.call_tool_verified(
                "publish_release",
                {"release_id": "rel-001"},
                lambda **_kwargs: {"api_status": "accepted", "persisted": False},
                postcondition=lambda result: result["persisted"] is True,
            )

    assert run.status is RunStatus.FAILED
    event_types = [event.event_type for event in run.events]
    assert RunEventType.TOOL_CALL_SUCCEEDED in event_types
    assert RunEventType.TOOL_CALL_VERIFICATION_FAILED in event_types
    verification = next(
        event for event in run.events
        if event.event_type is RunEventType.TOOL_CALL_VERIFICATION_FAILED
    )
    assert verification.reason == "postcondition_failed"
    assert "rel-001" not in repr(run.events)
    assert "persisted" not in repr(run.events)


def test_independent_postcondition_success_is_recorded():
    with SynapseRun() as run:
        result = run.call_tool_verified(
            "write_document",
            {"document_id": "doc-001"},
            lambda **_kwargs: {"ok": True},
            postcondition=lambda response: response["ok"] is True,
        )

    assert result == {"ok": True}
    assert run.status is RunStatus.SUCCEEDED
    assert any(
        event.event_type is RunEventType.TOOL_CALL_VERIFICATION_PASSED
        and event.reason == "postcondition_passed"
        for event in run.events
    )


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, __import__("asyncio").CancelledError])
def test_interruption_and_async_cancellation_leave_aborted_terminal_state(cancellation):
    run = SynapseRun()
    with pytest.raises(cancellation):
        with run:
            raise cancellation("operator interruption")

    assert run.status is RunStatus.ABORTED
    finished = run.events[-1]
    assert finished.event_type is RunEventType.RUN_FINISHED
    assert finished.reason == "aborted"
    assert finished.error_type == cancellation.__name__
    assert "operator interruption" not in repr(run.events)
