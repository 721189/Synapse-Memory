"""Tenant-isolated persistence for metadata-only agent run traces.

The store accepts a deliberately narrow event contract. It never persists prompts,
tool arguments, tool results, memory content, or arbitrary caller-provided payloads.
"""
from __future__ import annotations

import json
import math
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from synapse_memory.runtime import RunEventType, RunStatus

__all__ = [
    "RunTraceStore",
    "RunTraceError",
    "RunNotFoundError",
    "RunConflictError",
]

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_FINGERPRINT_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_REASONS = {
    "succeeded", "failed", "aborted", "limit_exceeded", "timed_out",
    "timeout", "max_steps", "max_tool_calls", "repeated_tool_call_failures",
    "budget_exceeded", "verification_failed", "unknown_outcome", "cancelled",
    "manual_stop", "approval_required", "approval_granted", "approval_denied",
}
_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.ABORTED.value,
    RunStatus.LIMIT_EXCEEDED.value,
    RunStatus.TIMED_OUT.value,
}
_ALLOWED_SCORE_KEYS = {
    "relevance_score", "retrieval_score", "lexical_rank", "dense_score",
    "rrf_score", "decay_multiplier", "bm25_score", "semantic_score",
}
_ALLOWED_EVIDENCE_KEYS = {
    "memory_id", "rank", "score_components", "token_cost", "packed",
    "created_at", "supersedes", "superseded_by",
}


class RunTraceError(RuntimeError):
    """Base class for trace storage failures."""


class RunNotFoundError(RunTraceError):
    """Raised when a run does not exist inside the authenticated tenant."""


class RunConflictError(RunTraceError):
    """Raised when an event conflicts with an existing run lifecycle."""


