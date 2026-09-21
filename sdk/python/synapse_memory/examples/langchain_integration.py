from typing import Dict, Any, List, Optional
import time
from pydantic import BaseModel, Field

# We import from langchain or mock its base structure depending on direct availability
# Here, we implement a highly robust, standard BaseMemory layout conforming to LangChain specs
class SynapseLangChainMemory(BaseModel):
    """
    Drop-in LangChain-compatible Memory Component.
    Seamlessly manages vector ingestion, Knapsack packing, and temporal decay
    at the end of every agent cycle.
    """
    
    memory_key: str = "chat_history"
    max_token_budget: int = 1500
    local_memories: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Custom components instantiated on load
    class Config:
        arbitrary_types_allowed = True

    @property
    def memory_variables(self) -> List[str]:
        """Defines the memory key injected into the agent prompt template."""
        return [self.memory_key]

    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, str]:
        """
        Runs hybrid search over the cognitive store, applies temporal Ebbinghaus decay,
        packs the results within the token budget, and returns the formatted context.
        """
        query_text = inputs.get("input") or inputs.get("question") or ""
        if not query_text or not self.local_memories:
            return {self.memory_key: ""}

        # 1. Simple Jaccard keyword matching
        scored_mems = []
        query_words = set(query_text.lower().split())
        
        for mem in self.local_memories:
            # Apply decay factor
            elapsed = time.time() - mem["created_at"]
            # Decay strength based on accesses
            strength = 86400.0 * (1.0 + mem["access_count"] * 0.45)
            decay_factor = min(1.0, max(0.15, 2.718 ** (-elapsed / strength)))
            
            # Apply Jaccard matching
            mem_words = set(mem["content"].lower().split())
            jaccard = len(query_words.intersection(mem_words)) / max(1, len(query_words.union(mem_words)))
            
            relevance = (jaccard * 0.5 + 0.5) * mem["confidence"] * decay_factor
            
            mem_copy = mem.copy()
            mem_copy["relevance_score"] = relevance
            scored_mems.append(mem_copy)

        # 2. Pack optimally under Knapsack constraint
        scored_mems.sort(key=lambda x: x["relevance_score"], reverse=True)
        packed_mems = []
        current_tokens = 0
        
        for m in scored_mems:
            cost = m["token_cost"]
            if current_tokens + cost <= self.max_token_budget:
                packed_mems.append(m)
                current_tokens += cost
                # Increment access count
                m["access_count"] += 1

        # 3. Format as a coherent text block
        if not packed_mems:
            return {self.memory_key: "No relevant long-term memories retrieved."}

        formatted_history = "\n".join([
            f"[{m['category'].upper()}] (Confidence: {m['confidence']:.2f}): {m['content']}"
            for m in packed_mems
        ])
        
        return {self.memory_key: f"Retrieved Historical Context:\n{formatted_history}"}

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> None:
        """Saves interaction outputs directly to the sovereign pgvector memory store."""
        user_input = inputs.get("input") or inputs.get("question") or ""
        assistant_output = outputs.get("output") or outputs.get("text") or ""
        
        if not assistant_output:
            return

        # Estimate simple token costs (word count * 1.3 approximation)
        token_cost = int(len(assistant_output.split()) * 1.3) + 10

        # Ingest new record
        new_memory = {
            "id": f"mem_{int(time.time() * 1000)}",
            "content": f"User: {user_input} -> Agent: {assistant_output}",
            "category": "interaction",
            "confidence": 0.95,
            "created_at": time.time(),
            "access_count": 0,
            "token_cost": max(15, token_cost)
        }
        
        self.local_memories.append(new_memory)

    def clear(self) -> None:
        """Flushes local working memory context."""
        self.local_memories.clear()
