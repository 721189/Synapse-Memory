"""Run-guard admission errors must not be treated as retryable tool failures."""

import pytest

from synapse_memory.runtime import (
    RunPolicy,
    RunStatus,
    RunToolCallLimitExceeded,
    RunEventType,
    SynapseRun,
)


def test_broad_retry_exception_tuple_does_not_retry_run_guard_errors():
    calls = 0

    def transient_tool(**_kwargs):
        nonlocal calls
        calls += 1
        raise ConnectionError("first attempt cannot connect")

    run = SynapseRun(
        run_id="retry-guard-exception",
        policy=RunPolicy(max_tool_calls=1, max_retry_attempts=3),
    )
    with pytest.raises(RunToolCallLimitExceeded):
        with run:
            run.call_tool_with_retry(
                "read_status",
                {"resource_id": "guard-1"},
                transient_tool,
                retry_safe=True,
                retry_on=(Exception,),
                max_attempts=3,
            )

    assert calls == 1
    assert run.tool_calls == 1
    assert run.status is RunStatus.LIMIT_EXCEEDED
    retry_events = [
        event for event in run.events
        if event.event_type is RunEventType.TOOL_CALL_RETRYING
    ]
    assert len(retry_events) == 1
