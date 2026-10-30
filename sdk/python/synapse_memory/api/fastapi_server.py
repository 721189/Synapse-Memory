import sys
import os
import time
import datetime
import hashlib
import json
import logging
import math
import re
import threading
from typing import List, Dict, Any, Optional, Tuple, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import uvicorn
from fastapi import FastAPI, HTTPException, status, Header, Request, Depends, Path
from fastapi.middleware.cors import CORSMiddleware

# Adjust path to enable absolute imports when running as standalone script
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import (
    DEFAULT_MAX_RECORD_LIMIT, MemoryManager, estimate_token_cost,
)
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.temporal import is_retrieval_eligible
from synapse_memory.runtime import RunEventType, RunStatus
from synapse_memory.core.run_trace_store import (
    RunTraceStore, RunTraceError, RunNotFoundError, RunConflictError,
)
from synapse_memory.core.auth import (
    SecurityManager,
    APIKeyRecord,
    can_create_api_key,
    is_platform_admin,
    tenant_selection_allowed,
)

logger = logging.getLogger("SynapseGateway")
logging.basicConfig(level=logging.INFO)

# Detect Canonical Database Storage Backend
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
STORAGE_BACKEND = os.environ.get("SYNAPSE_STORAGE_BACKEND", "auto").lower()
# SQLite file location. Configurable so tests and multi-instance dev setups can
# isolate their state instead of always colliding on ./synapse_memory.db.
SQLITE_DB_PATH = os.environ.get("SYNAPSE_SQLITE_DB_PATH", "synapse_memory.db")
IS_PRODUCTION = os.environ.get("NODE_ENV", "").lower() == "production"
# Fail-closed by default whenever a DATABASE_URL is configured: silent in-memory
# fallback causes data loss and is unacceptable for teams. Explicit opt-out via
# SYNAPSE_ALLOW_DB_FALLBACK=true (dev only).
ALLOW_DB_FALLBACK = os.environ.get("SYNAPSE_ALLOW_DB_FALLBACK", "").lower() == "true"
FAIL_CLOSED_DB = bool(DATABASE_URL) and not ALLOW_DB_FALLBACK
# Gate demo/simulated routes (infrastructure fantasy metrics, licensing toy).
# Production deployments MUST set ENABLE_DEMO_ROUTES=false.
ENABLE_DEMO_ROUTES = os.environ.get("ENABLE_DEMO_ROUTES", "false" if IS_PRODUCTION else "true").lower() == "true"
# Schema ownership topology. true (default) = the engine creates/upgrades its own
# schema (single-role local/dev). false = "migrate-then-run": DDL + migrations are
# performed ahead of start-up by the migration role, and the engine connects as a
# least-privilege DML-only runtime role (scripts/provision_db_roles.py). Required
# when the runtime role is a NON-owner without BYPASSRLS, because such a role
# cannot execute DDL.
APPLY_DB_SCHEMA = os.environ.get("SYNAPSE_APPLY_DB_SCHEMA", "true").lower() == "true"

# Security & Distributed Identity Engine
# When SYNAPSE_APPLY_DB_SCHEMA=false (least-privilege production topology), the
# engine must not issue DDL; it only validates that auth tables exist and are
# readable by the runtime role.
security_manager = SecurityManager(
    db_path=SQLITE_DB_PATH,
    connection_string=DATABASE_URL if (DATABASE_URL and STORAGE_BACKEND in ["pgvector", "auto"]) else None,
    fail_closed=FAIL_CLOSED_DB or (IS_PRODUCTION and bool(DATABASE_URL)),
    validate_schema_only=not APPLY_DB_SCHEMA,
)

# Instantiate active embedder to establish canonical vector dimension
embedder = SynapseEmbedder()
embedding_dimension = embedder.dimension

store: Any
if STORAGE_BACKEND == "pgvector":
    try:
        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            dimension=embedding_dimension,
            fail_closed=True,
            apply_schema=APPLY_DB_SCHEMA,
        )
        ACTIVE_BACKEND = "pgvector"
        logger.info(f"Canonical Production Engine using PostgreSQL + pgvector ({embedding_dimension}-d HNSW) at {str(DATABASE_URL)[:20]}...")
    except Exception as e:
        logger.exception(f"PostgreSQL startup failed in pgvector mode: {e}")
        raise
elif STORAGE_BACKEND == "auto" and DATABASE_URL:
    try:
        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            dimension=embedding_dimension,
            fail_closed=FAIL_CLOSED_DB or IS_PRODUCTION,
            apply_schema=APPLY_DB_SCHEMA,
        )
        ACTIVE_BACKEND = "pgvector"
        logger.info(f"Canonical Production Engine using PostgreSQL + pgvector ({embedding_dimension}-d HNSW) at {str(DATABASE_URL)[:20]}...")
    except Exception as e:
        if FAIL_CLOSED_DB or IS_PRODUCTION:
            logger.exception(f"PostgreSQL startup failed (fail-closed): {e}")
            raise
        logger.warning(f"Could not connect to PostgreSQL ({e}); initializing SQLiteMemoryStore.")
        store = SQLiteMemoryStore(SQLITE_DB_PATH)
        ACTIVE_BACKEND = "sqlite"
else:
    store = SQLiteMemoryStore(SQLITE_DB_PATH)
    ACTIVE_BACKEND = "sqlite"

manager = MemoryManager(store=store, embedder=embedder, max_record_limit=DEFAULT_MAX_RECORD_LIMIT)
run_trace_store = RunTraceStore(store)
logger.info(
    f"MemoryManager capacity limit: {manager.max_record_limit} records "
    f"(override with SYNAPSE_MAX_RECORD_LIMIT; historical default is 50)"
)
packer = KnapsackPacker(token_budget=min(int(os.environ.get("TOKEN_BUDGET_MAX", "4000")), KnapsackPacker.MAX_BUDGET))
decay = DecayEngine(category_configs={})
searcher = HybridSearch()

# --- Rate limiting: distributed (Redis) when configured, else in-process ---
# REDIS_URL enables a Redis sorted-set sliding window enforced across ALL
# replicas; without it (or when Redis is unreachable) we degrade to a
# thread-safe per-process window. See synapse_memory.core.rate_limiter.
from synapse_memory.core.rate_limiter import build_rate_limiter

RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))
RATE_LIMIT_WINDOW_SECONDS = float(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
rate_limiter = build_rate_limiter()

# --- Live operational metrics (no extra deps; scraped via /metrics) ---
_metrics_lock = threading.Lock()
# Request counts keyed by (route_template, status) with a max of 10000 distinct
# keys. Dynamic paths (e.g. /api/memories/<id>) are bucketed as "other" so
# request volume cannot exhaust memory (F-08).
_REQUEST_COUNTS: Dict[Tuple[str, str], int] = {}
# Latency samples keyed by route template (bounded by _REQUEST_COUNTS).
_LATENCY_SAMPLES: Dict[str, List[float]] = {}
# Key expiration for metrics: (timestamp, count) tuples; entries older than
# _METRICS_TTL_SECONDS are pruned on each scrape.
_METRICS_TTL: Dict[Tuple[str, str], Tuple[float, int]] = {}
_METRICS_TTL_SECONDS = 3600.0
# Max distinct route templates; excess paths get bucketed as "other".
_MAX_ROUTE_TEMPLATES = 10000
# Canonical route templates for dynamic paths (F-08)
_ROUTE_TEMPLATE_MAP = {
    "/api/memories": "/api/memories",
    "/api/memories/{memory_id}": "/api/memories/{memory_id}",
    "/api/memories/list": "/api/memories",
    "/api/memories/{memory_id}/feedback": "/api/memories/{memory_id}/feedback",
}
_rate_limit_rejections = 0
_rate_limit_checks = 0
_ingest_success = 0
_ingest_errors = 0
_retrieval_success = 0
_retrieval_errors = 0
# Auth/authorization failures. Exported as synapse_auth_failures_total in
# /metrics; incremented on the fail-closed path in _build_auth_dependency.
_auth_failures = 0
_last_candidate_count = 0
_last_packed_tokens = 0


def _record_request(path: str, status: int, latency_s: float) -> None:
    """Record request metrics with bounded cardinality (F-08).

    Route templates are used as labels (e.g. /api/memories/{memory_id});
    dynamic paths are bucketed as 'other'. Keys are pruned after TTL.
    """
    with _metrics_lock:
        # Prune expired keys
        now = time.time()
        expired_keys = [
            k for k, (t, _) in _METRICS_TTL.items() if now - t > _METRICS_TTL_SECONDS
        ]
        for key in expired_keys:
            _REQUEST_COUNTS.pop(key, None)
            _METRICS_TTL.pop(key, None)

        # Bucket path: use route template if known, else 'other'
        route_template = _ROUTE_TEMPLATE_MAP.get(path, "other")

        key = (route_template, str(status))
        if key not in _REQUEST_COUNTS and len(_REQUEST_COUNTS) >= _MAX_ROUTE_TEMPLATES:
            # Hard ceiling on distinct series: over-cap paths fold into 'other'
            # instead of growing the counter dict without bound.
            key = ("other", str(status))
        _REQUEST_COUNTS[key] = _REQUEST_COUNTS.get(key, 0) + 1
        # Record freshness so the prune above can actually expire this series.
        _METRICS_TTL[key] = (now, _REQUEST_COUNTS[key])
        samples = _LATENCY_SAMPLES.setdefault(route_template, [])
        samples.append(latency_s)
        if len(samples) > 500:
            del samples[: len(samples) - 500]


def _check_rate_limit(key: str) -> None:
    global _rate_limit_rejections, _rate_limit_checks
    with _metrics_lock:
        _rate_limit_checks += 1
        should_prune = _rate_limit_checks % 256 == 0
    # Opportunistically bound bucket growth: a spoofed/rotating key space must
    # not accumulate buckets forever. No-op on the Redis limiter (keys PEXPIRE).
    if should_prune:
        prune = getattr(rate_limiter, "prune_idle", None)
        if callable(prune):
            try:
                prune()
            except Exception:
                pass
    if rate_limiter.check(key, RATE_LIMIT_PER_MIN, RATE_LIMIT_WINDOW_SECONDS):
        return
    with _metrics_lock:
        _rate_limit_rejections += 1
    raise HTTPException(status_code=429, detail="Rate limit exceeded. Retry after 60s.")


def _rate_limit_principal(request: Request) -> str:
    """Stable, non-reversible bucket identity for a request.

    Buckets key on a truncated SHA-256 of the presented API key — never the raw
    secret — so every request from the same credential shares one bucket and the
    secret is never stored in limiter state. The client IP is folded in so a
    caller rotating spoofed API-key values cannot mint unlimited buckets from a
    single source address. Anonymous traffic falls back to the client IP alone.
    """
    api_key = request.headers.get("X-Synapse-API-Key") or ""
    client_ip = request.client.host if request.client else "anon"
    if api_key:
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        return f"{client_ip}:{digest}"
    return f"ip:{client_ip}"


# Initialize FastAPI application (MUST precede any @app.middleware / route registration)
app = FastAPI(
    title="SynapseMemory Enterprise Cognitive Gateway",
    description="Sovereign Active Long-Term Memory & Context Budgeting REST API with Multi-Tenancy and Database-Level RLS.",
    version="1.4.0"
)


@app.middleware("http")
async def add_request_id_and_rate_limit(request: Request, call_next):  # type: ignore
    import uuid as _uuid

    request_id = request.headers.get("X-Request-ID") or f"req_{_uuid.uuid4().hex[:12]}"
    start = time.time()
    # Rate-limit only mutating + heavy retrieval endpoints
    if request.url.path.startswith(("/ingest", "/api/memories", "/query", "/api/retrieval", "/api/agent-runs")):
        # Avoid unbounded per-run limiter buckets: all run/event URLs share one bucket.
        rate_route = "/api/agent-runs" if request.url.path.startswith("/api/agent-runs") else request.url.path
        try:
            _check_rate_limit(f"{_rate_limit_principal(request)}:{rate_route}")
        except HTTPException as e:
            from fastapi.responses import JSONResponse

            _record_request(request.url.path, e.status_code, time.time() - start)
            return JSONResponse(
                status_code=e.status_code,
                content={"success": False, "error": e.detail, "request_id": request_id},
            )
    # HTTPException raised inside endpoints is shaped by the registered handler
    # below; the middleware only stamps the request id on successful responses.
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    try:
        _record_request(request.url.path, response.status_code, time.time() - start)
    except Exception:
        pass
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # type: ignore
    """Shape HTTPException into the canonical error envelope with request_id."""
    import uuid as _uuid
    from fastapi.responses import JSONResponse

    request_id = request.headers.get("X-Request-ID") or f"req_{_uuid.uuid4().hex[:12]}"
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


# Tighten CORS to production-appropriate origins
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if os.environ.get("ALLOW_ALL_CORS") == "true" else ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
def _flush_audit_on_shutdown() -> None:
    """Flush buffered audit rows on graceful shutdown (F-13).

    Without this, a clean container stop still loses the pending audit batch;
    abrupt SIGKILL remains a documented residual risk (see SECURITY.md).
    """
    try:
        security_manager.close()
    except Exception:  # pragma: no cover - shutdown must never raise
        pass


# Security Authentication Dependency & Scope Enforcement
def require_scope(scope: str):
    async def dependency(
        request: Request,
        x_synapse_api_key: Optional[str] = Header(None, alias="X-Synapse-API-Key"),
        authorization: Optional[str] = Header(None)
    ) -> APIKeyRecord:
        token = x_synapse_api_key
        if not token and authorization:
            if authorization.lower().startswith("bearer "):
                token = authorization[7:].strip()
            else:
                token = authorization.strip()

        tenant_header = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")
        client_ip = request.client.host if request.client else None

        # Dev-open bypass is ONLY for a completely unconfigured local setup:
        # not in production AND no SYNAPSE_API_KEY env AND no keys provisioned.
        # The moment any auth is configured, anonymous requests are denied (403).
        # Short-circuited so authenticated requests never hit the keys table.
        if not token:
            auth_configured = bool(os.environ.get("SYNAPSE_API_KEY")) or len(security_manager.list_api_keys()) > 0
            if not IS_PRODUCTION and not auth_configured:
                return APIKeyRecord(
                    key_id="key_dev_open",
                    key_hash="",
                    name="Development Default",
                    organization_id="org_dev",
                    project_id="proj_dev",
                    tenant_id=tenant_header or "default",
                    scopes=["admin", "memories:read", "memories:write", "memories:delete"],
                    # Explicit platform_admin: this bypass exists only when NO
                    # auth is configured at all (local dev). It was previously
                    # role="admin", which now normalizes to workspace_admin.
                    role="platform_admin",
                    is_active=True,
                    expires_at=None,
                    created_at=time.time(),
                    last_used_at=time.time()
                )

        valid, record, reason = security_manager.validate_api_key(
            token=token or "",
            required_scope=scope,
            requested_tenant_id=tenant_header,
            route=request.url.path,
            action=request.method,
            client_ip=client_ip,
        )

        if not valid or record is None:
            global _auth_failures
            with _metrics_lock:
                _auth_failures += 1
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication failed.",
            )

        return record

    return dependency


def resolve_tenant(
    principal: APIKeyRecord,
    requested_tenant: Optional[str]
) -> str:
    """Resolve the effective tenant for this request.

    Cross-tenant selection is a platform_admin capability only (F-01). Neither
    the raw stored role nor the legacy "admin" scope widens the boundary, so a
    delegated workspace key can never operate on another tenant by asking.
    """
    if is_platform_admin(principal):
        if requested_tenant:
            return requested_tenant
        if principal.tenant_id not in ("*", ""):
            return principal.tenant_id
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit tenant_id is required for admin cross-tenant operations."
        )

    if not tenant_selection_allowed(principal, requested_tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant mismatch."
        )

    return principal.tenant_id or "default"


