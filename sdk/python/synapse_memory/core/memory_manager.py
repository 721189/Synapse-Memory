import time
import logging
from typing import List, Dict, Any, Tuple, Optional
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder

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
        similarity_threshold: float = 0.85,
        max_record_limit: int = 50
    ):
        self.store = store
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self.max_record_limit = max_record_limit

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Computes true mathematical cosine similarity between two float vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def ingest_with_deduplication(
        self, 
        content: str, 
        category: str = "interaction", 
        confidence: float = 0.95
    ) -> Tuple[str, str, int]:
        """
        Ingests a new memory node.
        Runs semantic similarity comparison against all existing indexed nodes.
        If similarity > similarity_threshold, merges and reinforces the existing record.
        Otherwise, inserts a clean record.
        
        Returns:
            Tuple: (memory_id, action_status ("CREATED" | "MERGED"), token_cost)
        """
        import math # local import safe
        
        # 1. Generate real vector representation
        new_vector = self.embedder.embed_query(content)
        existing_memories = self.store.get_all_memories()

        # 2. Check for duplicate nodes
        best_similarity = -1.0
        best_match_mem: Optional[Dict[str, Any]] = None

        for existing in existing_memories:
            existing_vec = existing.get("embedding", [])
            if existing_vec:
                # Compute vector distance
                dot_product = sum(a * b for a, b in zip(new_vector, existing_vec))
                norm_a = math.sqrt(sum(a * a for a in new_vector))
                norm_b = math.sqrt(sum(b * b for b in existing_vec))
                similarity = dot_product / (norm_a * norm_b) if norm_a > 0 and norm_b > 0 else 0.0

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match_mem = existing

        # 3. Deduplicate / Update Loop
        if best_match_mem and best_similarity >= self.similarity_threshold:
            match_id = best_match_mem["id"]
            logger.info(f"Semantic match found (Similarity: {best_similarity:.3f}). Merging into node: {match_id}")
            
            # Reinforce: Boost confidence, update timestamp, increment access count
            boosted_confidence = min(1.0, best_match_mem["confidence"] + 0.05)
            
            # Save reinforcement details
            self.store.update_access_count(match_id, 1)
            self.store.update_confidence(match_id, boosted_confidence)
            
            # Return match parameters
            return match_id, "MERGED", best_match_mem["token_cost"]

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
            decay_relevance = m["confidence"] * (2.718 ** (-elapsed / strength))
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
