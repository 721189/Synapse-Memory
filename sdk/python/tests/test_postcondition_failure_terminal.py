"""A failed postcondition must latch the run as failed, even when caught."""

import pytest

from synapse_memory.runtime import PostconditionFailed, RunEventType, RunStateError, RunStatus, SynapseRun


def test_caught_postcondition_failure_cannot_be_followed_by_more_tool_work():
    run = SynapseRun(run_id="postcondition-failed-terminal")
    with run:
        try:
            run.call_tool_verified(
                "publish",
                {"resource_id": "resource-42"},
                lambda **_kwargs: {"accepted": True, "persisted": False},
                postcondition=lambda result: result["persisted"] is True,
            )
        except PostconditionFailed:
            pass

        assert run.status is RunStatus.FAILED
        with pytest.raises(RunStateError, match="active"):
            run.call_tool("continue_after_failed_verification", {}, lambda: "must-not-run")

    assert run.status is RunStatus.FAILED
    assert run.events[-1].event_type is RunEventType.RUN_FINISHED
    assert run.events[-1].reason == "failed"
    assert any(
        event.event_type is RunEventType.TOOL_CALL_VERIFICATION_FAILED
        for event in run.events
    )
