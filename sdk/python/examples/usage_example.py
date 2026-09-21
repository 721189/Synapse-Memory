import os
from synapse_memory import MemoryManager, SQLiteMemoryStore, SynapseEmbedder

# 1. Initialize Components
# Using local embedder for self-contained example
embedder = SynapseEmbedder(provider="local")
store = SQLiteMemoryStore(db_path="example_memories.db") # Persistent for example
manager = MemoryManager(store=store, embedder=embedder, max_record_limit=10)

# 2. Ingest Memories
id1, status1, cost1 = manager.ingest_with_deduplication(
    content="The project infrastructure is deployed on AWS.",
    category="infrastructure"
)
print(f"Ingestion 1: {id1}, Status: {status1}, Cost: {cost1}")

# 3. Ingest Duplicate (Should Merge)
id2, status2, cost2 = manager.ingest_with_deduplication(
    content="Project infra is on AWS.",
    category="infrastructure"
)
print(f"Ingestion 2: {id2}, Status: {status2}, Cost: {cost2}")

# 4. Ingest Distinct Memory
id3, status3, cost3 = manager.ingest_with_deduplication(
    content="User reported a bug in the login flow.",
    category="interaction"
)
print(f"Ingestion 3: {id3}, Status: {status3}, Cost: {cost3}")

print("\nExample complete.")
