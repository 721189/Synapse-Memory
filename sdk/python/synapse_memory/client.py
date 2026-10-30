"""Canonical SynapseMemory Python client (Phase 6.1).

Small, typed surface over the REST gateway — the supported path for
external applications. Handles auth headers, timeouts, retries on
retryable (429/5xx) failures, and the canonical {success, data} envelope.
"""
from __future__ import annotations

import time
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

__all__ = ["SynapseClient", "SynapseError", "CreateResult", "QueryResult"]

SDK_VERSION = "1.4.0"
API_COMPAT_VERSION = "v1"


class SynapseError(Exception):
    def __init__(self, message: str, *, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class CreateResult:
    id: str
    status: str
    token_cost: int
    tenant_id: str


@dataclass
class QueryResult:
    fused_context: str
    injected_nodes_count: int
    injected_nodes: List[Dict[str, Any]]
    storage_backend: str


class SynapseClient:
    """Synchronous client; also usable as a context manager."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8008",
        api_key: Optional[str] = None,
        tenant_id: str = "default",
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def __enter__(self) -> "SynapseClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Tenant-ID": self.tenant_id}
        if self.api_key:
            headers["X-Synapse-API-Key"] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        retry_safe: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Perform a request without automatically replaying ambiguous writes.

        GET/HEAD/OPTIONS are retry-safe by default. POST requests are not
        retried unless the caller explicitly opts in with a proven idempotency
        contract. This matters for /query too: retrieval updates access counts and
        last-access timestamps. Mutation requests are not replayed after an
        ambiguous timeout or 5xx response.
        """
        url = f"{self.base_url}{path}"
        method = method.upper()
        safe = method in {"GET", "HEAD", "OPTIONS"} if retry_safe is None else retry_safe
        max_attempts = self.max_retries + 1 if safe else 1
        last_error: Optional[SynapseError] = None

        for attempt in range(max_attempts):
            retry_after: Optional[float] = None
            try:
                resp = self._session.request(
                    method, url, json=payload, headers=self._headers(), timeout=self.timeout
                )
            except requests.RequestException as exc:
                if not safe:
                    raise SynapseError(
                        f"Connection failed during {method} {path}; the write outcome is unknown "
                        "and was not automatically retried.",
                        retryable=False,
                    ) from exc
                last_error = SynapseError(f"Connection failed: {exc}", retryable=True)
            else:
                if resp.status_code in (429, 502, 503, 504):
                    if not safe:
                        raise SynapseError(
                            f"Transient HTTP {resp.status_code} for non-replay-safe {method} {path}; "
                            "automatic retry suppressed to avoid duplicate mutations.",
                            status=resp.status_code,
                            retryable=True,
                        )
                    last_error = SynapseError(
                        f"Retryable {resp.status_code}: {resp.text[:200]}",
                        status=resp.status_code,
                        retryable=True,
                    )
                    header = resp.headers.get("Retry-After")
                    if header:
                        try:
                            retry_after = max(0.0, float(header))
                        except ValueError:
                            try:
                                retry_after = max(
                                    0.0,
                                    (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds(),
                                )
                            except (TypeError, ValueError, OverflowError):
                                retry_after = None
                elif not resp.ok:
                    raise SynapseError(
                        f"Request failed {resp.status_code}: {resp.text[:500]}",
                        status=resp.status_code,
                    )
                else:
                    try:
                        body = resp.json()
                    except ValueError as exc:
                        raise SynapseError(
                            f"API returned invalid JSON for {method} {path}",
                            status=resp.status_code,
                        ) from exc
                    if isinstance(body, dict) and "success" in body and "data" in body:
                        if not body["success"]:
                            raise SynapseError(
                                f"API error: {body.get('error')}", status=resp.status_code
                            )
                        return body["data"]
                    return body

            if attempt < max_attempts - 1:
                backoff = min(2.0, 0.25 * (2 ** attempt))
                time.sleep(retry_after if retry_after is not None else backoff)

        raise last_error or SynapseError("Request failed")

    def create_memory(
        self,
        content: str,
        category: str = "interaction",
        confidence: float = 0.95,
        *,
        source_type: str = "unspecified",
        source_id: Optional[str] = None,
        observed_at: Optional[float] = None,
        valid_from: Optional[float] = None,
        valid_until: Optional[float] = None,
        trust_level: str = "unverified",
    ) -> CreateResult:
        """Create a memory with optional source and temporal provenance metadata."""
        payload: Dict[str, Any] = {
            "content": content,
            "category": category,
            "confidence": confidence,
            "source_type": source_type,
            "trust_level": trust_level,
        }
        for key, value in (
            ("source_id", source_id),
            ("observed_at", observed_at),
            ("valid_from", valid_from),
            ("valid_until", valid_until),
        ):
            if value is not None:
                payload[key] = value
        data = self._request("POST", "/ingest", payload)
        return CreateResult(id=data["id"], status=data["status"], token_cost=data["token_cost"], tenant_id=data["tenant_id"])

    def retrieve(self, query: str, max_tokens: int = 1500) -> QueryResult:
        data = self._request(
            "POST", "/query", {"prompt": query, "maxTokens": max_tokens}
        )
        return QueryResult(
            fused_context=data.get("fused_context", ""),
            injected_nodes_count=data.get("injected_nodes_count", 0),
            injected_nodes=data.get("injected_nodes", []),
            storage_backend=data.get("active_storage_backend", "unknown"),
        )

    def list_memories(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        return self._request("GET", f"/api/memories/list?limit={limit}&offset={offset}")

    def delete_memory(self, memory_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/memories/{memory_id}")

    def feedback(self, memory_id: str, feedback_type: str) -> Dict[str, Any]:
        return self._request("POST", "/feedback", {"memory_id": memory_id, "feedback_type": feedback_type})

    def create_agent_run(self, run_id: str) -> Dict[str, Any]:
        """Create (idempotently) a tenant-bound persistent run trace."""
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", run_id):
            raise ValueError("run_id must match [A-Za-z0-9._:-] and be 1–128 characters")
        return self._request(
            "POST", "/api/agent-runs", {"run_id": run_id, "schema_version": 1}, retry_safe=True
        )

    def append_agent_run_event(self, run_id: str, event: Any) -> Dict[str, Any]:
        """Append one versioned event. Stable event IDs make retry safe."""
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", run_id):
            raise ValueError("run_id must match [A-Za-z0-9._:-] and be 1–128 characters")
        if hasattr(event, "to_dict") and callable(event.to_dict):
            payload = event.to_dict()
        elif isinstance(event, dict):
            payload = dict(event)
        else:
            raise TypeError("event must be a RunEvent or JSON-compatible event dictionary")
        if payload.get("run_id") != run_id:
            raise ValueError("event run_id does not match run_id")
        if not isinstance(payload.get("event_id"), str) or not payload["event_id"]:
            raise ValueError("event must include a stable event_id")
        return self._request(
            "POST", f"/api/agent-runs/{run_id}/events", payload, retry_safe=True
        )

    def upload_agent_run(self, run: Any) -> Dict[str, Any]:
        """Persist an in-memory SynapseRun after it finishes.

        The API appends events in the run's original order. Re-uploading the same
        run is idempotent because create-run and event IDs are stable. A partial
        upload can be retried by calling this method again.
        """
        run_id = getattr(run, "run_id", None)
        events = getattr(run, "events", None)
        if not isinstance(run_id, str) or not run_id or events is None:
            raise TypeError("run must expose run_id and events (for example, SynapseRun)")
        summary = self.create_agent_run(run_id)
        uploaded = []
        for event in events:
            uploaded.append(self.append_agent_run_event(run_id, event))
        return {
            "run": summary,
            "events_submitted": len(uploaded),
            "events_inserted": sum(1 for result in uploaded if not result.get("duplicate", False)),
            "events_deduplicated": sum(1 for result in uploaded if result.get("duplicate", False)),
            "last_sequence_number": uploaded[-1]["sequence_number"] if uploaded else 0,
        }

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def readiness(self) -> Dict[str, Any]:
        return self._request("GET", "/readiness")
