"""Regression tests for score precision in token-budget optimization."""
import math
import unittest

from synapse_memory.core.knapsack_packer import KnapsackPacker


class TestKnapsackScorePrecision(unittest.TestCase):
    def _items(self, scale=1.0):
        # A single 10-token item scores 0.006. Two 5-token facts score 0.00349
        # each, so their combined utility (0.00698) is greater than the single
        # item. Integer quantization at *1000 turned these into 6 vs 3+3,
        # incorrectly preserving the first item because DP replaces only on >.
        return [
            {"id": "single", "token_cost": 10, "relevance_score": 0.0060 * scale},
            {"id": "fact_a", "token_cost": 5, "relevance_score": 0.00349 * scale},
            {"id": "fact_b", "token_cost": 5, "relevance_score": 0.00349 * scale},
        ]

    def test_dp_preserves_small_rrf_score_differences(self):
        packed = KnapsackPacker(token_budget=10).pack(self._items(), token_budget=10)
        self.assertEqual({item["id"] for item in packed}, {"fact_a", "fact_b"})
        self.assertEqual(sum(item["token_cost"] for item in packed), 10)

    def test_dp_selection_is_invariant_to_positive_score_scaling(self):
        selected = []
        for scale in (1.0, 10.0, 1000.0):
            packed = KnapsackPacker(token_budget=10).pack(
                self._items(scale=scale), token_budget=10
            )
            selected.append({item["id"] for item in packed})
        self.assertEqual(selected, [{"fact_a", "fact_b"}] * 3)

    def test_non_finite_scores_do_not_break_dp_or_beat_valid_scores(self):
        memories = [
            {"id": "nan", "token_cost": 2, "relevance_score": math.nan},
            {"id": "positive_inf", "token_cost": 2, "relevance_score": math.inf},
            {"id": "negative_inf", "token_cost": 2, "relevance_score": -math.inf},
            {"id": "useful", "token_cost": 5, "relevance_score": 0.25},
        ]
        packed = KnapsackPacker(token_budget=5).pack(memories, token_budget=5)
        self.assertEqual([item["id"] for item in packed], ["useful"])

    def test_non_finite_only_scores_return_no_items(self):
        memories = [
            {"id": "nan", "token_cost": 2, "relevance_score": math.nan},
            {"id": "positive_inf", "token_cost": 2, "relevance_score": math.inf},
            {"id": "negative_inf", "token_cost": 2, "relevance_score": -math.inf},
        ]
        packed = KnapsackPacker(token_budget=5).pack(memories, token_budget=5)
        self.assertEqual(packed, [])


if __name__ == "__main__":
    unittest.main()
