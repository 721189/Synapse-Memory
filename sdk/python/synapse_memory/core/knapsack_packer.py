from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger("KnapsackPacker")


class MemoryNodeResult(dict):
    """
    Dual-contract result node that acts both as a dictionary with keys
    ('id', 'content', etc.) and equals its string ID for backwards-compatible
    membership checks.
    """
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.get("id") == other
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.get("id", ""))


class KnapsackPacker:
    """
    Robust 0/1 Knapsack optimizer for packing memory nodes into a token budget.
    Implements a memory-efficient Dynamic Programming solution with a greedy fallback.
    """
    def __init__(self, token_budget: int = 4096):
        self.token_budget = token_budget

    def pack(
        self,
        memories: List[Dict[str, Any]],
        token_budget: Optional[int] = None
    ) -> List[MemoryNodeResult]:
        """
        Packs memories into the token budget using a DP approach.

        Args:
            memories: List of memory nodes with 'id', 'token_cost', and 'relevance_score'.
            token_budget: Optional override for budget. If None, uses self.token_budget.

        Returns:
            List of packed memory nodes that maximize total relevance score within budget.
        """
        budget = token_budget if token_budget is not None else self.token_budget
        if not memories or budget <= 0:
            return []

        n = len(memories)
        weights = [max(1, int(m.get("token_cost", 1))) for m in memories]
        values = [max(0, int(float(m.get("relevance_score", 0.0)) * 1000)) for m in memories]

        dp = [0] * (budget + 1)
        items_included: List[List[int]] = [[] for _ in range(budget + 1)]

        for i in range(n):
            weight = weights[i]
            value = values[i]
            if weight > budget:
                continue
            for w in range(budget, weight - 1, -1):
                if dp[w - weight] + value > dp[w]:
                    dp[w] = dp[w - weight] + value
                    items_included[w] = items_included[w - weight] + [i]

        selected_indices = items_included[budget]
        return [MemoryNodeResult(memories[idx]) for idx in selected_indices]

    def greedy_pack(
        self,
        memories: List[Dict[str, Any]],
        token_budget: Optional[int] = None
    ) -> List[MemoryNodeResult]:
        """Greedy fallback: Pack by highest relevance/cost ratio."""
        budget = token_budget if token_budget is not None else self.token_budget
        sorted_memories = sorted(
            memories,
            key=lambda m: float(m.get("relevance_score", 0.0)) / max(1, m.get("token_cost", 1)),
            reverse=True
        )

        selected: List[MemoryNodeResult] = []
        current_cost = 0
        for m in sorted_memories:
            cost = m.get("token_cost", 1)
            if current_cost + cost <= budget:
                selected.append(MemoryNodeResult(m))
                current_cost += cost
        return selected
