from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger("KnapsackPacker")

class KnapsackPacker:
    """
    Robust 0/1 Knapsack optimizer for packing memory nodes into a token budget.
    Implements a memory-efficient Dynamic Programming solution with a greedy fallback.
    """
    def __init__(self, token_budget: int):
        self.token_budget = token_budget

    def pack(self, memories: List[Dict[str, Any]]) -> List[str]:
        """
        Packs memories into the token budget using a DP approach.
        
        Args:
            memories: List of memory nodes with 'id', 'token_cost', and 'relevance_score'.
            
        Returns:
            List of memory IDs that maximize total relevance score within budget.
        """
        if not memories:
            return []

        # Convert relevance score to integer weight for DP (scaled)
        # We need integer weights for DP, so we scale by 1000 and round
        n = len(memories)
        weights = [m["token_cost"] for m in memories]
        values = [int(m.get("relevance_score", 0.0) * 1000) for m in memories]
        
        # DP table: dp[i][w] is max value with first i items and weight limit w
        # Using 1D DP array optimization to save memory: O(budget) space
        dp = [0] * (self.token_budget + 1)
        # To reconstruct the solution, keep track of items
        # items_included[w] = list of indices
        items_included = [[] for _ in range(self.token_budget + 1)]

        for i in range(n):
            weight = weights[i]
            value = values[i]
            for w in range(self.token_budget, weight - 1, -1):
                if dp[w - weight] + value > dp[w]:
                    dp[w] = dp[w - weight] + value
                    items_included[w] = items_included[w - weight] + [i]

        selected_indices = items_included[self.token_budget]
        return [memories[idx]["id"] for idx in selected_indices]

    def greedy_pack(self, memories: List[Dict[str, Any]]) -> List[str]:
        """Greedy fallback: Pack by highest relevance/cost ratio."""
        # Sort by relevance/cost ratio descending
        sorted_memories = sorted(
            memories, 
            key=lambda m: m.get("relevance_score", 0.0) / max(1, m["token_cost"]), 
            reverse=True
        )
        
        selected = []
        current_cost = 0
        for m in sorted_memories:
            if current_cost + m["token_cost"] <= self.token_budget:
                selected.append(m["id"])
                current_cost += m["token_cost"]
        return selected
