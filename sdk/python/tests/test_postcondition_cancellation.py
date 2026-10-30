"""Cancellation raised by a postcondition must remain an aborted run."""

import asyncio

import pytest

from synapse_memory.runtime import RunEventType, RunStatus, SynapseRun


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, asyncio.CancelledError])
def test_postcondition_interruption_is_not_rewritten_as_verification_failure(interrupt):
    run = SynapseRun(run_id="postcondition-interrupted")

    def interrupted(_result):
        raise interrupt("cancel verification")

    with pytest.raises(interrupt):
        with run:
            run.call_tool_verified(
                "publish",
                {"resource_id": "resource-1"},
                lambda **_kwargs: {"accepted": True},
                postcondition=interrupted,
            )

    assert run.status is RunStatus.ABORTED
    assert run.events[-1].event_type is RunEventType.RUN_FINISHED
    assert run.events[-1].reason == "aborted"
    assert not any(
        event.event_type is RunEventType.TOOL_CALL_VERIFICATION_FAILED
        for event in run.events
    )
