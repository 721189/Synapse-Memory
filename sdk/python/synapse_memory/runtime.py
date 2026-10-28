"""Opt-in, synchronous guard for bounded and inspectable agent runs.

This module guards only work explicitly routed through SynapseRun. It does not
intercept arbitrary framework calls or forcibly cancel an already-running tool.
Events are held in memory and contain metadata only; tool arguments are never
stored in the event stream.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TypeVar

__all__ = [
    "RunPolicy",
    "RunStatus",
    "RunEventType",
    "RunEvent",
    "SynapseRun",
    "RunGuardError",
    "RunStateError",
    "RunLimitExceeded",
    "RunStepLimitExceeded",
    "RunToolCallLimitExceeded",
    "RunDeadlineExceeded",
    "RepeatedToolCallError",
]

T = TypeVar("T")


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
    LIMIT_EXCEEDED = "limit_exceeded"
    TIMED_OUT = "timed_out"


class RunEventType(str, Enum):
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    TOOL_CALL_STARTED = "tool_call.started"
    TOOL_CALL_SUCCEEDED = "tool_call.succeeded"
    TOOL_CALL_FAILED = "tool_call.failed"
    TOOL_CALL_BLOCKED = "tool_call.blocked"
    LIMIT_EXCEEDED = "run.limit_exceeded"
    RUN_FINISHED = "run.finished"


@dataclass(frozen=True)
class RunPolicy:
    """Hard admission limits for work explicitly routed through SynapseRun.

    timeout_seconds is cooperative: the guard checks it before starting each
    step/tool call. It cannot interrupt a synchronous callable already running.
    max_repeated_failures counts failed calls for one tool+argument signature;
    a successful call resets that signature's failure counter.
    """

    max_steps: int = 30
    max_tool_calls: int = 20
    timeout_seconds: float = 120.0
    max_repeated_failures: int = 2

    def __post_init__(self) -> None:
        for field_name in ("max_steps", "max_tool_calls", "max_repeated_failures"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        timeout = self.timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be a finite number greater than zero")


@dataclass(frozen=True)
class RunEvent:
    """Privacy-minimized event; fields intentionally exclude arguments/content."""

    event_id: str
    run_id: str
    event_type: RunEventType
    timestamp: datetime
    run_status: RunStatus
    step_index: Optional[int] = None
    tool_call_index: Optional[int] = None
    tool_name: Optional[str] = None
    tool_fingerprint: Optional[str] = None
    reason: Optional[str] = None
    error_type: Optional[str] = None


class RunGuardError(RuntimeError):
    """Base exception for rejected or invalid run-guard operations."""


class RunStateError(RunGuardError):
    """Raised when guarded work is attempted outside an active run."""


class RunLimitExceeded(RunGuardError):
    """Base class for a configured run limit being reached."""


class RunStepLimitExceeded(RunLimitExceeded):
    """Raised before a step beyond max_steps is admitted."""


class RunToolCallLimitExceeded(RunLimitExceeded):
    """Raised before a tool call beyond max_tool_calls is executed."""


class RunDeadlineExceeded(RunLimitExceeded):
    """Raised when the cooperative run deadline has passed."""


class RepeatedToolCallError(RunLimitExceeded):
    """Raised when an equivalent tool call has failed too many times."""


def _normalize_json(value: Any, path: str = "arguments") -> Any:
    """Return JSON-safe data and reject ambiguous/non-finite input values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} must use string keys")
            normalized[key] = _normalize_json(item, f"{path}.{key}")
        return normalized
    if isinstance(value, list):
        return [_normalize_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} must contain only JSON-compatible values")