def envelope(data: Any, request: Optional[Request] = None) -> Dict[str, Any]:
    """Wrap a successful payload in the canonical response envelope.

    {success: true, data: <payload>, request_id: <id>}

    Every JSON endpoint returns this so clients (React dashboard, Python/JS SDKs,
    curl) read `success`/`data` uniformly. request_id is echoed from the
    inbound header when present so a caller can trace a request end-to-end.
    """
    rid = ""
    if request is not None:
        rid = request.headers.get("X-Request-ID", "")
    return {"success": True, "data": data, "request_id": rid}


# --- PYDANTIC SCHEMAS ---

class SynapseAPIModel(BaseModel):
    """Request/response models accept canonical aliases and Python field names.

    Clients in Python naturally serialize fields as snake_case (tenant_id,
    max_token_budget), while the public JSON API also documents camelCase aliases
    (tenantId, maxTokens). Pydantic otherwise silently ignores the snake_case key
    when an alias is configured, which can silently change a request's tenant.
    """
    model_config = ConfigDict(populate_by_name=True)


class IngestRequest(SynapseAPIModel):
    content: str = Field(..., min_length=1, max_length=32000, description="Raw text representation of the memory segment")
    category: str = Field("interaction", min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$", description="Cognitive category (e.g., interaction, preference, system)")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Confidence alignment coefficient")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$", description="Target tenant ID for isolation")
    source_type: Literal["unspecified", "user", "tool", "document", "system", "import"] = "unspecified"
    source_id: Optional[str] = Field(None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$")
    observed_at: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    valid_from: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    valid_until: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    trust_level: Literal["unverified", "low", "medium", "high", "authoritative"] = "unverified"

    @model_validator(mode="after")
    def validate_provenance_window(self):
        if self.valid_from is not None and self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class IngestResponse(SynapseAPIModel):
    id: str
    status: str
    token_cost: int
    tenant_id: str


class ProtectedConstraint(SynapseAPIModel):
    """Caller-supplied mandatory context, kept separate from ranked memories."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    kind: Literal["goal", "authorization", "safety", "invariant"] = Field(
        ..., description="Constraint classification; not an authorization proof"
    )
    text: str = Field(..., min_length=1, max_length=2000, description="Constraint text")

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("protected constraint text must not be blank")
        return value


class QueryRequest(SynapseAPIModel):
    prompt: Optional[str] = Field(None, min_length=1, max_length=8000, description="Active user prompt")
    query: Optional[str] = Field(None, min_length=1, max_length=8000, description="Alternative alias for prompt")
    max_token_budget: int = Field(1500, ge=50, le=8000, description="Max token window allowed for context packing", alias="maxTokens")
    category: Optional[str] = Field(None, min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$", description="Optional category filter")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$", description="Target tenant ID")
    as_of: Optional[float] = Field(None, ge=0, allow_inf_nan=False, alias="asOf", description="Evaluate temporal validity at this Unix timestamp")
    run_id: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$", description="Optionally attach this retrieval to an active tenant-scoped agent run")
    protected_constraints: List[ProtectedConstraint] = Field(
        default_factory=list,
        max_length=8,
        description="Mandatory caller-supplied context, reserved before optional memory packing",
    )

    @model_validator(mode="after")
    def protected_constraint_payload_is_bounded(self):
        total_chars = sum(len(item.text) for item in self.protected_constraints)
        if total_chars > 8000:
            raise ValueError("protected_constraints may contain at most 8000 characters in total")
        return self


class QueryResponse(SynapseAPIModel):
    fused_context: str
    injected_nodes_count: int
    injected_nodes: List[Dict[str, Any]]
    active_storage_backend: str
    retrieval_as_of: Optional[float] = None
    retrieval_id: Optional[str] = None
    protected_constraints_count: int = 0
    protected_constraints_token_cost: int = 0


def _format_protected_constraints(constraints: List[ProtectedConstraint]) -> str:
    """Serialize required caller context as a separate, bounded context section."""
    if not constraints:
        return ""
    records = [
        {"kind": item.kind, "text": item.text}
        for item in constraints
    ]
    return (
        "## Protected task constraints\n"
        "The following caller-supplied items are mandatory context, separate from "
        "optional memories. Synapse preserves them but does not independently "
        "authenticate their authority.\n"
        + json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


class RetractionRequest(SynapseAPIModel):
    reason: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")


class RunCreateRequest(SynapseAPIModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    run_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    schema_version: Literal[1] = 1
    tenant_id: Optional[str] = Field(
        None, alias="tenantId", min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9_\-\.]+$",
    )


class MemoryEvidenceRequest(SynapseAPIModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    memory_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    rank: int = Field(..., ge=1, le=10000)
    score_components: Dict[str, float] = Field(default_factory=dict, max_length=8)
    token_cost: int = Field(0, ge=0, le=100000)
    packed: bool = False
    created_at: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    supersedes: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    superseded_by: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    source_type: Optional[Literal["unspecified", "user", "tool", "document", "system", "import"]] = None
    source_id: Optional[str] = Field(None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$")
    observed_at: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    valid_from: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    valid_until: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    trust_level: Optional[Literal["unverified", "low", "medium", "high", "authoritative"]] = None
    supersession_reason: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    retracted_at: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    retraction_reason: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")


class AgentRunEventRequest(SynapseAPIModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal[1] = 1
    event_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    run_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: RunEventType
    timestamp: datetime.datetime
    run_status: RunStatus
    step_index: Optional[int] = Field(None, ge=1)
    tool_call_index: Optional[int] = Field(None, ge=1)
    tool_name: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    tool_fingerprint: Optional[str] = Field(None, pattern=r"^[a-fA-F0-9]{64}$")
    reason: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    error_type: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    retrieval_id: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    retrieval_as_of: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    protected_constraints_count: Optional[int] = Field(None, ge=0, le=8)
    protected_constraints_token_cost: Optional[int] = Field(None, ge=0, le=8000)
    memory_evidence: List[MemoryEvidenceRequest] = Field(default_factory=list, max_length=100)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def retrieval_metadata_must_match_event_type(self):
        protected_metadata = (
            self.protected_constraints_count,
            self.protected_constraints_token_cost,
        )
        if (protected_metadata[0] is None) != (protected_metadata[1] is None):
            raise ValueError("protected constraint count and token cost must be supplied together")
        if self.event_type is RunEventType.MEMORY_RETRIEVAL:
            if not self.retrieval_id:
                raise ValueError("memory.retrieval requires retrieval_id")
        elif (
            self.retrieval_id is not None
            or self.retrieval_as_of is not None
            or self.memory_evidence
            or any(value is not None for value in protected_metadata)
        ):
            raise ValueError("retrieval metadata is only valid for memory.retrieval")
        return self


class FeedbackRequest(SynapseAPIModel):
    memory_id: str = Field(..., min_length=1, max_length=256, description="Memory node ID")
    feedback_type: str = Field(..., pattern=r"^(positive|negative)$", description="Must be either 'positive' (boost) or 'negative' (hallucination alert)")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")


class CreateKeyRequest(SynapseAPIModel):
    name: str = Field(..., min_length=1, max_length=128)
    organization_id: str = Field("org_default", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")
    project_id: str = Field("proj_default", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")
    tenant_id: str = Field("default", min_length=1, max_length=128, pattern=r"^([A-Za-z0-9_\-\.]+|\*)$")
    scopes: List[str] = Field(["memories:read", "memories:write"], min_length=1, max_length=16)
    # Accepts the canonical roles plus the legacy "admin" string. The legacy
    # string stays schema-valid so can_create_api_key() can reject it with a
    # clear 403 ("not assignable") instead of a generic 422, and so the role
    # hierarchy is actually reachable through the API (F-01).
    role: str = Field(
        "developer",
        pattern=r"^(admin|platform_admin|organization_admin|workspace_admin|developer|service|viewer)$",
    )
    ttl_days: Optional[int] = Field(365, ge=1, le=3650)


class RevokeKeyRequest(SynapseAPIModel):
    key_id: str = Field(..., min_length=1, max_length=128)


AUTH_KEYS_CREATE_SCOPE = "auth:keys:create"


# --- Telemetry (no auth) ---

@app.get("/liveness", tags=["Telemetry"])
def liveness():
    """Returns simple process liveness state.

    DB-independent by design: the engine can be running and the process alive
    even when the database or auth backend has failed. K8s `livenessProbe`
    depends on this route; failures restart the container but do not take it
    out of rotation until /readiness fails.
    """
    return {"status": "OK", "timestamp": time.time()}


@app.get("/readiness", tags=["Telemetry"])
def readiness():
    """Checks actual database and auth backend usability.

    Performs a bounded live health check with a short timeout (F-07). This is
    the Kubernetes readiness probe: it must fail closed (503) when the storage
    backend becomes unreachable so traffic is shed before requests start
    failing.
    """
    if ACTIVE_BACKEND == "pgvector":
        # Bounded live check (F-07): trigger a real connection attempt with timeout.
        # NOTE: psycopg2 connections have no execute() (that is a psycopg3 API), so
        # the check must go through a cursor — otherwise readiness always 503s and
        # the k8s probe keeps the engine out of rotation forever.
        conn = None
        try:
            conn = store._get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            conn.rollback()  # end the probe's transaction before returning it
        except Exception as e:
            logger.warning("Readiness check failed: " + str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "UNHEALTHY",
                    "storage_backend": ACTIVE_BACKEND,
                    "reason": "PostgreSQL connection unavailable."
                },
            )
        finally:
            if conn is not None:
                store._return_connection(conn)

    return {
        "status": "READY",
        "timestamp": time.time(),
        "storage_backend": ACTIVE_BACKEND,
        "embedding_provider": embedder.provider_name,
        "embedding_is_semantic": embedder.is_semantic
    }


@app.get("/health", tags=["Telemetry"])
def get_health():
    """Returns active service telemetry, storage backend, and security status."""
    db_ok = True
    if ACTIVE_BACKEND == "pgvector":
        db_ok = store.is_connected

    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "UNHEALTHY",
                "storage_backend": ACTIVE_BACKEND,
                "reason": "Database connection offline"
            }
        )

    return {
        "status": "HEALTHY",
        "timestamp": time.time(),
        "storage_backend": ACTIVE_BACKEND,
        "hnsw_enabled": ACTIVE_BACKEND == "pgvector",
        "row_level_security": "ENFORCED" if ACTIVE_BACKEND == "pgvector" else "TENANT_INDEXED",
        "total_indexed_memories": store.count_memories(),
        "embedding_provider": embedder.provider_name,
        "engine_budget_solver": "Knapsack DP 0/1",
        "auth_engine": "Hashed RBAC + Audit Log"
    }


@app.get("/api/memories", tags=["Memory Operations"])
@app.get("/api/memories/list", tags=["Memory Operations"])
def list_memories(
    request: Request,
    tenant_id: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    principal: APIKeyRecord = Depends(require_scope("memories:read"))
):
    """Lists memories for the authenticated tenant with pagination."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if category:
        mems = store.get_memories_by_category(category, tenant_id=effective_tenant)
        # Full category-filtered set: length is the accurate total.
        total = len(mems)
    else:
        # Pagination total must be the REAL corpus size (via COUNT), not the
        # length of the fetched page window.
        if hasattr(store, "count_active_memories"):
            total = store.count_active_memories(tenant_id=effective_tenant)  # type: ignore
        elif hasattr(store, "count_memories"):
            total = store.count_memories(tenant_id=effective_tenant)  # type: ignore
        else:
            total = None  # type: ignore
        # Prefer bounded candidate query when available to avoid full-table loads
        if hasattr(store, "query_candidates"):
            try:
                mems = store.query_candidates(tenant_id=effective_tenant, limit=limit + offset)  # type: ignore
            except Exception:
                mems = store.get_all_memories(tenant_id=effective_tenant)
        else:
            mems = store.get_all_memories(tenant_id=effective_tenant)
        if total is None:
            total = len(mems)

    page = mems[offset : offset + limit]

    return envelope(
        {
            "memories": page,
            "tenant_id": effective_tenant or "all",
            "total": total,
            "limit": limit,
            "offset": offset,
            "storage_backend": ACTIVE_BACKEND,
        },
        request,
    )


@app.post("/ingest", status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/create", status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/add", status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
def ingest_memory(
    request: Request,
    payload: IngestRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:write"))
):
    """
    Ingests memory segments with tenant isolation, cryptographic payload encryption,
    semantic deduplication, and Knapsack token footprint calculation.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content string cannot be empty.")

    effective_tenant = resolve_tenant(principal, payload.tenant_id)

    global _ingest_success, _ingest_errors
    try:
        memory_id, action, cost = manager.ingest_with_deduplication(
            content=payload.content,
            category=payload.category,
            confidence=payload.confidence,
            tenant_id=effective_tenant,
            source_type=payload.source_type,
            source_id=payload.source_id,
            observed_at=payload.observed_at,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            trust_level=payload.trust_level,
        )
    except Exception:
        with _metrics_lock:
            _ingest_errors += 1
        raise
    with _metrics_lock:
        _ingest_success += 1

    return envelope(
        {
            "id": memory_id,
            "status": f"SUCCESS_{action}",
            "token_cost": cost,
            "tenant_id": effective_tenant,
        },
        request,
    )


@app.post("/query", tags=["Retrieval"])
@app.post("/api/memories/query", tags=["Retrieval"])
@app.post("/api/retrieval/knapsack-pack", tags=["Retrieval"])
def query_memory(
    request: Request,
    payload: QueryRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:read"))
):
    """
    Performs reciprocal hybrid vector search with Ebbinghaus temporal decay
    and packs candidate nodes optimally into context budget using 0/1 Knapsack DP.
    """
    query_text = payload.prompt or payload.query or ""
    retrieval_as_of = time.time() if payload.as_of is None else payload.as_of
    protected_context_block = _format_protected_constraints(payload.protected_constraints)
    protected_constraints_count = len(payload.protected_constraints)
    # Include formatting/separator overhead in the reserved estimate so optional
    # memories cannot consume budget needed to retain the required context.
    protected_constraints_token_cost = (
        estimate_token_cost(protected_context_block + "\n\n---\n\n")
        if protected_context_block else 0
    )
    try:
        # Validate the mandatory reservation before retrieval or empty-query exits.
        packer.pack(
            [], payload.max_token_budget,
            reserved_token_cost=protected_constraints_token_cost,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not query_text.strip():
        return envelope(QueryResponse(
            fused_context=protected_context_block, injected_nodes_count=0, injected_nodes=[],
            active_storage_backend=ACTIVE_BACKEND, retrieval_as_of=retrieval_as_of,
            protected_constraints_count=protected_constraints_count,
            protected_constraints_token_cost=protected_constraints_token_cost,
        ).model_dump(), request)

    effective_tenant = resolve_tenant(principal, payload.tenant_id)
    if payload.run_id and "agent-runs:write" not in principal.scopes:
        raise HTTPException(
            status_code=403,
            detail="Agent retrieval tracing requires the agent-runs:write scope.",
        )
    query_vector = embedder.embed_query(query_text)

    # 1. Candidate retrieval. On the pgvector backend, hybrid search (dense HNSW
    #    + lexical tsvector) is fused inside a single SQL query (the scale path).
    #    On SQLite we load the tenant corpus and fuse in Python (dev/local only).
    candidates = []
    if ACTIVE_BACKEND == "pgvector" and hasattr(store, "hybrid_search_sql"):
        candidates = store.hybrid_search_sql(
            query=query_text,
            query_vec=query_vector,
            top_k=25,
            category=payload.category,
            tenant_id=effective_tenant,
            temporal_as_of=retrieval_as_of,
        )

    if not candidates:
        # Fallback / SQLite path: dense + lexical pools fused in Python.
        dense_candidates = store.vector_search(
            query_vec=query_vector,
            top_k=100,
            category=payload.category,
            tenant_id=effective_tenant,
            temporal_as_of=retrieval_as_of,
        )
        lexical_candidates = store.lexical_search(
            query=query_text,
            top_k=100,
            category=payload.category,
            tenant_id=effective_tenant,
            temporal_as_of=retrieval_as_of,
        )

        candidate_map = {m["id"]: m for m in dense_candidates}
        for m in lexical_candidates:
            if m["id"] not in candidate_map:
                candidate_map[m["id"]] = m

        candidate_docs = list(candidate_map.values())
        if not candidate_docs:
            candidate_docs = store.get_all_memories(tenant_id=effective_tenant)
        candidate_docs = [
            memory for memory in candidate_docs
            if is_retrieval_eligible(memory, retrieval_as_of)
        ]

        # Empty candidate sets still pass through packing and trace correlation.
        # This preserves an explainable retrieval event even when no eligible
        # memories were found for the query.
        if candidate_docs:
            # 2. Reciprocal Rank Fusion (RRF) in Python for the non-pgvector backends.
            candidates = searcher.fused_search(
                query_text,
                candidate_docs,
                query_vector=query_vector,
                top_k=25,
                category_filter=payload.category,
                tenant_filter=effective_tenant
            )

    # 2. Dynamic temporal decay adjustment: S(m,q,t) = R(m,q) * D(m,t).
    # Preserve retrieval relevance; decay only re-weights priority.
    decayed_candidates = []
    for m in candidates:
        mem_node = {
            "created_at": m["created_at"],
            "last_accessed_at": m.get("last_accessed_at", m["created_at"]),
            "access_count": m.get("access_count", 0),
            "confidence": m.get("confidence", 0.5),
            "category": m.get("category", "interaction")
        }
        retrieval_score = float(m.get("relevance_score", 0.0))
        combined = decay.combine_scores(retrieval_score, mem_node)
        m_copy = m.copy()
        m_copy["retrieval_score"] = retrieval_score
        m_copy["decay_multiplier"] = combined / retrieval_score if retrieval_score else decay.temporal_multiplier(mem_node)
        m_copy["relevance_score"] = combined
        decayed_candidates.append(m_copy)

    # 3. Reserve required context first; relevance-ranked memories use only the remainder.
    try:
        packed_nodes = packer.pack(
            decayed_candidates,
            payload.max_token_budget,
            reserved_token_cost=protected_constraints_token_cost,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    retrieval_id: Optional[str] = None
    if payload.run_id:
        import uuid as _uuid

        retrieval_id = "ret_" + _uuid.uuid4().hex
        packed_ids = {node["id"] for node in packed_nodes}
        evidence: List[Dict[str, Any]] = []
        for rank, node in enumerate(decayed_candidates[:100], start=1):
            explanation = node.get("explanation") or {}
            if not isinstance(explanation, dict):
                explanation = {}
            scores: Dict[str, float] = {}
            score_sources = {
                "relevance_score": node.get("relevance_score"),
                "retrieval_score": node.get("retrieval_score"),
                "decay_multiplier": node.get("decay_multiplier"),
                "lexical_rank": explanation.get("sparse_rank", node.get("lexical_rank")),
                "dense_rank": explanation.get("dense_rank"),
                "fused_score": explanation.get("fused_score"),
                "bm25_score": explanation.get("lexical_score", node.get("bm25_score")),
                "semantic_score": explanation.get("semantic_score", node.get("semantic_score")),
            }
            for key, value in score_sources.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    scores[key] = float(value)
            item: Dict[str, Any] = {
                "memory_id": node["id"],
                "rank": rank,
                "score_components": scores,
                "token_cost": max(0, int(node.get("token_cost", 0) or 0)),
                "packed": node["id"] in packed_ids,
            }
            for field_name in (
                "created_at", "supersedes", "superseded_by", "source_type",
                "source_id", "observed_at", "valid_from", "valid_until",
                "trust_level", "supersession_reason", "retracted_at",
                "retraction_reason",
            ):
                value = node.get(field_name)
                if value is None:
                    continue
                if field_name in {"supersedes", "superseded_by"}:
                    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
                        continue
                elif field_name == "source_id":
                    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value):
                        continue
                elif field_name == "source_type":
                    if value not in {"unspecified", "user", "tool", "document", "system", "import"}:
                        continue
                elif field_name == "trust_level":
                    if value not in {"unverified", "low", "medium", "high", "authoritative"}:
                        continue
                elif field_name in {"supersession_reason", "retraction_reason"}:
                    # Older rows may contain free-form legacy values. Persist
                    # controlled codes only; never copy arbitrary text into traces.
                    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value):
                        continue
                elif field_name in {"created_at", "observed_at", "valid_from", "valid_until", "retracted_at"}:
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value < 0
                    ):
                        continue
                item[field_name] = value
            evidence.append(item)

        try:
            run_trace_store.append_event(effective_tenant, payload.run_id, {
                "schema_version": 1,
                "event_id": "evt_" + _uuid.uuid4().hex,
                "run_id": payload.run_id,
                "event_type": RunEventType.MEMORY_RETRIEVAL.value,
                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                "run_status": RunStatus.RUNNING.value,
                "retrieval_id": retrieval_id,
                "retrieval_as_of": retrieval_as_of,
                "protected_constraints_count": protected_constraints_count,
                "protected_constraints_token_cost": protected_constraints_token_cost,
                "memory_evidence": evidence,
            })
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found for the authenticated tenant.") from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail="Run cannot accept another retrieval event.") from exc
        except RunTraceError as exc:
            raise HTTPException(status_code=503, detail="Retrieval trace storage unavailable.") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    global _retrieval_success, _last_candidate_count, _last_packed_tokens
    with _metrics_lock:
        _retrieval_success += 1
        _last_candidate_count = len(candidates)
        _last_packed_tokens = protected_constraints_token_cost + sum(
            int(n.get("token_cost", 0) or 0) for n in packed_nodes
        )

    # Increment access counts for Ebbinghaus stabilization
    for node in packed_nodes:
        store.update_access_count(node["id"], 1, tenant_id=effective_tenant)

    # Formulate fused context block
    context_lines = []
    for n in packed_nodes:
        context_lines.append(f"[{n.get('category', 'MEMORY').upper()}] (Confidence: {n.get('confidence', 0.9):.2f}): {n.get('content', '')}")

    memory_context_block = "\n---\n".join(context_lines)
    context_sections = [
        section for section in (protected_context_block, memory_context_block) if section
    ]
    fused_context_block = "\n\n---\n\n".join(context_sections)

    return envelope(
        {
            "fused_context": fused_context_block,
            "injected_nodes_count": len(packed_nodes),
            "injected_nodes": packed_nodes,
            "active_storage_backend": ACTIVE_BACKEND,
            "retrieval_as_of": retrieval_as_of,
            "retrieval_id": retrieval_id,
            "protected_constraints_count": protected_constraints_count,
            "protected_constraints_token_cost": protected_constraints_token_cost,
        },
        request,
    )


@app.post("/api/agent-runs", tags=["Agent Reliability"])
def create_agent_run(
    request: Request,
    payload: RunCreateRequest,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:write")),
):
    """Create a tenant-bound run before streaming events (or before manual upload)."""
    effective_tenant = resolve_tenant(principal, payload.tenant_id)
    try:
        result = run_trace_store.create_run(
            effective_tenant, payload.run_id, schema_version=payload.schema_version,
        )
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope(result, request)


@app.post("/api/agent-runs/{run_id}/events", tags=["Agent Reliability"])
def append_agent_run_event(
    request: Request,
    payload: AgentRunEventRequest,
    run_id: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:write")),
):
    """Append one versioned event; retries with the same event ID are idempotent."""
    if payload.run_id != run_id:
        raise HTTPException(status_code=422, detail="event run_id does not match the path run_id.")
    effective_tenant = resolve_tenant(principal, tenant_id)
    event = payload.model_dump(exclude_none=True)
    event["event_type"] = payload.event_type.value
    event["run_status"] = payload.run_status.value
    event["memory_evidence"] = [
        item.model_dump(exclude_none=True) for item in payload.memory_evidence
    ]
    try:
        result = run_trace_store.append_event(effective_tenant, run_id, event)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found for the authenticated tenant.") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope(result, request)


@app.get("/api/agent-runs", tags=["Agent Reliability"])
def list_agent_runs(
    request: Request,
    tenant_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:read")),
):
    """List recent run summaries for the authenticated tenant."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    try:
        runs = run_trace_store.list_runs(effective_tenant, limit=limit, offset=offset)
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope({"runs": runs, "limit": limit, "offset": offset}, request)


@app.get("/api/agent-runs/{run_id}", tags=["Agent Reliability"])
def get_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:read")),
):
    """Fetch one run summary without returning event payloads by default."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    try:
        result = run_trace_store.get_run(effective_tenant, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found for the authenticated tenant.") from exc
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope(result, request)


@app.get("/api/agent-runs/{run_id}/events", tags=["Agent Reliability"])
def list_agent_run_events(
    request: Request,
    run_id: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    tenant_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:read")),
):
    """Read a bounded, chronological metadata-only event page."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    try:
        events = run_trace_store.list_events(
            effective_tenant, run_id, limit=limit, offset=offset,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found for the authenticated tenant.") from exc
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope({"run_id": run_id, "events": events, "limit": limit, "offset": offset}, request)


@app.delete("/api/agent-runs/{run_id}", tags=["Agent Reliability"])
def delete_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("agent-runs:delete")),
):
    """Delete a run and its events for data lifecycle/retention workflows."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    try:
        deleted = run_trace_store.delete_run(effective_tenant, run_id)
    except RunTraceError as exc:
        raise HTTPException(status_code=503, detail="Agent trace storage unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope({"run_id": run_id, "status": "DELETED" if deleted else "NOT_FOUND"}, request)


@app.post("/feedback", tags=["Reinforcement"])
def submit_feedback(
    request: Request,
    payload: FeedbackRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:write"))
):
    """Applies RLAIF reinforcement feedback to boost or demote memory relevance score."""
    effective_tenant = resolve_tenant(principal, payload.tenant_id)
    node = store.get_memory_by_id(payload.memory_id, tenant_id=effective_tenant)
    if not node:
        raise HTTPException(status_code=404, detail="Memory node ID not found in store.")

    old_conf = node.get("confidence", 0.5)
    new_conf = decay.compute_feedback_boost(old_conf, payload.feedback_type)
    store.update_confidence(payload.memory_id, new_conf, tenant_id=effective_tenant)

    return envelope(
        {
            "id": payload.memory_id,
            "feedback_status": "PROCESSED",
            "old_confidence": old_conf,
            "new_confidence": new_conf,
            "tenant_id": effective_tenant,
        },
        request,
    )


@app.delete("/api/memories/{memory_id}", tags=["Memory Operations"])
def delete_memory(
    request: Request,
    memory_id: str,
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:delete"))
):
    """Deletes memory node by ID with tenant isolation."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    store.delete_memory(memory_id, tenant_id=effective_tenant)
    return envelope({"status": "DELETED", "id": memory_id}, request)


