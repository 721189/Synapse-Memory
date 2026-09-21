import time
import logging
import math
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.deduplicator import Deduplicator, Action
from synapse_memory.core.pruner import PruningEngine, CapacityPruningPolicy
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.observability import log_event, instrument_operation

logger = logging.getLogger("SynapseMemoryManager")

class MemoryManager:
    """
    Manages high-level memory operations: Semantic Deduplication, 
    Auto-Eviction, and Cognitive Memory Pruning.
    """

    def __init__(
        self, 
        store: SQLiteMemoryStore, 
        embedder: SynapseEmbedder,
        category_thresholds: Dict[str, float] = None,
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
        confidence: float = 0.95
    ) -> Tuple[str, str, int]:
        """
        Ingests a new memory node.
        Runs semantic similarity comparison against all existing indexed nodes using Deduplicator.
        """
        
        # 1. Generate real vector representation
        new_vector = self.embedder.embed_query(content)
        existing_memories = self.store.get_memories_by_category(category)

        # 2. Check for duplicate nodes
        new_node_base = {
            "content": content,
            "category": category,
            "embedding": new_vector
        }
        
        result = self.deduplicator.check_duplicate(new_node_base, existing_memories)

        # 3. Handle Deduplication Result
        if result.action == Action.MERGE and result.target_id:
            match_id = result.target_id
            log_event("memory_deduplication", "MERGED", {"target_id": match_id, "reason": result.reason})
            
            # Reinforce: Boost confidence, update timestamp, increment access count
            self.store.update_access_count(match_id, 1)
            self.store.update_confidence(match_id, min(1.0, confidence + 0.05))
            
            # Re-fetch for token cost
            match_mem = next(m for m in existing_memories if m["id"] == match_id)
            
            return match_id, "MERGED", match_mem["token_cost"]

        elif result.action == Action.REJECT:
            log_event("memory_deduplication", "REJECTED", {"reason": result.reason})
            return "REJECTED", "REJECTED", 0

        # 4. Otherwise, save as a clean memory
        log_event("memory_deduplication", "CREATED", {})
        memory_id = f"mem_{int(time.time() * 1000)}"
        token_cost = max(15, int(len(content.split()) * 1.35) + 10)

        new_node = {
            "id": memory_id,
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
        self.auto_prune_store()
        
        return memory_id, "CREATED", token_cost

    async def ingest_async(self, *args, **kwargs) -> Tuple[str, str, int]:
        return await asyncio.to_thread(self.ingest_with_deduplication, *args, **kwargs)

    def auto_prune_store(self) -> int:
        """
        Evicts memories based on the configured PruningEngine.
        
        Returns:
            The number of successfully evicted memory nodes.
        """
        memories = self.store.get_all_memories()
        targets = self.pruner.run_pruning(memories)
        
        evicted = 0
        for target_id in targets:
            self.store.delete_memory(target_id)
            evicted += 1
            log_event("memory_pruning", "EVICTED", {"target_id": target_id})
            
        return evicted

    async def prune_async(self) -> int:
        return await asyncio.to_thread(self.auto_prune_store)
