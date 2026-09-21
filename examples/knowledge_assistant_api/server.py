from fastapi import FastAPI
from pydantic import BaseModel
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedding_provider import LocalEmbeddingProvider, EmbeddingManager
from synapse_memory.core.memory_manager import MemoryManager

app = FastAPI()

# Initialize library (In a real app, use dependency injection or app state)
store = SQLiteMemoryStore(db_path="knowledge_base.db")
embedder = EmbeddingManager(LocalEmbeddingProvider(dim=128))
manager = MemoryManager(store=store, embedder=embedder)

class MemoryRequest(BaseModel):
    content: str
    category: str = "general"

@app.post("/ingest")
async def ingest(request: MemoryRequest):
    mid, status, cost = manager.ingest_with_deduplication(
        request.content, category=request.category
    )
    return {"id": mid, "status": status, "cost": cost}

@app.get("/memories")
async def get_memories():
    return store.get_all_memories()