class SynapseRun:
    """Opt-in synchronous run guard with bounded work and an in-memory trace.

    Each call to call_tool consumes one step and, if admitted, one tool call.
    record_step() accounts for non-tool work. The tool callable receives the
    provided JSON-compatible arguments as keyword arguments.
    """

    def __init__(self, policy: Optional[RunPolicy] = None, run_id: Optional[str] = None):
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError("run_id must be a non-empty string")
        self.policy = policy or RunPolicy()
        self.run_id = run_id or str(uuid.uuid4())
        self._status = RunStatus.CREATED
        self._started_monotonic: Optional[float] = None
        self._steps = 0
        self._tool_calls = 0
        self._failure_counts: dict[str, int] = {}
        self._fingerprint_key = secrets.token_bytes(32)
        self._events: list[RunEvent] = []

    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def events(self) -> tuple[RunEvent, ...]:
        """Return an immutable snapshot of events captured so far."""
        return tuple(self._events)

    def __enter__(self) -> "SynapseRun":
        if self._status is not RunStatus.CREATED:
            raise RunStateError("A SynapseRun can be entered only once.")
        self._started_monotonic = time.monotonic()
        self._status = RunStatus.RUNNING
        self._emit(RunEventType.RUN_STARTED)
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if self._status is RunStatus.RUNNING:
            if self._deadline_passed():
                self._mark_limit(RunStatus.TIMED_OUT, "timeout")
            elif exc_type is None:
                self._status = RunStatus.SUCCEEDED
            elif isinstance(exc_type, type) and issubclass(exc_type, Exception):
                self._status = RunStatus.FAILED
            else:
                self._status = RunStatus.ABORTED
        self._emit(
            RunEventType.RUN_FINISHED,
            reason=self._status.value,
            error_type=exc_type.__name__ if isinstance(exc_type, type) else None,
        )
        return False

    def record_step(self) -> int:
        """Account for one non-tool step; raises before exceeding max_steps."""
        self._reserve_step()
        self._emit(RunEventType.STEP_STARTED, step_index=self._steps)
        return self._steps

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool: Callable[..., T],
    ) -> T:
        """Execute one tool call through the run guard.

        Only a per-run keyed HMAC fingerprint of canonical arguments is emitted.
        Raw arguments and exception messages are never added to events.
        """
        self._assert_running()
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        tool_name = tool_name.strip()
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        if not callable(tool):
            raise TypeError("tool must be callable")

        normalized = _normalize_json(arguments)
        encoded = json.dumps(
            {"tool_name": tool_name, "arguments": normalized},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        fingerprint = hmac.new(self._fingerprint_key, encoded, hashlib.sha256).hexdigest()

        self._reserve_step()
        self._emit(RunEventType.STEP_STARTED, step_index=self._steps)
        if self._tool_calls >= self.policy.max_tool_calls:
            self._block_call(
                RunToolCallLimitExceeded("Configured maximum tool calls reached."),
                "max_tool_calls",
                tool_name,
                fingerprint,
            )
        if self._failure_counts.get(fingerprint, 0) >= self.policy.max_repeated_failures:
            self._block_call(
                RepeatedToolCallError("Equivalent tool call blocked after repeated failures."),
                "repeated_tool_call_failures",
                tool_name,
                fingerprint,
            )

        self._tool_calls += 1
        self._emit(
            RunEventType.TOOL_CALL_STARTED,
            step_index=self._steps,
            tool_call_index=self._tool_calls,
            tool_name=tool_name,
            tool_fingerprint=fingerprint,
        )
        try:
            result = tool(**normalized)
        except BaseException as exc:
            self._failure_counts[fingerprint] = self._failure_counts.get(fingerprint, 0) + 1
            self._emit(
                RunEventType.TOOL_CALL_FAILED,
                step_index=self._steps,
                tool_call_index=self._tool_calls,
                tool_name=tool_name,
                tool_fingerprint=fingerprint,
                error_type=type(exc).__name__,
            )
            raise
        self._failure_counts.pop(fingerprint, None)
        self._emit(
            RunEventType.TOOL_CALL_SUCCEEDED,
            step_index=self._steps,
            tool_call_index=self._tool_calls,
            tool_name=tool_name,
            tool_fingerprint=fingerprint,
        )
        return result

    def _assert_running(self) -> None:
        if self._status is not RunStatus.RUNNING:
            raise RunStateError("Guarded work requires an active, non-terminal SynapseRun.")

    def _reserve_step(self) -> None:
        self._assert_running()
        if self._deadline_passed():
            self._mark_limit(RunStatus.TIMED_OUT, "timeout")
            raise RunDeadlineExceeded("The configured run deadline has passed.")
        if self._steps >= self.policy.max_steps:
            self._mark_limit(RunStatus.LIMIT_EXCEEDED, "max_steps")
            raise RunStepLimitExceeded("Configured maximum steps reached.")
        self._steps += 1

    def _block_call(
        self,
        exception: RunLimitExceeded,
        reason: str,
        tool_name: str,
        fingerprint: str,
    ) -> None:
        self._status = RunStatus.LIMIT_EXCEEDED
        self._emit(
            RunEventType.TOOL_CALL_BLOCKED,
            step_index=self._steps,
            tool_call_index=self._tool_calls,
            tool_name=tool_name,
            tool_fingerprint=fingerprint,
            reason=reason,
        )
        self._emit(RunEventType.LIMIT_EXCEEDED, reason=reason)
        raise exception

    def _deadline_passed(self) -> bool:
        return (
            self._started_monotonic is not None
            and time.monotonic() - self._started_monotonic >= self.policy.timeout_seconds
        )

    def _mark_limit(self, status: RunStatus, reason: str) -> None:
        if self._status is RunStatus.RUNNING:
            self._status = status
            self._emit(RunEventType.LIMIT_EXCEEDED, reason=reason)

    def _emit(
        self,
        event_type: RunEventType,
        *,
        step_index: Optional[int] = None,
        tool_call_index: Optional[int] = None,
        tool_name: Optional[str] = None,
        tool_fingerprint: Optional[str] = None,
        reason: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        self._events.append(
            RunEvent(
                event_id=str(uuid.uuid4()),
                run_id=self.run_id,
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                run_status=self._status,
                step_index=step_index,
                tool_call_index=tool_call_index,
                tool_name=tool_name,
                tool_fingerprint=tool_fingerprint,
                reason=reason,
                error_type=error_type,
            )
        )