@app.post("/api/memories/{memory_id}/retract", tags=["Memory Operations"])
def retract_memory(
    request: Request,
    payload: RetractionRequest,
    memory_id: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:delete")),
):
    """Retract evidence while retaining its record and provenance lineage."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    try:
        changed = store.retract_memory(
            memory_id, tenant_id=effective_tenant, reason=payload.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Memory retraction unavailable.") from exc
    if not changed:
        existing = store.get_memory_by_id(memory_id, tenant_id=effective_tenant)
        if existing is None:
            raise HTTPException(status_code=404, detail="Memory not found for the authenticated tenant.")
        return envelope({
            "id": memory_id,
            "status": "ALREADY_RETRACTED" if existing.get("retracted_at") is not None else "NOT_RETRACTED",
            "retracted_at": existing.get("retracted_at"),
            "retraction_reason": existing.get("retraction_reason"),
        }, request)

    existing = store.get_memory_by_id(memory_id, tenant_id=effective_tenant)
    security_manager.log_audit_action(
        actor_key_id=principal.key_id,
        tenant_id=effective_tenant,
        action="MEMORY_RETRACT",
        resource=memory_id,
        status="SUCCESS",
        metadata={"reason": payload.reason},
    )
    return envelope({
        "id": memory_id,
        "status": "RETRACTED",
        "retracted_at": existing.get("retracted_at") if existing else None,
        "retraction_reason": payload.reason,
    }, request)


@app.post("/api/memories/clear", tags=["Memory Operations"])
def clear_memories(
    request: Request,
    payload: Optional[Dict[str, Any]] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:delete"))
):
    """Flushes memory corpus for the authenticated tenant."""
    requested_tenant = payload.get("tenant_id") if payload else None
    effective_tenant = resolve_tenant(principal, requested_tenant)
    deleted_count = store.clear_memories(tenant_id=effective_tenant)
    security_manager.log_audit_action(
        actor_key_id=principal.key_id,
        tenant_id=effective_tenant or "all",
        action="MEMORIES_CLEAR",
        resource="memories",
        status="SUCCESS",
        metadata={"deleted_count": deleted_count}
    )
    return envelope(
        {
            "status": "CLEARED",
            "tenant_id": effective_tenant or "all",
            "deleted_count": deleted_count,
        },
        request,
    )


@app.post("/api/security/gdpr-delete", tags=["Security"])
def gdpr_delete(
    request: Request,
    payload: Dict[str, Any],
    principal: APIKeyRecord = Depends(require_scope("memories:delete"))
):
    """
    Executes an authenticated GDPR Right to be Forgotten deletion path.
    Performs PostgreSQL transaction DELETE on tenant memories and returns actual deleted row count.
    """
    requested_tenant = payload.get("tenant_id") or payload.get("tenantId")
    effective_tenant = resolve_tenant(principal, requested_tenant)
    deleted_count = store.clear_memories(tenant_id=effective_tenant)
    security_manager.log_audit_action(
        actor_key_id=principal.key_id,
        tenant_id=effective_tenant or "all",
        action="GDPR_RIGHT_TO_BE_FORGOTTEN_DELETE",
        resource="memories",
        status="SUCCESS",
        metadata={"deleted_count": deleted_count}
    )
    return envelope(
        {
            "status": "SUCCESS",
            "tenant_id": effective_tenant or "all",
            "deleted_count": deleted_count,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "audit_logged": True,
        },
        request,
    )


# --- ENTERPRISE SECURITY & API KEY ENDPOINTS ---

@app.post("/api/auth/keys/create", tags=["Enterprise Security"])
def create_api_key(
    request: Request,
    payload: CreateKeyRequest,
    principal: APIKeyRecord = Depends(require_scope(AUTH_KEYS_CREATE_SCOPE)),
):
    """Generates a new cryptographic API Key with RBAC scopes.

    P1.1/P1.2: key minting is authorization-aware. The authenticated principal may
    only mint keys inside the workspaces they control, at a role that their own
    role permits, and MUST NOT be allowed to elevate to platform-wide admin. The
    'admin' scope is never auto-promoted: requested role/scopes are authoritative
    and validated before minting.
    """
    # Resolve the caller's effective workspace binding from their key.
    caller_tenant = principal.tenant_id or "default"
    caller_org = principal.organization_id or "org_default"

    # Target workspace that the new key is bound to. Defaults to the caller's own
    # binding so a caller cannot mint a key for an org/workspace they do not own.
    target_org = (payload.organization_id or caller_org).strip() or caller_org
    target_workspace = payload.tenant_id or caller_tenant
    requested_role = payload.role or "developer"

    allowed, reason = can_create_api_key(
        principal,
        target_org=target_org,
        target_workspace=target_workspace,
        requested_role=requested_role,
        # F-01: the requested scopes MUST reach the delegation check, otherwise
        # a workspace admin can mint scopes=["admin"]/"security:audit" that the
        # guard was written to block.
        requested_scopes=payload.scopes,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    # Defense-in-depth: still re-validate the org/project/tenant bounding via the
    # generic hierarchy check (system principals bypass it).
    ok, reason = security_manager.check_hierarchy(
        principal,
        requested_org=target_org,
        requested_project=payload.project_id,
        requested_tenant=target_workspace,
    )
    if not ok:
        raise HTTPException(status_code=403, detail=reason)

    token, record = security_manager.generate_api_key(
        name=payload.name,
        organization_id=target_org,
        project_id=payload.project_id,
        tenant_id=target_workspace,
        scopes=payload.scopes,
        role=requested_role,
        ttl_days=payload.ttl_days,
        owner_id=principal.key_id,
    )

    return envelope(
        {
            "status": "CREATED",
            "api_key": token,
            "key_id": record.key_id,
            "name": record.name,
            "scopes": record.scopes,
            "tenant_id": record.tenant_id,
            "expires_at": record.expires_at,
        },
        request,
    )


@app.get("/api/auth/keys", tags=["Enterprise Security"])
def list_api_keys(
    request: Request,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Lists registered API keys without exposing secrets."""
    scoped_org = (
        principal.organization_id
        if principal.organization_id not in ("org_root", "org_system")
        else None
    )
    return envelope(
        {"keys": security_manager.list_api_keys(organization_id=scoped_org)},
        request,
    )


