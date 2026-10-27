"""Canonical SynapseMemory Python client (Phase 6.1).

Small, typed surface over the REST gateway — the supported path for
external applications. Handles auth headers, timeouts, retries on
retryable (429/5xx) failures, and the canonical {success, data} envelope.
"""
from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

__all__ = ["SynapseClient", "SynapseError", "CreateResult", "QueryResult"]

SDK_VERSION = "1.3.0"
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

        GET/HEAD/OPTIONS are retry-safe by default. Callers may explicitly mark
        semantically read-only POSTs (for example /query) as retry-safe. Mutation
        requests are never automatically replayed after a timeout or a 5xx,
        because the server may already have committed the operation.
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

    def create_memory(self, content: str, category: str = "interaction", confidence: float = 0.95) -> CreateResult:
        data = self._request("POST", "/ingest", {"content": content, "category": category, "confidence": confidence})
        return CreateResult(id=data["id"], status=data["status"], token_cost=data["token_cost"], tenant_id=data["tenant_id"])

    def retrieve(self, query: str, max_tokens: int = 1500) -> QueryResult:
        data = self._request(
            "POST", "/query", {"prompt": query, "maxTokens": max_tokens}, retry_safe=True
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

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def readiness(self) -> Dict[str, Any]:
        return self._request("GET", "/readiness")
