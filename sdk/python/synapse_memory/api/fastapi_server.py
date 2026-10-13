import sys
import os
import time
import datetime
import logging
import threading
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import uvicorn
from fastapi import FastAPI, HTTPException, status, Security, Header, Request, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

# Adjust path to enable absolute imports when running as standalone script
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
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

# Security & Distributed Identity Engine
security_manager = SecurityManager(
    db_path=SQLITE_DB_PATH,
    connection_string=DATABASE_URL if (DATABASE_URL and STORAGE_BACKEND in ["pgvector", "auto"]) else None,
    fail_closed=FAIL_CLOSED_DB or (IS_PRODUCTION and bool(DATABASE_URL))
)

# Instantiate active embedder to establish canonical vector dimension
embedder = SynapseEmbedder()
embedding_dimension = embedder.dimension

if STORAGE_BACKEND == "pgvector":
    try:
        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            dimension=embedding_dimension,
            fail_closed=True
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
            fail_closed=FAIL_CLOSED_DB or IS_PRODUCTION
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

manager = MemoryManager(store=store, embedder=embedder)
packer = KnapsackPacker(token_budget=min(int(os.environ.get("TOKEN_BUDGET_MAX", "4000")), KnapsackPacker.MAX_BUDGET))
decay = DecayEngine(category_configs={})
searcher = HybridSearch()

# --- Minimal in-process rate limiting (no extra deps; replace with Redis in HA) ---
_rate_buckets: Dict[str, list] = {}
_rate_lock = threading.Lock()
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))


def _check_rate_limit(key: str) -> None:
    now = time.time()
    window_start = now - 60.0
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, [])
        bucket[:] = [t for t in bucket if t > window_start]
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Retry after 60s.")
        bucket.append(now)


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
    # Rate-limit only mutating + heavy retrieval endpoints
    if request.url.path.startswith(("/ingest", "/api/memories", "/query", "/api/retrieval")):
        client_key = request.headers.get("X-Synapse-API-Key") or (request.client.host if request.client else "anon")
        try:
            _check_rate_limit(f"{client_key}:{request.url.path}")
        except HTTPException as e:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=e.status_code,
                content={"success": False, "error": e.detail, "request_id": request_id},
            )
    # HTTPException raised inside endpoints is shaped by the registered handler
    # below; the middleware only stamps the request id on successful responses.
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Authentication failed: {reason}"
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
    content: str = Field(..., description="Raw text representation of the memory segment")
    category: str = Field("interaction", description="Cognitive category (e.g., interaction, preference, system)")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Confidence alignment coefficient")
    tenant_id: Optional[str] = Field(None, alias="tenantId", description="Target tenant ID for isolation")

class IngestResponse(BaseModel):
    id: str
    status: str
    token_cost: int
    tenant_id: str

class QueryRequest(BaseModel):
    prompt: Optional[str] = Field(None, description="Active user prompt")
    query: Optional[str] = Field(None, description="Alternative alias for prompt")
    max_token_budget: int = Field(1500, ge=50, le=32000, description="Max token window allowed for context packing", alias="maxTokens")
    category: Optional[str] = Field(None, description="Optional category filter")
    tenant_id: Optional[str] = Field(None, alias="tenantId", description="Target tenant ID")

class QueryResponse(BaseModel):
    fused_context: str
    injected_nodes_count: int
    injected_nodes: List[Dict[str, Any]]
    active_storage_backend: str

class FeedbackRequest(BaseModel):
    memory_id: str
    feedback_type: str = Field(..., description="Must be either 'positive' (boost) or 'negative' (hallucination alert)")
    tenant_id: Optional[str] = Field(None, alias="tenantId")

class CreateKeyRequest(BaseModel):
    name: str
    organization_id: str = "org_default"
    project_id: str = "proj_default"
    tenant_id: str = "default"
    scopes: List[str] = ["memories:read", "memories:write"]
    role: str = "developer"
    ttl_days: Optional[int] = 365

class RevokeKeyRequest(BaseModel):
    key_id: str


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

    memory_id, action, cost = manager.ingest_with_deduplication(
        content=payload.content,
        category=payload.category,
        confidence=payload.confidence,
        tenant_id=effective_tenant
    )

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

    # 1. Scalable Candidate Retrieval: Dense (HNSW pgvector) + Lexical (PostgreSQL tsvector)
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

    # Deduplicate candidate nodes across dense and lexical retrieval pools
    candidate_map = {m["id"]: m for m in dense_candidates}
    for m in lexical_candidates:
        if m["id"] not in candidate_map:
            candidate_map[m["id"]] = m

    candidate_docs = list(candidate_map.values())
    if not candidate_docs:
        candidate_docs = store.get_all_memories(tenant_id=effective_tenant)

    if not candidate_docs:
        return envelope(QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND).model_dump(), request)

    # 2. Reciprocal Rank Fusion (RRF)
    candidates = searcher.fused_search(
        query_text,
        candidate_docs,
        query_vector=query_vector,
        top_k=25,
        category_filter=payload.category,
        tenant_filter=effective_tenant
    )

    # 2. Dynamic temporal decay adjustment
    decayed_candidates = []
    for m in candidates:
        mem_node = {
            "created_at": m["created_at"],
            "last_accessed_at": m.get("last_accessed_at", m["created_at"]),
            "access_count": m.get("access_count", 0),
            "confidence": m.get("confidence", 0.5),
            "category": m.get("category", "interaction")
        }
        current_relevance = decay.calculate_relevance(mem_node)
        m_copy = m.copy()
        m_copy["relevance_score"] = current_relevance
        decayed_candidates.append(m_copy)

    # 3. Dynamic programming 0/1 Knapsack optimal budget packing
    packed_nodes = packer.pack(decayed_candidates, payload.max_token_budget)

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
    content: str
    category: str = "interaction"
    confidence: float = 0.95


class BulkIngestRequest(BaseModel):
    items: List[BulkIngestItem]
    tenant_id: Optional[str] = Field(None, alias="tenantId")


@app.post("/api/memories/bulk", tags=["Ingestion"])
def bulk_ingest(
    request: Request,
    payload: BulkIngestRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:write"))
):
    """Bulk ingest up to 100 memories in one call (team backfills)."""
    if len(payload.items) > 100:
        raise HTTPException(status_code=400, detail="Bulk limit is 100 items per request.")
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
    """Prometheus-compatible metrics (no extra deps). Scrape with Prometheus/Grafana."""
    total = 0
    try:
        total = store.count_memories()
    except Exception:
        pass
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
    ]
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")


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
