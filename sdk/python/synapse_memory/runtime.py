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
import re
import secrets
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, TypeVar

__all__ = [
    "RunPolicy",
    "RunStatus",
    "RunEventType",
    "RunEvent",
    "MemoryEvidence",
    "SynapseRun",
    "RunGuardError",
    "RunStateError",
    "RunLimitExceeded",
    "RunStepLimitExceeded",
    "RunToolCallLimitExceeded",
    "RunDeadlineExceeded",
    "RepeatedToolCallError",
    "PostconditionFailed",
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
    TOOL_CALL_RETRYING = "tool_call.retrying"
    TOOL_CALL_RETRY_BLOCKED = "tool_call.retry_blocked"
    TOOL_CALL_VERIFICATION_PASSED = "tool_call.verification_passed"
    TOOL_CALL_VERIFICATION_FAILED = "tool_call.verification_failed"
    LIMIT_EXCEEDED = "run.limit_exceeded"
    RUN_FINISHED = "run.finished"
    MEMORY_RETRIEVAL = "memory.retrieval"


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
    max_retry_attempts: int = 2

    def __post_init__(self) -> None:
        for field_name in (
            "max_steps", "max_tool_calls", "max_repeated_failures", "max_retry_attempts"
        ):
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
class MemoryEvidence:
    """Metadata-only explanation of one retrieved memory; never includes content."""

    memory_id: str
    rank: int
    score_components: Mapping[str, float] = field(default_factory=dict)
    token_cost: int = 0
    packed: bool = False
    created_at: Optional[float] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "memory_id": self.memory_id,
            "rank": self.rank,
            "score_components": dict(self.score_components),
            "token_cost": self.token_cost,
            "packed": self.packed,
        }
        if self.created_at is not None:
            result["created_at"] = self.created_at
        if self.supersedes is not None:
            result["supersedes"] = self.supersedes
        if self.superseded_by is not None:
            result["superseded_by"] = self.superseded_by
        return result


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
    schema_version: int = 1
    retrieval_id: Optional[str] = None
    memory_evidence: tuple[MemoryEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-compatible event contract for trace upload."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_status": self.run_status.value,
        }
        for key in ("step_index", "tool_call_index", "tool_name", "tool_fingerprint", "reason", "error_type", "retrieval_id"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.memory_evidence:
            result["memory_evidence"] = [item.to_dict() for item in self.memory_evidence]
        return result


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


class PostconditionFailed(RunGuardError):
    """Raised when a successful tool result fails its independent postcondition."""


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
        if run_id is not None and (
            not isinstance(run_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", run_id)
        ):
            raise ValueError("run_id must match [A-Za-z0-9._:-] and be 1–128 characters")
        self.policy = policy or RunPolicy()
        self.run_id = run_id if run_id is not None else str(uuid.uuid4())
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

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Literal[False]:
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

    def record_memory_retrieval(
        self,
        evidence: Sequence[Mapping[str, Any]],
        *,
        packed_memory_ids: Optional[Sequence[str]] = None,
        retrieval_id: Optional[str] = None,
    ) -> str:
        """Record IDs/scores/packing decisions without storing memory text or query.

        The caller supplies candidate metadata from its retrieval pipeline. Only a
        small allow-list is copied; arbitrary fields such as content or prompt text
        are deliberately ignored. One retrieval consumes one step.
        """
        self._reserve_step()
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
            raise TypeError("evidence must be a sequence of memory metadata mappings")
        if len(evidence) > 100:
            raise ValueError("at most 100 memory evidence records may be captured")
        if retrieval_id is not None and (
            not isinstance(retrieval_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", retrieval_id)
        ):
            raise ValueError("retrieval_id must match [A-Za-z0-9._:-] and be 1–128 characters")
        packed_ids: set[str] = set()
        if packed_memory_ids is not None:
            if isinstance(packed_memory_ids, (str, bytes)) or not isinstance(packed_memory_ids, Sequence):
                raise TypeError("packed_memory_ids must be a sequence of memory IDs")
            for memory_id in packed_memory_ids:
                if not isinstance(memory_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9._:-]{1,128}", memory_id
                ):
                    raise ValueError(
                        "packed memory IDs must match [A-Za-z0-9._:-] and be 1–128 characters"
                    )
                packed_ids.add(memory_id)

        allowed_scores = {
            "relevance_score", "retrieval_score", "lexical_rank", "dense_score",
            "rrf_score", "decay_multiplier", "bm25_score", "semantic_score",
        }
        captured: list[MemoryEvidence] = []
        seen: set[str] = set()
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, Mapping):
                raise TypeError("each memory evidence record must be a mapping")
            memory_id = item.get("id", item.get("memory_id"))
            if not isinstance(memory_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", memory_id):
                raise ValueError("each memory evidence record needs a valid memory ID")
            if memory_id in seen:
                raise ValueError("memory evidence IDs must be unique within a retrieval")
            seen.add(memory_id)
            rank = item.get("rank", index)
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ValueError("memory evidence rank must be a positive integer")
            token_cost = item.get("token_cost", 0)
            if isinstance(token_cost, bool) or not isinstance(token_cost, int) or token_cost < 0:
                raise ValueError("memory evidence token_cost must be a non-negative integer")
            score_components: dict[str, float] = {}
            for key in allowed_scores:
                value = item.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("memory score components must be finite numbers")
                score_components[key] = float(value)
            created_at = item.get("created_at")
            if created_at is not None and (
                isinstance(created_at, bool)
                or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at)
            ):
                raise ValueError("memory created_at must be a finite timestamp")
            supersedes = item.get("supersedes")
            superseded_by = item.get("superseded_by")
            for lineage_id in (supersedes, superseded_by):
                if lineage_id is not None and (
                    not isinstance(lineage_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", lineage_id)
                ):
                    raise ValueError("memory lineage IDs must be non-empty strings of at most 128 characters")
            packed_value = item.get("packed", memory_id in packed_ids)
            if not isinstance(packed_value, bool):
                raise ValueError("memory packed flag must be boolean")
            if packed_memory_ids is not None:
                packed_value = memory_id in packed_ids
            captured.append(
                MemoryEvidence(
                    memory_id=memory_id,
                    rank=rank,
                    score_components=score_components,
                    token_cost=token_cost,
                    packed=packed_value,
                    created_at=float(created_at) if created_at is not None else None,
                    supersedes=supersedes,
                    superseded_by=superseded_by,
                )
            )

        resolved_retrieval_id = retrieval_id or str(uuid.uuid4())
        self._emit(
            RunEventType.MEMORY_RETRIEVAL,
            step_index=self._steps,
            retrieval_id=resolved_retrieval_id,
            memory_evidence=tuple(captured),
        )
        return resolved_retrieval_id

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool: Callable[..., T],
    ) -> T:
        """Execute one tool call once; ambiguous failures are never replayed."""
        return self._call_tool(tool_name, arguments, tool, retry_authorized=False)

    def call_tool_with_retry(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool: Callable[..., T],
        *,
        retry_safe: bool = False,
        retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
        max_attempts: Optional[int] = None,
        retry_delay_seconds: float = 0.0,
    ) -> T:
        """Retry transient errors only when the caller explicitly asserts replay safety.

        retry_safe=True is the caller's assertion that the operation is
        idempotent or protected by a downstream idempotency key. Synapse cannot
        infer this property. Non-idempotent operations default to one attempt.
        Each attempt consumes the normal step and tool-call budgets.
        """
        if not isinstance(retry_safe, bool):
            raise TypeError("retry_safe must be a boolean")
        if not isinstance(retry_on, tuple) or any(
            not isinstance(exc_type, type) or not issubclass(exc_type, Exception)
            for exc_type in retry_on
        ):
            raise TypeError("retry_on must be a tuple of Exception classes")
        if max_attempts is None:
            attempts = self.policy.max_retry_attempts if retry_safe else 1
        else:
            if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
                raise ValueError("max_attempts must be a positive integer")
            attempts = max_attempts
        if attempts > self.policy.max_retry_attempts:
            raise ValueError("max_attempts exceeds the configured max_retry_attempts ceiling")
        if not retry_safe and attempts > 1:
            raise ValueError("multiple attempts require retry_safe=True and an idempotent operation")
        if retry_safe and attempts > 1 and not retry_on:
            raise ValueError("retry_on must contain at least one exception class when retries are enabled")
        delay = retry_delay_seconds
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
            or delay < 0
        ):
            raise ValueError("retry_delay_seconds must be a finite non-negative number")

        for attempt in range(1, attempts + 1):
            try:
                return self._call_tool(
                    tool_name, arguments, tool, retry_authorized=retry_safe
                )
            except BaseException as exc:
                retryable = isinstance(exc, Exception) and isinstance(exc, retry_on)
                if not retryable:
                    raise
                if not retry_safe:
                    self._emit(
                        RunEventType.TOOL_CALL_RETRY_BLOCKED,
                        step_index=self._steps,
                        tool_call_index=self._tool_calls,
                        tool_name=tool_name,
                        reason="retry_not_safe",
                        error_type=type(exc).__name__,
                    )
                    raise
                if attempt >= attempts:
                    raise
                self._emit(
                    RunEventType.TOOL_CALL_RETRYING,
                    step_index=self._steps,
                    tool_call_index=self._tool_calls,
                    tool_name=tool_name,
                    reason="safe_transient_retry",
                    error_type=type(exc).__name__,
                )
                if delay > 0:
                    time.sleep(delay)
        raise AssertionError("bounded retry loop exited without returning or raising")

    def call_tool_verified(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool: Callable[..., T],
        *,
        postcondition: Callable[[T], bool],
    ) -> T:
        """Execute a tool and independently verify its externally observable result.

        The verifier must return literal True. Verification output is never
        serialized; only a pass/fail event with safe metadata is recorded.
        """
        if not callable(postcondition):
            raise TypeError("postcondition must be callable")
        result = self.call_tool(tool_name, arguments, tool)
        try:
            verified = postcondition(result)
        except BaseException as exc:
            self._emit(
                RunEventType.TOOL_CALL_VERIFICATION_FAILED,
                step_index=self._steps,
                tool_call_index=self._tool_calls,
                tool_name=tool_name,
                reason="postcondition_failed",
                error_type=type(exc).__name__,
            )
            raise PostconditionFailed("Tool result failed its independent postcondition.") from None
        if verified is not True:
            self._emit(
                RunEventType.TOOL_CALL_VERIFICATION_FAILED,
                step_index=self._steps,
                tool_call_index=self._tool_calls,
                tool_name=tool_name,
                reason="postcondition_failed",
                error_type="PostconditionFailed",
            )
            raise PostconditionFailed("Tool result failed its independent postcondition.")
        self._emit(
            RunEventType.TOOL_CALL_VERIFICATION_PASSED,
            step_index=self._steps,
            tool_call_index=self._tool_calls,
            tool_name=tool_name,
            reason="postcondition_passed",
        )
        return result

    def _call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool: Callable[..., T],
        *,
        retry_authorized: bool,
    ) -> T:
        """Execute one admitted attempt while preserving normal run budgets.

        Only a per-run keyed HMAC fingerprint of canonical arguments is emitted.
        Raw arguments and exception messages are never added to events.
        """
        self._assert_running()
        if not isinstance(tool_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", tool_name.strip()):
            raise ValueError("tool_name must be a safe identifier of at most 128 characters")
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
        if (
            not retry_authorized
            and self._failure_counts.get(fingerprint, 0) >= self.policy.max_repeated_failures
        ):
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
        retrieval_id: Optional[str] = None,
        memory_evidence: tuple[MemoryEvidence, ...] = (),
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
                retrieval_id=retrieval_id,
                memory_evidence=memory_evidence,
            )
        )
