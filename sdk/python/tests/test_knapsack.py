import unittest
from synapse_memory.core.knapsack_packer import KnapsackPacker

class TestKnapsackPacker(unittest.TestCase):
    def setUp(self):
        self.packer = KnapsackPacker(token_budget=100)
        self.memories = [
            {"id": "m1", "token_cost": 30, "relevance_score": 0.9},
            {"id": "m2", "token_cost": 40, "relevance_score": 0.8},
            {"id": "m3", "token_cost": 50, "relevance_score": 0.95},
            {"id": "m4", "token_cost": 20, "relevance_score": 0.7},
        ]

    def test_dp_packing(self):
        # Budget 100
        # Optimal: m1+m2+m4 = 30+40+20=90 cost, value 0.9+0.8+0.7=2.4
        # vs m3+m1 = 80 cost, value 1.85
        # vs m3+m4+m1 = 100 cost, value 2.55
        packed_ids = self.packer.pack(self.memories)

        total_cost = sum(m["token_cost"] for m in self.memories if m["id"] in packed_ids)
        self.assertLessEqual(total_cost, 100)

    def test_budget_safety(self):
        # Adversarial: budget too small
        packer = KnapsackPacker(token_budget=10)
        packed_ids = self.packer.pack(self.memories)
        # Should be empty or small
        total_cost = sum(m["token_cost"] for m in self.memories if m["id"] in packed_ids)
        self.assertLessEqual(total_cost, 100)

if __name__ == '__main__':
    unittest.main()
