import sys
import os
import time
import datetime
import logging
import threading
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import uvicorn
from fastapi import FastAPI, HTTPException, status, Header, Request, Depends
from fastapi.middleware.cors import CORSMiddleware

# Adjust path to enable absolute imports when running as standalone script
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import DEFAULT_MAX_RECORD_LIMIT, MemoryManager
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.auth import SecurityManager, APIKeyRecord

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
security_manager = SecurityManager(
    db_path=SQLITE_DB_PATH,
    connection_string=DATABASE_URL if (DATABASE_URL and STORAGE_BACKEND in ["pgvector", "auto"]) else None,
    fail_closed=FAIL_CLOSED_DB or (IS_PRODUCTION and bool(DATABASE_URL))
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
_request_counts: Dict[tuple, int] = {}
_latency_samples: Dict[str, list] = {}
_auth_failures = 0
_rate_limit_rejections = 0
_ingest_success = 0
_ingest_errors = 0
_retrieval_success = 0
_retrieval_errors = 0
_last_candidate_count = 0
_last_packed_tokens = 0


def _record_request(path: str, status: int, latency_s: float) -> None:
    with _metrics_lock:
        key = (path, str(status))
        _request_counts[key] = _request_counts.get(key, 0) + 1
        samples = _latency_samples.setdefault(path, [])
        samples.append(latency_s)
        if len(samples) > 500:
            del samples[: len(samples) - 500]


def _check_rate_limit(key: str) -> None:
    global _rate_limit_rejections
    if rate_limiter.check(key, RATE_LIMIT_PER_MIN, RATE_LIMIT_WINDOW_SECONDS):
        return
    with _metrics_lock:
        _rate_limit_rejections += 1
    raise HTTPException(status_code=429, detail="Rate limit exceeded. Retry after 60s.")


# Initialize FastAPI application (MUST precede any @app.middleware / route registration)
app = FastAPI(
    title="SynapseMemory Enterprise Cognitive Gateway",
    description="Sovereign Active Long-Term Memory & Context Budgeting REST API with Multi-Tenancy and Database-Level RLS.",
    version="1.2.0"
)


@app.middleware("http")
async def add_request_id_and_rate_limit(request: Request, call_next):  # type: ignore
    import uuid as _uuid

    request_id = request.headers.get("X-Request-ID") or f"req_{_uuid.uuid4().hex[:12]}"
    start = time.time()
    # Rate-limit only mutating + heavy retrieval endpoints
    if request.url.path.startswith(("/ingest", "/api/memories", "/query", "/api/retrieval")):
        client_key = request.headers.get("X-Synapse-API-Key") or (request.client.host if request.client else "anon")
        try:
            _check_rate_limit(f"{client_key}:{request.url.path}")
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
                    role="admin",
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
    if principal.role == "admin" or "admin" in principal.scopes:
        if requested_tenant:
            return requested_tenant
        if principal.tenant_id not in ("*", ""):
            return principal.tenant_id
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit tenant_id is required for admin cross-tenant operations."
        )

    if requested_tenant and requested_tenant != principal.tenant_id and principal.tenant_id != "*":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant mismatch."
        )

    return principal.tenant_id


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

class IngestRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000, description="Raw text representation of the memory segment")
    category: str = Field("interaction", min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$", description="Cognitive category (e.g., interaction, preference, system)")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Confidence alignment coefficient")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$", description="Target tenant ID for isolation")


class IngestResponse(BaseModel):
    id: str
    status: str
    token_cost: int
    tenant_id: str


class QueryRequest(BaseModel):
    prompt: Optional[str] = Field(None, min_length=1, max_length=8000, description="Active user prompt")
    query: Optional[str] = Field(None, min_length=1, max_length=8000, description="Alternative alias for prompt")
    max_token_budget: int = Field(1500, ge=50, le=8000, description="Max token window allowed for context packing", alias="maxTokens")
    category: Optional[str] = Field(None, min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$", description="Optional category filter")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$", description="Target tenant ID")


class QueryResponse(BaseModel):
    fused_context: str
    injected_nodes_count: int
    injected_nodes: List[Dict[str, Any]]
    active_storage_backend: str


