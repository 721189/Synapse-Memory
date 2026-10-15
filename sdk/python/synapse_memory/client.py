"""Canonical SynapseMemory Python client (Phase 6.1).

Small, typed surface over the REST gateway — the supported path for
external applications. Handles auth headers, timeouts, retries on
retryable (429/5xx) failures, and the canonical {success, data} envelope.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

__all__ = ["SynapseClient", "SynapseError", "CreateResult", "QueryResult"]

SDK_VERSION = "1.2.0"
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

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Optional[SynapseError] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.request(method, url, json=payload, headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as e:
                last_error = SynapseError(f"Connection failed: {e}", retryable=True)
            else:
                if resp.status_code in (429, 502, 503, 504):
                    last_error = SynapseError(f"Retryable {resp.status_code}: {resp.text[:200]}", status=resp.status_code, retryable=True)
                elif not resp.ok:
                    raise SynapseError(f"Request failed {resp.status_code}: {resp.text[:500]}", status=resp.status_code)
                else:
                    body = resp.json()
                    if isinstance(body, dict) and "success" in body and "data" in body:
                        if not body["success"]:
                            raise SynapseError(f"API error: {body.get('error')}", status=resp.status_code)
                        return body["data"]
                    return body
            if attempt < self.max_retries:
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise last_error or SynapseError("Request failed")

    def create_memory(self, content: str, category: str = "interaction", confidence: float = 0.95) -> CreateResult:
        data = self._request("POST", "/ingest", {"content": content, "category": category, "confidence": confidence})
        return CreateResult(id=data["id"], status=data["status"], token_cost=data["token_cost"], tenant_id=data["tenant_id"])

    def retrieve(self, query: str, max_tokens: int = 1500) -> QueryResult:
        data = self._request("POST", "/query", {"prompt": query, "maxTokens": max_tokens})
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
