"""Retry backoff must never outlive the cooperative run deadline."""

from unittest.mock import patch

import pytest

from synapse_memory.runtime import RunDeadlineExceeded, RunPolicy, RunStatus, SynapseRun


def test_safe_retry_backoff_is_capped_to_remaining_run_deadline():
    clock = [100.0]

    def transient_failure(**_kwargs):
        raise ConnectionError("temporary unavailable")

    def wait_until_timeout(_event, timeout=None):
        assert timeout == pytest.approx(0.9)
        clock[0] = 101.1
        return False

    run = SynapseRun(
        run_id="retry-deadline-backoff",
        policy=RunPolicy(timeout_seconds=1.0, max_retry_attempts=2),
    )
    with (
        patch("synapse_memory.runtime.time.monotonic", side_effect=lambda: clock[0]),
        patch("synapse_memory.runtime.threading.Event.wait", autospec=True, side_effect=wait_until_timeout) as wait,
        pytest.raises(RunDeadlineExceeded),
    ):
        with run:
            clock[0] = 100.1
            run.call_tool_with_retry(
                "read_status",
                {"resource_id": "res-42"},
                transient_failure,
                retry_safe=True,
                retry_on=(ConnectionError,),
                retry_delay_seconds=300.0,
            )

    wait.assert_called_once()
    assert run.status is RunStatus.TIMED_OUT
    assert run.tool_calls == 1