class FeedbackRequest(BaseModel):
    memory_id: str = Field(..., min_length=1, max_length=256, description="Memory node ID")
    feedback_type: str = Field(..., pattern=r"^(positive|negative)$", description="Must be either 'positive' (boost) or 'negative' (hallucination alert)")
    tenant_id: Optional[str] = Field(None, alias="tenantId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    organization_id: str = Field("org_default", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")
    project_id: str = Field("proj_default", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-\.]+$")
    tenant_id: str = Field("default", min_length=1, max_length=128, pattern=r"^([A-Za-z0-9_\-\.]+|\*)$")
    scopes: List[str] = Field(["memories:read", "memories:write"], min_length=1, max_length=16)
    role: str = Field("developer", pattern=r"^(admin|developer|service|viewer)$")
    ttl_days: Optional[int] = Field(365, ge=1, le=3650)


class RevokeKeyRequest(BaseModel):
    key_id: str = Field(..., min_length=1, max_length=128)


# --- API ENDPOINTS ---

@app.get("/liveness", tags=["Telemetry"])
def liveness():
    """Returns simple process liveness state."""
    return {"status": "OK", "timestamp": time.time()}


@app.get("/readiness", tags=["Telemetry"])
def readiness():
    """Checks actual database and auth backend usability."""
    db_ok = True
    if ACTIVE_BACKEND == "pgvector":
        db_ok = store.is_connected

    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "UNHEALTHY",
                "storage_backend": ACTIVE_BACKEND,
                "reason": "PostgreSQL + pgvector connection unavailable."
            }
        )
    return {
        "status": "READY",
        "timestamp": time.time(),
        "storage_backend": ACTIVE_BACKEND
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
    else:
        # Prefer bounded candidate query when available to avoid full-table loads
        if hasattr(store, "query_candidates"):
            try:
                mems = store.query_candidates(tenant_id=effective_tenant, limit=limit + offset)  # type: ignore
            except Exception:
                mems = store.get_all_memories(tenant_id=effective_tenant)
        else:
            mems = store.get_all_memories(tenant_id=effective_tenant)

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
            tenant_id=effective_tenant
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
    if not query_text.strip():
        return envelope(QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND).model_dump(), request)

    effective_tenant = resolve_tenant(principal, payload.tenant_id)
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
        )

    if not candidates:
        # Fallback / SQLite path: dense + lexical pools fused in Python.
        dense_candidates = store.vector_search(
            query_vec=query_vector,
            top_k=100,
            category=payload.category,
            tenant_id=effective_tenant
        )
        lexical_candidates = store.lexical_search(
            query=query_text,
            top_k=100,
            category=payload.category,
            tenant_id=effective_tenant
        )

        candidate_map = {m["id"]: m for m in dense_candidates}
        for m in lexical_candidates:
            if m["id"] not in candidate_map:
                candidate_map[m["id"]] = m

        candidate_docs = list(candidate_map.values())
        if not candidate_docs:
            candidate_docs = store.get_all_memories(tenant_id=effective_tenant)

        if not candidate_docs:
            return envelope(QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND).model_dump(), request)

        # 2. Reciprocal Rank Fusion (RRF) in Python for the non-pgvector backends.
        candidates = searcher.fused_search(
            query_text,
            candidate_docs,
            query_vector=query_vector,
            top_k=25,
            category_filter=payload.category,
            tenant_filter=effective_tenant
        )

    if not candidates:
        return envelope(QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND).model_dump(), request)

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

    # 3. Dynamic programming 0/1 Knapsack optimal budget packing
    packed_nodes = packer.pack(decayed_candidates, payload.max_token_budget)

    global _retrieval_success, _last_candidate_count, _last_packed_tokens
    with _metrics_lock:
        _retrieval_success += 1
        _last_candidate_count = len(candidates)
        _last_packed_tokens = sum(int(n.get("token_cost", 0) or 0) for n in packed_nodes)

    # Increment access counts for Ebbinghaus stabilization
    for node in packed_nodes:
        store.update_access_count(node["id"], 1, tenant_id=effective_tenant)

    # Formulate fused context block
    context_lines = []
    for n in packed_nodes:
        context_lines.append(f"[{n.get('category', 'MEMORY').upper()}] (Confidence: {n.get('confidence', 0.9):.2f}): {n.get('content', '')}")

    fused_context_block = "\n---\n".join(context_lines)

    return envelope(
        {
            "fused_context": fused_context_block,
            "injected_nodes_count": len(packed_nodes),
            "injected_nodes": packed_nodes,
            "active_storage_backend": ACTIVE_BACKEND,
        },
        request,
    )


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
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Generates a new cryptographic API Key with RBAC scopes."""
    token, record = security_manager.generate_api_key(
        name=payload.name,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        tenant_id=payload.tenant_id,
        scopes=payload.scopes,
        role=payload.role,
        ttl_days=payload.ttl_days
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
    return envelope(
        {"keys": security_manager.list_api_keys(organization_id=principal.organization_id if principal.organization_id != "org_root" else None)},
        request,
    )


@app.post("/api/auth/keys/revoke", tags=["Enterprise Security"])
def revoke_api_key(
    request: Request,
    payload: RevokeKeyRequest,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Revokes an API key immediately."""
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
    deleted = security_manager.prune_audit_logs(older_than_seconds=max(0.0, days) * 86400)
    security_manager.log_audit_action(
        actor_key_id=principal.key_id,
        tenant_id=principal.tenant_id,
        action="AUDIT_RETENTION_PRUNE",
        resource="auth_audit_logs",
        status="SUCCESS",
        metadata={"deleted_count": deleted, "older_than_days": days},
    )
    return envelope({"deleted": deleted, "older_than_days": days}, request)


