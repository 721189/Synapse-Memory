import time
import logging
import math
from typing import List, Dict, Any, Tuple, Optional
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.deduplicator import Deduplicator, Action

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
        existing_memories = self.store.get_all_memories()

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
            logger.info(f"Deduplication: {result.reason}. Merging into node: {match_id}")
            
            # Reinforce: Boost confidence, update timestamp, increment access count
            # Note: Best match memory record needs to be fetched for its token cost
            self.store.update_access_count(match_id, 1)
            # Need a way to fetch the best match again to update confidence properly
            # For now, simplistic update.
            self.store.update_confidence(match_id, min(1.0, confidence + 0.05))
            
            # Re-fetch for token cost
            match_mem = next(m for m in existing_memories if m["id"] == match_id)
            
            return match_id, "MERGED", match_mem["token_cost"]

        elif result.action == Action.REJECT:
            logger.info(f"Deduplication: Rejected insertion. Reason: {result.reason}")
            return "REJECTED", "REJECTED", 0

        # 4. Otherwise, save as a clean memory
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

    def auto_prune_store(self) -> int:
        """
        Evicts memories if the total database records exceed max_record_limit.
        Calculates Ebbinghaus temporal decay relevances and prunes the lowest scores first.
        
        Returns:
            The number of successfully evicted memory nodes.
        """
        memories = self.store.get_all_memories()
        if len(memories) <= self.max_record_limit:
            return 0

        # Calculate decay weight scores
        scored_memories = []
        now = time.time()
        for m in memories:
            elapsed = now - m["created_at"]
            strength = 86400.0 * (1.0 + m["access_count"] * 0.45)
            # Ebbinghaus curve retention factor
            decay_relevance = m["confidence"] * (math.exp(-elapsed / strength))
            scored_memories.append((decay_relevance, m["id"]))

        # Sort ascending (lowest relevance scores first)
        scored_memories.sort(key=lambda x: x[0])
        
        # Erase extra items
        excess_count = len(memories) - self.max_record_limit
        evicted = 0
        for i in range(excess_count):
            _, target_id = scored_memories[i]
            self.store.delete_memory(target_id)
            evicted += 1
            logger.info(f"Database overflow. Pruned decayed cognitive node: {target_id}")

        return evicted
