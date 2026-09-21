import time
import logging
import asyncio
from typing import List, Dict, Any, Tuple, Optional, Union
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.deduplicator import Deduplicator, Action
from synapse_memory.core.pruner import PruningEngine, CapacityPruningPolicy
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.observability import log_event, instrument_operation

logger = logging.getLogger("SynapseMemoryManager")


class MemoryManager:
    """
    Manages high-level memory operations: Semantic Deduplication,
    Multi-tenant Ingestion, Auto-Eviction, and Cognitive Memory Pruning.
    Compatible with both SQLiteMemoryStore and PGVectorMemoryStore backends.
    """

    def __init__(
        self,
        store: Union[SQLiteMemoryStore, PGVectorMemoryStore, Any],
        embedder: SynapseEmbedder,
        category_thresholds: Optional[Dict[str, float]] = None,
        max_record_limit: int = 50
    ):
        self.store = store
        self.embedder = embedder
        self.deduplicator = Deduplicator(category_thresholds or {})
        self.max_record_limit = max_record_limit
        self.decay_engine = DecayEngine({})
        self.pruner = PruningEngine([CapacityPruningPolicy(max_record_limit, self.decay_engine)])

    @instrument_operation("ingest_memory")
    def ingest_with_deduplication(
        self,
        content: str,
        category: str = "interaction",
        confidence: float = 0.95,
        tenant_id: str = "default"
    ) -> Tuple[str, str, int]:
        """
        Ingests a new memory node.
        Runs semantic similarity comparison against existing indexed nodes within the same tenant.
        """
        # 1. Generate dense vector representation
        new_vector = self.embedder.embed_query(content)
        existing_memories = self.store.get_memories_by_category(category, tenant_id=tenant_id)

        # 2. Check for duplicate nodes
        new_node_base = {
            "content": content,
            "category": category,
            "tenant_id": tenant_id,
            "embedding": new_vector
        }

        result = self.deduplicator.check_duplicate(new_node_base, existing_memories)

        # 3. Handle Deduplication Result
        if result.action == Action.MERGE and result.target_id:
            match_id = result.target_id
            log_event("memory_deduplication", "MERGED", {"target_id": match_id, "tenant_id": tenant_id, "reason": result.reason})

            # Reinforce: Boost confidence, update timestamp, increment access count
            self.store.update_access_count(match_id, 1, tenant_id=tenant_id)
            self.store.update_confidence(match_id, min(1.0, confidence + 0.05), tenant_id=tenant_id)

            # Re-fetch for token cost
            match_mem = next(m for m in existing_memories if m["id"] == match_id)
            return match_id, "MERGED", match_mem.get("token_cost", 20)

        elif result.action == Action.REJECT:
            log_event("memory_deduplication", "REJECTED", {"tenant_id": tenant_id, "reason": result.reason})
            return "REJECTED", "REJECTED", 0

        # 4. Otherwise, save as a clean memory
        log_event("memory_deduplication", "CREATED", {"tenant_id": tenant_id})
        memory_id = f"mem_{int(time.time() * 1000)}"
        token_cost = max(15, int(len(content.split()) * 1.35) + 10)

        new_node = {
            "id": memory_id,
            "tenant_id": tenant_id,
            "content": content,
            "category": category,
            "confidence": confidence,
            "created_at": time.time(),
            "access_count": 0,
            "token_cost": token_cost,
            "embedding": new_vector
        }

        self.store.insert_memory(new_node)

        # 5. Execute Auto-Pruning if limit is breached
        self.auto_prune_store(tenant_id=tenant_id)

        return memory_id, "CREATED", token_cost

    async def ingest_async(self, *args, **kwargs) -> Tuple[str, str, int]:
        return await asyncio.to_thread(self.ingest_with_deduplication, *args, **kwargs)

    def auto_prune_store(self, tenant_id: Optional[str] = None) -> int:
        """
        Evicts memories based on the configured PruningEngine within a tenant.

        Returns:
            The number of successfully evicted memory nodes.
        """
        memories = self.store.get_all_memories(tenant_id=tenant_id)
        targets = self.pruner.run_pruning(memories)

        evicted = 0
        for target_id in targets:
            self.store.delete_memory(target_id, tenant_id=tenant_id)
            evicted += 1
            log_event("memory_pruning", "EVICTED", {"target_id": target_id, "tenant_id": tenant_id})

        return evicted

    async def prune_async(self, tenant_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.auto_prune_store, tenant_id=tenant_id)