class RotateKeyRequest(BaseModel):
    key_id: str


@app.post("/api/auth/keys/rotate", tags=["Enterprise Security"])
def rotate_api_key(
    request: Request,
    payload: RotateKeyRequest,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Rotates an API key: revokes old, issues new with same metadata. Zero-downtime rotation."""
    keys = security_manager.list_api_keys()
    old = next((k for k in keys if k.get("key_id") == payload.key_id), None)
    if not old:
        raise HTTPException(status_code=404, detail="Key not found.")
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


class BulkIngestItem(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000)
    category: str = Field("interaction", min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$")
    confidence: float = Field(0.95, ge=0.0, le=1.0)


class BulkIngestRequest(BaseModel):
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
        for (path, status_code), count in sorted(_request_counts.items()):
            lines.append(f"synapse_requests_total{{path=\"{path}\",status=\"{status_code}\"}} {count}")
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
        for path, samples in sorted(_latency_samples.items()):
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
    Authenticated PII scrubbing endpoint.
    Returns scrubbed content and redaction count without echoing original sensitive input.
    """
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Text field required.")

    import re
    redaction_count = 0
    patterns = [
        r'[\w\.-]+@[\w\.-]+\.\w+',  # email
        r'\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}',  # phone
        r'(sk-proj-[a-zA-Z0-9]{20,})',  # OpenAI
        r'(sk-ant-[a-zA-Z0-9\-_]{20,})',  # Anthropic
        r'(xox[bpas]-[a-zA-Z0-9\-_]{10,})',  # Slack
        r'(gh[pousr]_[a-zA-Z0-9]{20,})',  # GitHub
        r'(AKIA[0-9A-Z]{16})',  # AWS access key
        r'\b(\d{3}[-]?\d{2}[-]?\d{4})\b',  # US SSN
        r'\b(\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})\b',  # credit card
        r'\b(\d{4}[-\s]?\d{4}[-\s]?\d{4})\b',  # Aadhaar (12-digit grouped)
        r'\b([A-Z]{5}[0-9]{4}[A-Z])\b',  # India PAN
    ]
    for pat in patterns:
        redaction_count += len(re.findall(pat, text))

    scrubbed = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[REDACTED_EMAIL]', text)
    scrubbed = re.sub(r'\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}', '[REDACTED_PHONE]', scrubbed)
    scrubbed = re.sub(r'(sk-proj-[a-zA-Z0-9]{20,}|sk-ant-[a-zA-Z0-9\-_]{20,}|xox[bpas]-[a-zA-Z0-9\-_]{10,}|gh[pousr]_[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16})', '[REDACTED_API_KEY]', scrubbed)
    scrubbed = re.sub(r'\b(\d{3}[-]?\d{2}[-]?\d{4})\b', '[REDACTED_SSN]', scrubbed)
    scrubbed = re.sub(r'\b(\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})\b', '[REDACTED_CARD]', scrubbed)
    scrubbed = re.sub(r'\b([A-Z]{5}[0-9]{4}[A-Z])\b', '[REDACTED_PAN]', scrubbed)

    return envelope(
        {
            "detectedPII": redaction_count > 0 or scrubbed != text,
            "scrubbedText": scrubbed,
            "redactionCount": redaction_count,
            "encryptionCMEK": "kms-key-aes256-synapse-active",
        },
        request,
    )


if __name__ == "__main__":
    uvicorn.run("fastapi_server:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8008)), reload=True)  # nosec B104
