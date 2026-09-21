from fastapi import FastAPI
from synapse_memory import MemoryManager, SQLiteMemoryStore, SynapseEmbedder
from pydantic import BaseModel

app = FastAPI()

# Simple initialization for API example
store = SQLiteMemoryStore(db_path="example_memories.db")
embedder = SynapseEmbedder(provider="local")
manager = MemoryManager(store=store, embedder=embedder)

class MemoryIngest(BaseModel):
    content: str
    category: str = "interaction"

@app.post("/ingest")
async def ingest(request: MemoryIngest):
    m_id, status, cost = await manager.ingest_async(request.content, category=request.category)
    return {"id": m_id, "status": status, "token_cost": cost}

@app.get("/health")
async def health():
    return {"status": "ok"}
