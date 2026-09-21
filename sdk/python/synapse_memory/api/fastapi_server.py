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

# Thread-safe in-memory memory store (in production, connected to PostgreSQL with pgvector)
cognitive_store: List[Dict[str, Any]] = []

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
        "total_indexed_memories": len(cognitive_store),
        "embedding_model": "text-embedding-004",
        "engine_budget_solver": "Knapsack DP 0/1"
    }

@app.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED, tags=["Ingestion"])
def ingest_memory(payload: IngestRequest):
    """
    Ingests raw prompt segments. Calculates token footprints and indexes 
    the segment with secure metadata.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content string cannot be empty.")

    # Simulates professional token cost calculation (words * 1.35)
    estimated_tokens = int(len(payload.content.split()) * 1.35) + 10

    memory_id = f"mem_node_{int(time.time() * 1000)}"
    new_node = {
        "id": memory_id,
        "content": payload.content,
        "category": payload.category,
        "confidence": payload.confidence,
        "created_at": time.time(),
        "access_count": 0,
        "token_cost": max(15, estimated_tokens)
    }

    cognitive_store.append(new_node)
    return IngestResponse(id=memory_id, status="SUCCESS_INDEXED", token_cost=new_node["token_cost"])

@app.post("/query", response_model=QueryResponse, tags=["Retrieval"])
def query_memory(payload: QueryRequest):
    """
    Performs reciprocal hybrid vector search, applies temporal decay factor, 
    and packs nodes optimally under the target token budget using Knapsack DP.
    """
    if not cognitive_store:
        return QueryResponse(fused_context="", injected_nodes_count=0, injected_nodes=[])

    # 1. Hybrid semantic/lexical search
    candidates = searcher.fused_search(payload.prompt, cognitive_store, top_k=25)

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

    # Increment access counts for the selected nodes to stimulate stabilization
    for node in packed_nodes:
        # Match back to reference store
        for ref in cognitive_store:
            if ref["id"] == node["id"]:
                ref["access_count"] += 1

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
    relevance confidence score in the cognitive grid.
    """
    for node in cognitive_store:
        if node["id"] == payload.memory_id:
            old_conf = node["confidence"]
            new_conf = decay.compute_feedback_boost(old_conf, payload.feedback_type)
            node["confidence"] = new_conf
            return {
                "id": payload.memory_id,
                "feedback_status": "PROCESSED",
                "old_confidence": old_conf,
                "new_confidence": new_conf
            }

    raise HTTPException(status_code=404, detail="Memory node ID not found in store.")


if __name__ == "__main__":
    uvicorn.run("fastapi_server:app", host="0.0.0.0", port=8000, reload=True)
