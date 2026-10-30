"""Retry backoff must never outlive the cooperative run deadline."""

from unittest.mock import patch

import pytest

from synapse_memory.runtime import RunDeadlineExceeded, RunPolicy, RunStatus, SynapseRun


def test_safe_retry_backoff_is_capped_to_remaining_run_deadline():
    timestamps = iter([100.0, 100.1, 100.75, 101.1])

    def transient_failure(**_kwargs):
        raise ConnectionError("temporary unavailable")

    run = SynapseRun(
        run_id="retry-deadline-backoff",
        policy=RunPolicy(timeout_seconds=1.0, max_retry_attempts=2),
    )
    with (
        patch("synapse_memory.runtime.time.monotonic", side_effect=lambda: next(timestamps)),
        patch("synapse_memory.runtime.time.sleep") as sleep,
        pytest.raises(RunDeadlineExceeded),
    ):
        with run:
            run.call_tool_with_retry(
                "read_status",
                {"resource_id": "res-42"},
                transient_failure,
                retry_safe=True,
                retry_on=(ConnectionError,),
                retry_delay_seconds=300.0,
            )

    sleep.assert_called_once_with(pytest.approx(0.25))
    assert run.status is RunStatus.TIMED_OUT
    assert run.tool_calls == 1
