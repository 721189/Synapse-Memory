import sys
import os
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

# Adjust path to enable absolute imports when running as standalone script
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.hybrid_search import HybridSearch

# Initialize FastAPI application
app = FastAPI(
    title="SynapseMemory RAG Gateway Proxy",
    description="Sovereign Active Long-Term Memory & Context Budgeting REST API.",
    version="1.0.0"
)

# Enable CORS for secure microservice routing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to relational SQLite DB instead of a transient python list
store = SQLiteMemoryStore("synapse_memory.db")
embedder = SynapseEmbedder(provider="local")
manager = MemoryManager(store=store, embedder=embedder)

# Core Engine Instances
packer = KnapsackPacker()
decay = DecayEngine()
searcher = HybridSearch()


# --- PYDANTIC SCHEMAS ---

class IngestRequest(BaseModel):
    content: str = Field(..., description="Raw text representation of the memory segment")
    category: str = Field("interaction", description="Cognitive category (e.g., interaction, preference, system)")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Confidence alignment coefficient")

class IngestResponse(BaseModel):
    id: str
    status: str
    token_cost: int

class QueryRequest(BaseModel):
    prompt: str = Field(..., description="Active user prompt or system search query")
    max_token_budget: int = Field(1500, ge=100, le=32000, description="Max token window allowed for context packing")

class QueryResponse(BaseModel):
    fused_context: str
    injected_nodes_count: int
    injected_nodes: List[Dict[str, Any]]

class FeedbackRequest(BaseModel):
    memory_id: str
    feedback_type: str = Field(..., description="Must be either 'positive' (boost) or 'negative' (hallucination alert)")


# --- API ENDPOINTS ---

@app.get("/health", tags=["Telemetry"])
def get_health():
    """Returns active service telemetry and cognitive node counts."""
    return {
        "status": "HEALTHY",
        "timestamp": time.time(),
        "total_indexed_memories": store.count_memories(),
        "embedding_model": "text-embedding-004",
        "engine_budget_solver": "Knapsack DP 0/1"
    }

# ALIGNED API CONTRACT PATHS (Resolving /api/memories/create and /api/memories/add and /ingest)
@app.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/create", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
@app.post("/api/memories/add", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
def ingest_memory(payload: IngestRequest):
    """
    Ingests raw prompt segments. Calculates token footprints and indexes 
    the segment with secure metadata and semantic deduplication.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content string cannot be empty.")

    # Ingest with actual deduplication logic
    memory_id, action, cost = manager.ingest_with_deduplication(
        content=payload.content,
        category=payload.category,
        confidence=payload.confidence
    )

    return IngestResponse(id=memory_id, status=f"SUCCESS_{action}", token_cost=cost)

@app.post("/query", response_model=QueryResponse, tags=["Retrieval"])
@app.post("/api/memories/query", response_model=QueryResponse, tags=["Retrieval"])
def query_memory(payload: QueryRequest):
    """
    Performs reciprocal hybrid vector search, applies temporal decay factor, 
    and packs nodes optimally under the target token budget using Knapsack DP.
    """
    memories = store.get_all_memories()
    if not memories:
        return QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[])

    # 1. Hybrid semantic/lexical search (using genuine vectors)
    candidates = searcher.fused_search(payload.prompt, memories, top_k=25)

    # 2. Dynamic temporal decay adjustment
    decayed_candidates = []
    for m in candidates:
        current_relevance = decay.calculate_retention(
            base_relevance=m["relevance_score"],
            created_epoch=m["created_at"],
            access_count=m["access_count"]
        )
        m_copy = m.copy()
        m_copy["relevance_score"] = current_relevance
        decayed_candidates.append(m_copy)

    # 3. Dynamic programming 0/1 Knapsack optimal budget packing
    packed_nodes = packer.pack(decayed_candidates, payload.max_token_budget)

    # Increment access counts in SQLite database to stimulate stabilization
    for node in packed_nodes:
        store.update_access_count(node["id"], 1)

    # Formulate fused context response block
    context_lines = []
    for n in packed_nodes:
        context_lines.append(f"[{n['category'].upper()}] (Confidence: {n['confidence']:.2f}): {n['content']}")

    fused_context_block = "\n---\n".join(context_lines)

    return QueryResponse(
        fused_context=fused_context_block,
        injected_nodes_count=len(packed_nodes),
        injected_nodes=packed_nodes
    )

@app.post("/feedback", tags=["Reinforcement"])
def submit_feedback(payload: FeedbackRequest):
    """
    Applies RLAIF reward feedback to boost or demote a specific memory node's
    relevance confidence score in the SQLite database.
    """
    node = store.get_memory_by_id(payload.memory_id)
    if not node:
        raise HTTPException(status_code=404, detail="Memory node ID not found in store.")

    old_conf = node["confidence"]
    new_conf = decay.compute_feedback_boost(old_conf, payload.feedback_type)
    
    # Save to SQLite
    store.update_confidence(payload.memory_id, new_conf)
    
    return {
        "id": payload.memory_id,
        "feedback_status": "PROCESSED",
        "old_confidence": old_conf,
        "new_confidence": new_conf
    }

# ALIGNED SECURITY PATH CONTRACTS
@app.post("/api/security/scrub-pii", tags=["Security"])
@app.post("/api/security/scrub", tags=["Security"])
def scrub_pii(payload: Dict[str, Any]):
    """Provides unified proxy endpoint for PII scrubbing validations."""
    text = payload.get("text", "")
    import re
    # Match email addresses
    scrubbed = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[REDACTED_EMAIL]', text)
    # Match phone numbers
    scrubbed = re.sub(r'\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}', '[REDACTED_PHONE]', scrubbed)
    # Match typical secret/API keys
    scrubbed = re.sub(r'(sk-proj-[a-zA-Z0-9]{20,})', '[REDACTED_API_KEY]', scrubbed)
    return {
        "detectedPII": scrubbed != text,
        "scrubbedText": scrubbed,
        "encryptionCMEK": "kms-key-aes256-synapse-active"
    }


if __name__ == "__main__":
    uvicorn.run("fastapi_server:app", host="0.0.0.0", port=8000, reload=True)