def _as_dict(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return {description[0]: value for description, value in zip(cursor.description, row)}


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must include a timezone")
        result = value.timestamp()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("event timestamp must include a timezone")
        result = parsed.timestamp()
    else:
        raise ValueError("event timestamp must be a timezone-aware datetime or finite epoch")
    if not math.isfinite(result):
        raise ValueError("event timestamp must be finite")
    return result


class RunTraceStore:
    """SQLite/PostgreSQL trace store layered over an initialized Synapse store."""

    def __init__(self, backend: Any):
        self.backend = backend
        self.is_postgres = callable(getattr(backend, "_set_tenant_context", None))

    def _validate_tenant(self, tenant_id: str) -> str:
        if not isinstance(tenant_id, str) or not _TENANT_ID_RE.fullmatch(tenant_id):
            raise ValueError("tenant_id must match [A-Za-z0-9_.-] and be 1–128 characters")
        return tenant_id

    def _validate_id(self, value: str, field_name: str) -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise ValueError(f"{field_name} must match [A-Za-z0-9._:-] and be 1–128 characters")
        return value

    @contextmanager
    def _session(self, tenant_id: str, *, write: bool = False) -> Iterator[Tuple[Any, Any]]:
        tenant = self._validate_tenant(tenant_id)
        if self.is_postgres and not getattr(self.backend, "is_connected", False):
            raise RunTraceError("Persistent agent traces require an available PostgreSQL connection.")
        conn = None
        cursor = None
        try:
            conn = self.backend._get_connection()
            if self.is_postgres:
                cursor = conn.cursor()
                self.backend._set_tenant_context(cursor, tenant)
            else:
                if write:
                    conn.execute("BEGIN IMMEDIATE;")
                cursor = conn.cursor()
            yield conn, cursor
            conn.commit()
        except BaseException as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if isinstance(exc, (RunTraceError, ValueError, TypeError)) or not isinstance(exc, Exception):
                raise
            raise RunTraceError("Agent trace storage operation failed.") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                if self.is_postgres:
                    self.backend._return_connection(conn)
                elif getattr(self.backend, "db_path", None) != ":memory:":
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _placeholder(self) -> str:
        return "%s" if self.is_postgres else "?"

    def _run_result(self, row: Mapping[str, Any], *, created: Optional[bool] = None) -> Dict[str, Any]:
        result = {
            "run_id": row["run_id"],
            "schema_version": int(row["schema_version"]),
            "status": row["status"],
            "started_at": _iso_timestamp(row["started_at"]),
            "created_at": _iso_timestamp(row["created_at"]),
            "updated_at": _iso_timestamp(row["updated_at"]),
            "event_count": int(row["event_count"]),
        }
        if created is not None:
            result["created"] = created
        return result

    def create_run(
        self,
        tenant_id: str,
        run_id: str,
        *,
        schema_version: int = 1,
        started_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        tenant = self._validate_tenant(tenant_id)
        run = self._validate_id(run_id, "run_id")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
            raise ValueError("unsupported run schema_version; supported version is 1")
        now = time.time()
        start_time = now if started_at is None else _epoch_timestamp(started_at)
        p = self._placeholder()
        with self._session(tenant, write=True) as (_conn, cur):
            cur.execute(
                f"INSERT INTO agent_runs "
                f"(tenant_id, run_id, schema_version, status, started_at, created_at, updated_at, event_count) "
                f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, 0) "
                f"ON CONFLICT (tenant_id, run_id) DO NOTHING",
                (tenant, run, schema_version, RunStatus.CREATED.value, start_time, now, now),
            )
            created = cur.rowcount == 1
            cur.execute(
                f"SELECT run_id, schema_version, status, started_at, created_at, updated_at, event_count "
                f"FROM agent_runs WHERE tenant_id = {p} AND run_id = {p}",
                (tenant, run),
            )
            row = cur.fetchone()
            if row is None:
                raise RunTraceError("Run was not available after create.")
            record = _as_dict(cur, row)
            if int(record["schema_version"]) != schema_version:
                raise RunConflictError("run_id already exists with a different schema version.")
            return self._run_result(record, created=created)

    def _safe_metadata(self, event: Mapping[str, Any], event_type: str) -> str:
        retrieval_id = event.get("retrieval_id")
        evidence = event.get("memory_evidence") or []
        if event_type != RunEventType.MEMORY_RETRIEVAL.value:
            if retrieval_id is not None or evidence:
                raise ValueError("retrieval metadata is only valid on memory.retrieval events")
            return "{}"
        if not isinstance(retrieval_id, str) or not _ID_RE.fullmatch(retrieval_id):
            raise ValueError("memory.retrieval events require a non-empty retrieval_id")
        if not isinstance(evidence, list) or len(evidence) > 100:
            raise ValueError("memory_evidence must be a list of at most 100 records")
        safe_evidence: List[Dict[str, Any]] = []
        seen_ids = set()
        for item in evidence:
            if not isinstance(item, Mapping) or set(item) - _ALLOWED_EVIDENCE_KEYS:
                raise ValueError("memory_evidence contains unsupported fields")
            memory_id = item.get("memory_id")
            if not isinstance(memory_id, str) or not _ID_RE.fullmatch(memory_id):
                raise ValueError("memory evidence has an invalid memory_id")
            if memory_id in seen_ids:
                raise ValueError("memory evidence IDs must be unique within a retrieval")
            seen_ids.add(memory_id)
            rank = item.get("rank")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ValueError("memory evidence rank must be a positive integer")
            token_cost = item.get("token_cost", 0)
            if isinstance(token_cost, bool) or not isinstance(token_cost, int) or token_cost < 0:
                raise ValueError("memory evidence token_cost must be a non-negative integer")
            packed = item.get("packed", False)
            if not isinstance(packed, bool):
                raise ValueError("memory evidence packed must be boolean")
            scores = item.get("score_components", {})
            if not isinstance(scores, Mapping) or set(scores) - _ALLOWED_SCORE_KEYS:
                raise ValueError("memory evidence contains unsupported score components")
            normalized_scores: Dict[str, float] = {}
            for key, score in scores.items():
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("memory score components must be finite numbers")
                normalized_scores[key] = float(score)
            normalized: Dict[str, Any] = {
                "memory_id": memory_id,
                "rank": rank,
                "score_components": normalized_scores,
                "token_cost": token_cost,
                "packed": packed,
            }
            created_at = item.get("created_at")
            if created_at is not None:
                if isinstance(created_at, bool) or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
                    raise ValueError("memory created_at must be a finite timestamp")
                normalized["created_at"] = float(created_at)
            for lineage_key in ("supersedes", "superseded_by"):
                lineage_id = item.get(lineage_key)
                if lineage_id is not None:
                    if not isinstance(lineage_id, str) or not _ID_RE.fullmatch(lineage_id):
                        raise ValueError("memory lineage ID is invalid")
                    normalized[lineage_key] = lineage_id
            safe_evidence.append(normalized)
        metadata = json.dumps(
            {"retrieval_id": retrieval_id, "memory_evidence": safe_evidence},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        if len(metadata.encode("utf-8")) > 65536:
            raise ValueError("retrieval metadata exceeds the 64 KiB limit")
        return metadata

    def append_event(self, tenant_id: str, run_id: str, event: Mapping[str, Any]) -> Dict[str, Any]:
        tenant = self._validate_tenant(tenant_id)
        run = self._validate_id(run_id, "run_id")
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        if event.get("run_id") != run:
            raise ValueError("event run_id does not match the target run")
        schema_version = event.get("schema_version", 1)
        if isinstance(schema_version, bool) or schema_version != 1:
            raise ValueError("unsupported event schema_version; supported version is 1")
        event_id = self._validate_id(event.get("event_id"), "event_id")
        event_type = event.get("event_type")
        status = event.get("run_status")
        event_types = {item.value for item in RunEventType}
        statuses = {item.value for item in RunStatus}
        if event_type not in event_types:
            raise ValueError("unsupported event_type")
        if status not in statuses:
            raise ValueError("unsupported run_status")
        timestamp = _epoch_timestamp(event.get("timestamp"))
        step_index = event.get("step_index")
        tool_call_index = event.get("tool_call_index")
        for name, value in (("step_index", step_index), ("tool_call_index", tool_call_index)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer when supplied")
        tool_name = event.get("tool_name")
        if tool_name is not None and (
            not isinstance(tool_name, str) or not _SAFE_LABEL_RE.fullmatch(tool_name)
        ):
            raise ValueError("tool_name must be a safe identifier of at most 128 characters")
        fingerprint = event.get("tool_fingerprint")
        if fingerprint is not None and (not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint)):
            raise ValueError("tool_fingerprint must be a 64-character hexadecimal digest")
        reason = event.get("reason")
        error_type = event.get("error_type")
        if reason is not None and (
            not isinstance(reason, str) or reason not in _ALLOWED_REASONS
        ):
            raise ValueError("reason must be a recognized event reason code")
        if error_type is not None and (
            not isinstance(error_type, str) or not _SAFE_LABEL_RE.fullmatch(error_type)
        ):
            raise ValueError("error_type must be a safe exception type identifier")
        metadata_json = self._safe_metadata(event, event_type)
        now = time.time()
        p = self._placeholder()
        with self._session(tenant, write=True) as (_conn, cur):
            cur.execute(
                f"SELECT status, event_count, schema_version FROM agent_runs "
                f"WHERE tenant_id = {p} AND run_id = {p}" + (" FOR UPDATE" if self.is_postgres else ""),
                (tenant, run),
            )
            row = cur.fetchone()
            if row is None and event_type == RunEventType.RUN_STARTED.value:
                cur.execute(
                    f"INSERT INTO agent_runs "
                    f"(tenant_id, run_id, schema_version, status, started_at, created_at, updated_at, event_count) "
                    f"VALUES ({p}, {p}, 1, {p}, {p}, {p}, {p}, 0) "
                    f"ON CONFLICT (tenant_id, run_id) DO NOTHING",
                    (tenant, run, RunStatus.CREATED.value, timestamp, now, now),
                )
                cur.execute(
                    f"SELECT status, event_count, schema_version FROM agent_runs "
                    f"WHERE tenant_id = {p} AND run_id = {p}" + (" FOR UPDATE" if self.is_postgres else ""),
                    (tenant, run),
                )
                row = cur.fetchone()
            if row is None:
                raise RunNotFoundError("Run not found for the authenticated tenant.")
            run_row = _as_dict(cur, row)
            if int(run_row["schema_version"]) != schema_version:
                raise RunConflictError("event schema version does not match the run.")
            cur.execute(
                f"SELECT sequence_number, event_type, timestamp, run_status, tool_fingerprint, tool_name, "
                f"step_index, tool_call_index, reason, error_type, metadata_json "
                f"FROM agent_run_events WHERE tenant_id = {p} AND run_id = {p} AND event_id = {p}",
                (tenant, run, event_id),
            )
            duplicate_row = cur.fetchone()
            if duplicate_row is not None:
                duplicate = _as_dict(cur, duplicate_row)
                same = (
                    duplicate["event_type"] == event_type
                    and duplicate["run_status"] == status
                    and math.isclose(float(duplicate["timestamp"]), timestamp, abs_tol=0.000001)
                    and duplicate["tool_fingerprint"] == fingerprint
                    and duplicate["tool_name"] == tool_name
                    and duplicate["step_index"] == step_index
                    and duplicate["tool_call_index"] == tool_call_index
                    and duplicate["reason"] == reason
                    and duplicate["error_type"] == error_type
                    and (duplicate["metadata_json"] or "{}") == metadata_json
                )
                if not same:
                    raise RunConflictError("event_id has already been used for different event data.")
                return {
                    "run_id": run, "event_id": event_id,
                    "sequence_number": int(duplicate["sequence_number"]),
                    "duplicate": True,
                }

            current_status = run_row["status"]
            current_count = int(run_row["event_count"])
            last_event_type = None
            if current_count:
                cur.execute(
                    f"SELECT event_type FROM agent_run_events "
                    f"WHERE tenant_id = {p} AND run_id = {p} ORDER BY sequence_number DESC LIMIT 1",
                    (tenant, run),
                )
                last_event_row = cur.fetchone()
                if last_event_row is not None:
                    last_event = _as_dict(cur, last_event_row)
                    last_event_type = last_event["event_type"]

            if current_count == 0 and event_type != RunEventType.RUN_STARTED.value:
                raise RunConflictError("the first run event must be run.started.")
            if current_count > 0 and event_type == RunEventType.RUN_STARTED.value:
                raise RunConflictError("run.started may only be the first event.")
            if event_type == RunEventType.RUN_STARTED.value and status != RunStatus.RUNNING.value:
                raise RunConflictError("run.started must have run_status=running.")
            if event_type == RunEventType.RUN_FINISHED.value and status not in _TERMINAL_STATUSES:
                raise RunConflictError("run.finished must have a terminal run_status.")

            # Match the runtime's explicit lifecycle: ordinary work stays running;
            # only stop notifications may switch to a terminal state before finish.
            if event_type == RunEventType.TOOL_CALL_BLOCKED.value and status != RunStatus.LIMIT_EXCEEDED.value:
                raise RunConflictError("tool_call.blocked must have run_status=limit_exceeded.")
            if event_type == RunEventType.LIMIT_EXCEEDED.value and status not in {
                RunStatus.LIMIT_EXCEEDED.value, RunStatus.TIMED_OUT.value
            }:
                raise RunConflictError("run.limit_exceeded must have a limit terminal status.")
            if event_type not in {
                RunEventType.RUN_STARTED.value,
                RunEventType.RUN_FINISHED.value,
                RunEventType.TOOL_CALL_BLOCKED.value,
                RunEventType.LIMIT_EXCEEDED.value,
            } and status != RunStatus.RUNNING.value:
                raise RunConflictError("non-terminal work events must have run_status=running.")

            if current_status in _TERMINAL_STATUSES:
                allow_limit_followup = (
                    current_status == RunStatus.LIMIT_EXCEEDED.value
                    and last_event_type == RunEventType.TOOL_CALL_BLOCKED.value
                    and event_type == RunEventType.LIMIT_EXCEEDED.value
                    and status == current_status
                )
                allow_finish = (
                    event_type == RunEventType.RUN_FINISHED.value
                    and status == current_status
                    and last_event_type != RunEventType.RUN_FINISHED.value
                )
                if not (allow_limit_followup or allow_finish):
                    raise RunConflictError("run is terminal; no further events may be appended.")
            elif event_type == RunEventType.LIMIT_EXCEEDED.value and status not in {
                RunStatus.LIMIT_EXCEEDED.value, RunStatus.TIMED_OUT.value
            }:
                raise RunConflictError("invalid run limit status.")

            next_sequence = current_count + 1
            cur.execute(
                f"INSERT INTO agent_run_events "
                f"(tenant_id, run_id, sequence_number, event_id, schema_version, event_type, timestamp, "
                f"run_status, step_index, tool_call_index, tool_name, tool_fingerprint, reason, error_type, "
                f"metadata_json, recorded_at) "
                f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})",
                (
                    tenant, run, next_sequence, event_id, schema_version, event_type, timestamp, status,
                    step_index, tool_call_index, tool_name, fingerprint, reason, error_type, metadata_json, now,
                ),
            )
            if event_type == RunEventType.RUN_STARTED.value:
                cur.execute(
                    f"UPDATE agent_runs SET status = {p}, event_count = {p}, updated_at = {p}, started_at = {p} "
                    f"WHERE tenant_id = {p} AND run_id = {p}",
                    (status, next_sequence, now, timestamp, tenant, run),
                )
            else:
                cur.execute(
                    f"UPDATE agent_runs SET status = {p}, event_count = {p}, updated_at = {p} "
                    f"WHERE tenant_id = {p} AND run_id = {p}",
                    (status, next_sequence, now, tenant, run),
                )
            return {
                "run_id": run, "event_id": event_id,
                "sequence_number": next_sequence, "duplicate": False,
            }

    def get_run(self, tenant_id: str, run_id: str) -> Dict[str, Any]:
        tenant = self._validate_tenant(tenant_id)
        run = self._validate_id(run_id, "run_id")
        p = self._placeholder()
        with self._session(tenant) as (_conn, cur):
            cur.execute(
                f"SELECT run_id, schema_version, status, started_at, created_at, updated_at, event_count "
                f"FROM agent_runs WHERE tenant_id = {p} AND run_id = {p}",
                (tenant, run),
            )
            row = cur.fetchone()
            if row is None:
                raise RunNotFoundError("Run not found for the authenticated tenant.")
            return self._run_result(_as_dict(cur, row))

    def list_runs(self, tenant_id: str, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        tenant = self._validate_tenant(tenant_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        p = self._placeholder()
        with self._session(tenant) as (_conn, cur):
            cur.execute(
                f"SELECT run_id, schema_version, status, started_at, created_at, updated_at, event_count "
                f"FROM agent_runs WHERE tenant_id = {p} ORDER BY updated_at DESC, run_id LIMIT {p} OFFSET {p}",
                (tenant, limit, offset),
            )
            return [self._run_result(_as_dict(cur, row)) for row in cur.fetchall()]

    def list_events(
        self, tenant_id: str, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        tenant = self._validate_tenant(tenant_id)
        run = self._validate_id(run_id, "run_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        p = self._placeholder()
        with self._session(tenant) as (_conn, cur):
            cur.execute(
                f"SELECT 1 FROM agent_runs WHERE tenant_id = {p} AND run_id = {p}",
                (tenant, run),
            )
            if cur.fetchone() is None:
                raise RunNotFoundError("Run not found for the authenticated tenant.")
            cur.execute(
                f"SELECT sequence_number, event_id, schema_version, event_type, timestamp, run_status, "
                f"step_index, tool_call_index, tool_name, tool_fingerprint, reason, error_type, metadata_json, recorded_at "
                f"FROM agent_run_events WHERE tenant_id = {p} AND run_id = {p} "
                f"ORDER BY sequence_number ASC LIMIT {p} OFFSET {p}",
                (tenant, run, limit, offset),
            )
            result = []
            for row in cur.fetchall():
                item = _as_dict(cur, row)
                try:
                    metadata = json.loads(item.pop("metadata_json") or "{}")
                except (TypeError, ValueError):
                    metadata = {}
                item["timestamp"] = _iso_timestamp(item["timestamp"])
                item["recorded_at"] = _iso_timestamp(item.pop("recorded_at"))
                item["schema_version"] = int(item["schema_version"])
                item["sequence_number"] = int(item["sequence_number"])
                item.update(metadata)
                result.append(item)
            return result

    def delete_run(self, tenant_id: str, run_id: str) -> bool:
        tenant = self._validate_tenant(tenant_id)
        run = self._validate_id(run_id, "run_id")
        p = self._placeholder()
        with self._session(tenant, write=True) as (_conn, cur):
            # Explicitly delete children as well: this remains correct even on old
            # SQLite connections created before foreign-key enforcement was enabled.
            cur.execute(
                f"DELETE FROM agent_run_events WHERE tenant_id = {p} AND run_id = {p}",
                (tenant, run),
            )
            cur.execute(
                f"DELETE FROM agent_runs WHERE tenant_id = {p} AND run_id = {p}",
                (tenant, run),
            )
            return cur.rowcount > 0