@app.post("/api/auth/keys/revoke", tags=["Enterprise Security"])
def revoke_api_key(
    request: Request,
    payload: RevokeKeyRequest,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Revokes an API key immediately.

    P1.1: an admin can only revoke keys it could have listed — i.e. keys inside
    its own org boundary (system principals see and manage everything).
    """
    scoped_org = (
        principal.organization_id
        if principal.organization_id not in ("org_root", "org_system")
        else None
    )
    target = next(
        (k for k in security_manager.list_api_keys(organization_id=scoped_org)
         if k.get("key_id") == payload.key_id),
        None,
    )
    if target is None:
        # Never leak whether a key exists outside the caller's boundary.
        return envelope({"status": "NOT_FOUND", "key_id": payload.key_id}, request)
    ok, reason = security_manager.check_hierarchy(
        principal,
        requested_org=target.get("organization_id") or None,
        requested_project=target.get("project_id") or None,
        requested_tenant=target.get("tenant_id") or None,
    )
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    success = security_manager.revoke_api_key(payload.key_id)
    return envelope({"status": "REVOKED" if success else "NOT_FOUND", "key_id": payload.key_id}, request)


@app.get("/api/auth/audit", tags=["Enterprise Security"])
def get_audit_trail(
    request: Request,
    limit: int = 50,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Retrieves authorization audit logs."""
    logs = security_manager.get_audit_logs(limit=min(limit, 200), tenant_id=principal.tenant_id if principal.tenant_id != "*" else None)
    return envelope({"audit_logs": logs}, request)


@app.get("/api/auth/audit/export", tags=["Enterprise Security"])
def export_audit_trail(
    request: Request,
    since: Optional[float] = None,
    limit: int = 5000,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Compliance export of audit records (tenant-scoped, bounded, time-ordered).

    Intended for archival into SIEM/object storage. Non-admins export only
    their own tenant; admins export everything via tenant wildcard.
    """
    scoped_tenant = None if principal.tenant_id in ("*", "") else principal.tenant_id
    logs = security_manager.export_audit_logs(
        tenant_id=scoped_tenant,
        since=since,
        limit=min(limit, 20000),
    )
    return envelope({"audit_logs": logs, "exported": len(logs)}, request)


@app.post("/api/auth/audit/prune", tags=["Enterprise Security"])
def prune_audit_trail(
    request: Request,
    payload: Optional[Dict[str, Any]] = None,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Delete audit records older than the retention window (default 90 days).

    Body: {"older_than_days": <float>}. Returns the deleted row count.
    """
    days = 90.0
    if payload and payload.get("older_than_days") is not None:
        try:
            days = float(payload["older_than_days"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="older_than_days must be a number.")
    # P1.1: tenant-bound admins prune ONLY their own audit trail; only system
    # principals (env master / org_system) run a global retention pass.
    scoped_tenant = (
        None
        if security_manager.is_system_principal(principal) or principal.tenant_id in ("*", "")
        else principal.tenant_id
    )
    deleted = security_manager.prune_audit_logs(
        older_than_seconds=max(0.0, days) * 86400, tenant_id=scoped_tenant
    )
    security_manager.log_audit_action(
        actor_key_id=principal.key_id,
        tenant_id=principal.tenant_id,
        action="AUDIT_RETENTION_PRUNE",
        resource="auth_audit_logs",
        status="SUCCESS",
        metadata={"deleted_count": deleted, "older_than_days": days},
    )
    return envelope({"deleted": deleted, "older_than_days": days}, request)


class RotateKeyRequest(SynapseAPIModel):
    key_id: str


@app.post("/api/auth/keys/rotate", tags=["Enterprise Security"])
def rotate_api_key(
    request: Request,
    payload: RotateKeyRequest,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Rotates an API key: revokes old, issues new with same metadata. Zero-downtime rotation.

    P1.1: the target must live inside the caller's org boundary (system
    principals manage everything) and inherit only hierarchy-compatible
    metadata.
    """
    scoped_org = (
        principal.organization_id
        if principal.organization_id not in ("org_root", "org_system")
        else None
    )
    keys = security_manager.list_api_keys(organization_id=scoped_org)
    old = next((k for k in keys if k.get("key_id") == payload.key_id), None)
    if not old:
        # 404 both for missing keys and keys outside the caller's boundary.
        raise HTTPException(status_code=404, detail="Key not found.")
    ok, reason = security_manager.check_hierarchy(
        principal,
        requested_org=old.get("organization_id") or None,
        requested_project=old.get("project_id") or None,
        requested_tenant=old.get("tenant_id") or None,
    )
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    token, record = security_manager.generate_api_key(
        name=old.get("name", "rotated"),
        organization_id=old.get("organization_id", principal.organization_id),
        project_id=old.get("project_id", "proj_default"),
        tenant_id=old.get("tenant_id", principal.tenant_id),
        scopes=old.get("scopes", ["memories:read", "memories:write"]),
        role=old.get("role", "developer"),
        ttl_days=365,
    )
    security_manager.revoke_api_key(payload.key_id)
    return envelope(
        {
            "status": "ROTATED",
            "old_key_id": payload.key_id,
            "api_key": token,
            "key_id": record.key_id,
            "expires_at": record.expires_at,
        },
        request,
    )


class BulkIngestItem(SynapseAPIModel):
    content: str = Field(..., min_length=1, max_length=32000)
    category: str = Field("interaction", min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$")
    confidence: float = Field(0.95, ge=0.0, le=1.0)


class BulkIngestRequest(SynapseAPIModel):
    items: List[BulkIngestItem] = Field(..., min_length=1, max_length=100)
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")


@app.post("/api/memories/bulk", tags=["Ingestion"])
def bulk_ingest(
    request: Request,
    payload: BulkIngestRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:write"))
):
    """Bulk ingest up to 100 memories in one call (team backfills)."""
    total_chars = sum(len(i.content) for i in payload.items)
    if total_chars > 500_000:
        raise HTTPException(status_code=413, detail="Bulk payload too large (500KB text limit).")
    effective_tenant = resolve_tenant(principal, payload.tenant_id)
    results = []
    for item in payload.items:
        if not item.content.strip():
            continue
        mid, action, cost = manager.ingest_with_deduplication(
            content=item.content,
            category=item.category,
            confidence=max(0.0, min(1.0, item.confidence)),
            tenant_id=effective_tenant,
        )
        results.append({"id": mid, "status": action, "token_cost": cost})
    return envelope({"tenant_id": effective_tenant, "ingested": len(results), "results": results}, request)


@app.get("/metrics", tags=["Telemetry"])
def prometheus_metrics():
    """Prometheus-compatible metrics (no extra deps). Scrape with Prometheus/Grafana.

    All values are live: request counters + latency samples by endpoint,
    auth failures, rate-limit rejections, ingestion/retrieval volumes and
    errors, pool utilization, token-budget usage. No hardcoded figures.
    """
    total = 0
    try:
        total = store.count_memories()
    except Exception:
        pass
    pool_stats: Dict[str, Any] = {}
    for candidate in (store, security_manager):
        stats_fn = getattr(candidate, "pool_stats", None)
        if not callable(stats_fn):
            continue
        try:
            candidate_stats = stats_fn() or {}
        except Exception:
            logger.debug("pool_stats() failed for %r", candidate)
            candidate_stats = {}
        if candidate_stats:
            pool_stats = candidate_stats
            break
    lines = [
        "# HELP synapse_memories_total Total indexed memories",
        "# TYPE synapse_memories_total gauge",
        f"synapse_memories_total{{backend=\"{ACTIVE_BACKEND}\"}} {total}",
        "# HELP synapse_up Service liveness",
        "# TYPE synapse_up gauge",
        "synapse_up 1",
        "# HELP synapse_embedding_dimension Active embedding dimension",
        "# TYPE synapse_embedding_dimension gauge",
        f"synapse_embedding_dimension {embedding_dimension}",
        "# HELP synapse_requests_total Requests by endpoint and status",
        "# TYPE synapse_requests_total counter",
    ]
    with _metrics_lock:
        for (route_template, status_code), count in sorted(_REQUEST_COUNTS.items()):
            lines.append(f"synapse_requests_total{{path=\"{route_template}\",status=\"{status_code}\"}} {count}")
        lines += [
            "# HELP synapse_auth_failures_total Authentication/authorization failures",
            "# TYPE synapse_auth_failures_total counter",
            f"synapse_auth_failures_total {_auth_failures}",
            "# HELP synapse_rate_limit_rejections_total Rate-limit 429 rejections",
            "# TYPE synapse_rate_limit_rejections_total counter",
            f"synapse_rate_limit_rejections_total {_rate_limit_rejections}",
            "# HELP synapse_ingest_total Ingested memories and failures",
            "# TYPE synapse_ingest_total counter",
            f"synapse_ingest_total{{outcome=\"success\"}} {_ingest_success}",
            f"synapse_ingest_total{{outcome=\"error\"}} {_ingest_errors}",
            "# HELP synapse_retrieval_total Retrieval requests and errors",
            "# TYPE synapse_retrieval_total counter",
            f"synapse_retrieval_total{{outcome=\"success\"}} {_retrieval_success}",
            f"synapse_retrieval_total{{outcome=\"error\"}} {_retrieval_errors}",
            "# HELP synapse_retrieval_candidates Candidate count of last retrieval",
            "# TYPE synapse_retrieval_candidates gauge",
            f"synapse_retrieval_candidates {_last_candidate_count}",
            "# HELP synapse_context_tokens_packed Tokens packed into last context",
            "# TYPE synapse_context_tokens_packed gauge",
            f"synapse_context_tokens_packed {_last_packed_tokens}",
        ]
    lines += [
        "# HELP synapse_db_pool_in_use Pooled DB connections currently checked out",
        "# TYPE synapse_db_pool_in_use gauge",
        f"synapse_db_pool_in_use {pool_stats.get('in_use', 0)}",
        "# HELP synapse_db_pool_size Configured DB pool size",
        "# TYPE synapse_db_pool_size gauge",
        f"synapse_db_pool_size {pool_stats.get('pool_size', 0)}",
        "# HELP synapse_rate_limit_backend Active rate-limiter backend (constant 1)",
        "# TYPE synapse_rate_limit_backend gauge",
        f'synapse_rate_limit_backend{{backend="{rate_limiter.backend}"}} 1',
    ]
    lat_lines = ["# HELP synapse_request_latency_seconds Request latency by endpoint", "# TYPE synapse_request_latency_seconds summary"]
    with _metrics_lock:
        for path, samples in sorted(_LATENCY_SAMPLES.items()):
            if not samples:
                continue
            ordered = sorted(samples)
            n = len(ordered)
            avg = sum(ordered) / n
            p50 = ordered[min(n - 1, n // 2)]
            p95 = ordered[min(n - 1, int(n * 0.95))]
            lat_lines.append(f"synapse_request_latency_seconds{{path=\"{path}\",quantile=\"0.5\"}} {p50:.6f}")
            lat_lines.append(f"synapse_request_latency_seconds{{path=\"{path}\",quantile=\"0.95\"}} {p95:.6f}")
            lat_lines.append(f"synapse_request_latency_seconds_sum{{path=\"{path}\"}} {sum(ordered):.6f}")
            lat_lines.append(f"synapse_request_latency_seconds_count{{path=\"{path}\"}} {n}")
            lat_lines.append(f"# avg {avg:.6f}s over {n} samples")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(lines + lat_lines) + "\n", media_type="text/plain")


@app.post("/api/security/scrub-pii", tags=["Security"])
@app.post("/api/security/scrub", tags=["Security"])
def scrub_pii(
    request: Request,
    payload: Dict[str, Any],
    principal: APIKeyRecord = Depends(require_scope("memories:read"))
):
    """
    Authenticated PII scrubbing endpoint — the single implementation.

    The Node gateway proxies both of its aliases here instead of running its own
    weaker regex set (F-11). Ordering matters: high-entropy identifiers and
    structured digit runs (card / Aadhaar / PAN / SSN) are redacted BEFORE the
    generic phone matcher, which would otherwise swallow them and leave them
    only *counted* as detected rather than actually removed.

    Nothing cryptographic happens in this handler, so the response carries no
    encryption/CMEK claim.
    """
    import re

    text = payload.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="Text field required.")
    if len(text) > 20000:
        raise HTTPException(status_code=413, detail="Text exceeds the 20000 character limit.")

    redactions = [
        (
            r"(sk-proj-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9\-_]{20,}"
            r"|xox[bpas]-[A-Za-z0-9\-_]{10,}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|AKIA[0-9A-Z]{16})",
            "[REDACTED_API_KEY]",
        ),
        (
            r"\b(?:4[0-9]{3}|5[1-5][0-9]{2}|3[47][0-9]{2}|6(?:011|5[0-9]{2}))"
            r"[-\s]?[0-9]{4}[-\s]?[0-9]{4}[-\s]?[0-9]{1,4}\b",
            "[REDACTED_CARD]",
        ),
        (r"\b[0-9]{4}[-\s]?[0-9]{4}[-\s]?[0-9]{4}\b", "[REDACTED_AADHAAR]"),
        (r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "[REDACTED_PAN]"),
        (r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b", "[REDACTED_SSN]"),
        (r"[\w\.-]+@[\w\.-]+\.\w+", "[REDACTED_EMAIL]"),
        (r"password\s*[:=]\s*\S+", "password=[REDACTED]"),
        (r"\+?[0-9][0-9\-.\s()]{7,}[0-9]", "[REDACTED_PHONE]"),
    ]

    scrubbed = text
    redaction_count = 0
    for pattern, replacement in redactions:
        scrubbed, count = re.subn(pattern, replacement, scrubbed)
        redaction_count += count

    return envelope(
        {
            "detectedPII": redaction_count > 0,
            "scrubbedText": scrubbed,
            "redactionCount": redaction_count,
            "scrubber": "synapse-engine/regex-v2",
        },
        request,
    )


if __name__ == "__main__":
    uvicorn.run("fastapi_server:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8008)), reload=True)  # nosec B104
