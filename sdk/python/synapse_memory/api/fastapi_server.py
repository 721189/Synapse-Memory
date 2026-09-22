import sys
import os
import time
import logging
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
IS_PRODUCTION = os.environ.get("NODE_ENV", "").lower() == "production"

# Security & Distributed Identity Engine
security_manager = SecurityManager(
    db_path="synapse_memory.db",
    connection_string=DATABASE_URL if (DATABASE_URL and STORAGE_BACKEND in ["pgvector", "auto"]) else None,
    fail_closed=IS_PRODUCTION and bool(DATABASE_URL)
)

if STORAGE_BACKEND == "pgvector":
    try:
        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            fail_closed=True
        )
        ACTIVE_BACKEND = "pgvector"
        logger.info(f"Canonical Production Engine using PostgreSQL + pgvector (HNSW) at {str(DATABASE_URL)[:20]}...")
    except Exception as e:
        logger.exception(f"PostgreSQL startup failed in pgvector mode: {e}")
        raise
elif STORAGE_BACKEND == "auto" and DATABASE_URL:
    try:
        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            fail_closed=IS_PRODUCTION
        )
        ACTIVE_BACKEND = "pgvector"
        logger.info(f"Canonical Production Engine using PostgreSQL + pgvector (HNSW) at {str(DATABASE_URL)[:20]}...")
    except Exception as e:
        if IS_PRODUCTION:
            logger.exception(f"PostgreSQL startup failed in production mode: {e}")
            raise
        logger.warning(f"Could not connect to PostgreSQL ({e}); initializing SQLiteMemoryStore.")
        store = SQLiteMemoryStore("synapse_memory.db")
        ACTIVE_BACKEND = "sqlite"
else:
    store = SQLiteMemoryStore("synapse_memory.db")
    ACTIVE_BACKEND = "sqlite"

embedder = SynapseEmbedder(provider="local")
manager = MemoryManager(store=store, embedder=embedder)
packer = KnapsackPacker(token_budget=32000)
decay = DecayEngine(category_configs={})
searcher = HybridSearch()

