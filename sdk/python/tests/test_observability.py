import json
import logging

import pytest

from synapse_memory.core.observability import instrument_operation


def test_failure_telemetry_omits_exception_message_and_preserves_exception(caplog):
    secret = "sk-live-observability-test-secret"
    original_error = RuntimeError(f"provider rejected request with credential={secret}")

    @instrument_operation("provider_request")
    def failing_operation():
        raise original_error

    with caplog.at_level(logging.ERROR, logger="SynapseObservability"):
        with pytest.raises(RuntimeError) as raised:
            failing_operation()

    assert raised.value is original_error
    records = [
        record for record in caplog.records
        if record.name == "SynapseObservability"
    ]
    assert len(records) == 1

    payload = json.loads(records[0].getMessage())
    assert payload["event"] == "provider_request"
    assert payload["status"] == "FAILURE"
    assert payload["details"]["error_type"] == "RuntimeError"
    assert isinstance(payload["details"]["duration_ms"], (int, float))
    assert payload["details"]["duration_ms"] >= 0
    assert "error" not in payload["details"]
    assert secret not in records[0].getMessage()
    assert secret not in caplog.text


def test_success_telemetry_and_return_value_are_unchanged(caplog):
    @instrument_operation("successful_operation")
    def successful_operation():
        return {"result": "ok"}

    with caplog.at_level(logging.INFO, logger="SynapseObservability"):
        result = successful_operation()

    assert result == {"result": "ok"}
    records = [
        record for record in caplog.records
        if record.name == "SynapseObservability"
    ]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["event"] == "successful_operation"
    assert payload["status"] == "SUCCESS"
    assert payload["details"]["duration_ms"] >= 0


def test_exception_summary_logs_only_authored_message_and_exception_type(caplog):
    from synapse_memory.core.observability import log_exception_summary

    secret = "postgres://user:top-secret-password@db.example/private"
    error = RuntimeError(f"connection failed for {secret}; payload=private-prompt")
    target_logger = logging.getLogger("SynapseSafeExceptionSummaryTest")

    with caplog.at_level(logging.WARNING, logger=target_logger.name):
        log_exception_summary(
            target_logger,
            "Database readiness check failed.",
            error,
            level=logging.WARNING,
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.getMessage() == (
        "Database readiness check failed. (error_type=RuntimeError)"
    )
    assert record.exc_info is None
    assert secret not in caplog.text
    assert "private-prompt" not in caplog.text
