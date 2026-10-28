"""Regression tests for the opt-in agent run guard."""
from datetime import timezone

import pytest

from synapse_memory.runtime import (
    RepeatedToolCallError,
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