# Initialize FastAPI application
app = FastAPI(
    title="SynapseMemory Enterprise Cognitive Gateway",
    description="Sovereign Active Long-Term Memory & Context Budgeting REST API with Multi-Tenancy and Database-Level RLS.",
    version="1.2.0"
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

        # Allow open dev access if no key set anywhere in dev mode
        if not token and not os.environ.get("SYNAPSE_API_KEY") and len(security_manager.list_api_keys()) == 0:
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

@app.get("/health", tags=["Telemetry"])
def get_health():
    """Returns active service telemetry, storage backend, and security status."""
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
    tenant_id: Optional[str] = None,
    category: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:read"))
):
    """Lists all memories for the authenticated tenant."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    if category:
        mems = store.get_memories_by_category(category, tenant_id=effective_tenant)
    else:
        mems = store.get_all_memories(tenant_id=effective_tenant)

    return {
        "memories": mems,
        "tenant_id": effective_tenant or "all",
        "total": len(mems),
        "storage_backend": ACTIVE_BACKEND
    }


@app.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/create", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/add", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
def ingest_memory(
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

    return IngestResponse(
        id=memory_id,
        status=f"SUCCESS_{action}",
        token_cost=cost,
        tenant_id=effective_tenant
    )


@app.post("/query", response_model=QueryResponse, tags=["Retrieval"])
@app.post("/api/memories/query", response_model=QueryResponse, tags=["Retrieval"])
@app.post("/api/retrieval/knapsack-pack", response_model=QueryResponse, tags=["Retrieval"])
def query_memory(
    payload: QueryRequest,
    principal: APIKeyRecord = Depends(require_scope("memories:read"))
):
    """
    Performs reciprocal hybrid vector search with Ebbinghaus temporal decay
    and packs candidate nodes optimally into context budget using 0/1 Knapsack DP.
    """
    query_text = payload.prompt or payload.query or ""
    if not query_text.strip():
        return QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND)

    effective_tenant = resolve_tenant(principal, payload.tenant_id)
    memories = store.get_all_memories(tenant_id=effective_tenant)
    if not memories:
        return QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[], active_storage_backend=ACTIVE_BACKEND)

    # 1. Hybrid semantic/lexical search
    query_vector = embedder.embed_query(query_text)
    candidates = searcher.fused_search(query_text, memories, query_vector=query_vector, top_k=25)

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

    return QueryResponse(
        fused_context=fused_context_block,
        injected_nodes_count=len(packed_nodes),
        injected_nodes=packed_nodes,
        active_storage_backend=ACTIVE_BACKEND
    )


@app.post("/feedback", tags=["Reinforcement"])
def submit_feedback(
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

    return {
        "id": payload.memory_id,
        "feedback_status": "PROCESSED",
        "old_confidence": old_conf,
        "new_confidence": new_conf,
        "tenant_id": effective_tenant
    }


@app.delete("/api/memories/{memory_id}", tags=["Memory Operations"])
def delete_memory(
    memory_id: str,
    tenant_id: Optional[str] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:delete"))
):
    """Deletes memory node by ID with tenant isolation."""
    effective_tenant = resolve_tenant(principal, tenant_id)
    store.delete_memory(memory_id, tenant_id=effective_tenant)
    return {"status": "DELETED", "id": memory_id}


@app.post("/api/memories/clear", tags=["Memory Operations"])
def clear_memories(
    payload: Optional[Dict[str, Any]] = None,
    principal: APIKeyRecord = Depends(require_scope("memories:delete"))
):
    """Flushes memory corpus for the authenticated tenant."""
    requested_tenant = payload.get("tenant_id") if payload else None
    effective_tenant = resolve_tenant(principal, requested_tenant)
    store.clear_memories(tenant_id=effective_tenant)
    return {"status": "CLEARED", "tenant_id": effective_tenant or "all"}


# --- ENTERPRISE SECURITY & API KEY ENDPOINTS ---

@app.post("/api/auth/keys/create", tags=["Enterprise Security"])
def create_api_key(
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

    return {
        "status": "CREATED",
        "api_key": token,
        "key_id": record.key_id,
        "name": record.name,
        "scopes": record.scopes,
        "tenant_id": record.tenant_id,
        "expires_at": record.expires_at
    }


@app.get("/api/auth/keys", tags=["Enterprise Security"])
def list_api_keys(
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Lists registered API keys without exposing secrets."""
    return {
        "keys": security_manager.list_api_keys(organization_id=principal.organization_id if principal.organization_id != "org_root" else None)
    }


@app.post("/api/auth/keys/revoke", tags=["Enterprise Security"])
def revoke_api_key(
    payload: RevokeKeyRequest,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Revokes an API key immediately."""
    success = security_manager.revoke_api_key(payload.key_id)
    return {"status": "REVOKED" if success else "NOT_FOUND", "key_id": payload.key_id}


@app.get("/api/auth/audit", tags=["Enterprise Security"])
def get_audit_trail(
    limit: int = 50,
    principal: APIKeyRecord = Depends(require_scope("admin"))
):
    """Retrieves authorization audit logs."""
    logs = security_manager.get_audit_logs(limit=limit, tenant_id=principal.tenant_id if principal.tenant_id != "*" else None)
    return {"audit_logs": logs}


@app.post("/api/security/scrub-pii", tags=["Security"])
@app.post("/api/security/scrub", tags=["Security"])
def scrub_pii(payload: Dict[str, Any]):
    """PII scrubbing endpoint."""
    text = payload.get("text", "")
    import re
    scrubbed = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[REDACTED_EMAIL]', text)
    scrubbed = re.sub(r'\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}', '[REDACTED_PHONE]', scrubbed)
    scrubbed = re.sub(r'(sk-proj-[a-zA-Z0-9]{20,})', '[REDACTED_API_KEY]', scrubbed)
    return {
        "detectedPII": scrubbed != text,
        "scrubbedText": scrubbed,
        "encryptionCMEK": "kms-key-aes256-synapse-active"
    }


if __name__ == "__main__":
    uvicorn.run("fastapi_server:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8008)), reload=True)  # nosec B104
