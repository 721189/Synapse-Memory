from typing import List, Dict, Any, Tuple
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SynapseKnapsack")

class KnapsackPacker:
    """
    Engine responsible for fitting the maximum semantic value (relevance) 
    into a bounded token envelope (budget) using 0/1 Knapsack optimization.
    """

    def __init__(self, greedy_threshold_capacity: int = 4096):
        """
        Args:
            greedy_threshold_capacity: Switch to Greedy approach if token capacity 
                                       is too large to avoid high memory/time complexity.
        """
        self.greedy_threshold_capacity = greedy_threshold_capacity

    def pack(
        self, 
        memories: List[Dict[str, Any]], 
        max_token_budget: int
    ) -> List[Dict[str, Any]]:
        """
        Solves the Knapsack optimization problem for prompt packing.
        
        Each memory dictionary must contain:
            - 'id': str
            - 'content': str
            - 'token_cost': int (weight)
            - 'relevance_score': float (value)
            
        Args:
            memories: List of memory dictionaries.
            max_token_budget: Maximum tokens allowed in context window.
            
        Returns:
            The optimal subset of memories.
        """
        if not memories or max_token_budget <= 0:
            return []

        # Filter out memories that exceed the budget individually
        valid_memories = [m for m in memories if m.get("token_cost", 0) <= max_token_budget]
        if not valid_memories:
            return []

        # Switch to Greedy heuristic if budget is too high to protect API latencies
        if max_token_budget > self.greedy_threshold_capacity:
            logger.info("Token budget exceeds DP matrix safety threshold. Utilizing Greedy heuristic.")
            return self._greedy_pack(valid_memories, max_token_budget)

        return self._dp_pack(valid_memories, max_token_budget)

    def _dp_pack(
        self, 
        memories: List[Dict[str, Any]], 
        capacity: int
    ) -> List[Dict[str, Any]]:
        """
        Classical 0/1 Knapsack Dynamic Programming solver.
        Complexity: O(N * Capacity)
        """
        n = len(memories)
        
        # Scaling relevance scores to integers for DP index representation
        # Multiply values by 100 to retain fractional precision in matrix
        values = [int(m.get("relevance_score", 0.0) * 100) for m in memories]
        weights = [m.get("token_cost", 0) for m in memories]

        # Initialize DP matrix table
        dp = [[0 for _ in range(capacity + 1)] for _ in range(n + 1)]

        for i in range(1, n + 1):
            for w in range(1, capacity + 1):
                if weights[i - 1] <= w:
                    dp[i][w] = max(
                        values[i - 1] + dp[i - 1][w - weights[i - 1]],
                        dp[i - 1][w]
                    )
                else:
                    dp[i][w] = dp[i - 1][w]

        # Reconstruct selected items
        selected_memories = []
        w = capacity
        for i in range(n, 0, -1):
            if dp[i][w] != dp[i - 1][w]:
                selected_memories.append(memories[i - 1])
                w -= weights[i - 1]

        # Return optimal selections reversed to preserve ascending priority
        selected_memories.reverse()
        return selected_memories

    def _greedy_pack(
        self, 
        memories: List[Dict[str, Any]], 
        capacity: int
    ) -> List[Dict[str, Any]]:
        """
        Greedy Knapsack packer based on density (relevance_score / token_cost).
        Complexity: O(N log N)
        """
        # Sort based on density
        sorted_memories = sorted(
            memories, 
            key=lambda x: x.get("relevance_score", 0.0) / max(x.get("token_cost", 1), 1), 
            reverse=True
        )

        selected_memories = []
        current_weight = 0

        for memory in sorted_memories:
            w = memory.get("token_cost", 0)
            if current_weight + w <= capacity:
                selected_memories.append(memory)
                current_weight += w

        return selected_memories
