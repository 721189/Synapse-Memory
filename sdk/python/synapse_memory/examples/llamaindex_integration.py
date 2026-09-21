from typing import Dict, Any, List, Optional
import time

class SynapseLlamaIndexMemory:
    """
    Drop-in LlamaIndex-compatible Memory Module.
    Injects context-capped dynamic memory nodes directly into index query streams.
    """

    def __init__(self, max_token_limit: int = 2048):
        self.max_token_limit = max_token_limit
        self.memories: List[Dict[str, Any]] = []

    def get(self, input_str: str) -> str:
        """Retrieves and packs relevant semantic nodes for LlamaIndex query streams."""
        if not input_str or not self.memories:
            return ""

        scored_nodes = []
        query_set = set(input_str.lower().split())

        for node in self.memories:
            # Simple keyword matching and decay weight calculation
            node_set = set(node["content"].lower().split())
            intersection = query_set.intersection(node_set)
            jaccard = len(intersection) / max(1, len(query_set.union(node_set)))

            elapsed = time.time() - node["created_at"]
            decay = max(0.15, 2.718 ** (-elapsed / (86400.0 * (1.0 + node["access_count"] * 0.4))))

            node_copy = node.copy()
            node_copy["relevance_score"] = jaccard * 0.6 * decay
            scored_nodes.append(node_copy)

        # Pack optimal subset
        scored_nodes.sort(key=lambda x: x["relevance_score"], reverse=True)
        packed_content = []
        current_tokens = 0

        for m in scored_nodes:
            if current_tokens + m["token_cost"] <= self.max_token_limit:
                packed_content.append(m["content"])
                current_tokens += m["token_cost"]
                m["access_count"] += 1

        return "\n---\n".join(packed_content)

    def put(self, content: str, category: str = "kb") -> None:
        """Stores a new knowledge node into the indexing stream."""
        if not content.strip():
            return

        token_cost = int(len(content.split()) * 1.3) + 12
        new_node = {
            "id": f"node_{int(time.time() * 1000)}",
            "content": content,
            "category": category,
            "confidence": 0.98,
            "created_at": time.time(),
            "access_count": 0,
            "token_cost": max(20, token_cost)
        }
        self.memories.append(new_node)

    def reset(self) -> None:
        """Flushes cognitive states."""
        self.memories.clear()
