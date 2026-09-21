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
        packed = self.packer.pack(self.memories)
        packed_ids = [m["id"] if isinstance(m, dict) else m for m in packed]
        total_cost = sum(m["token_cost"] for m in self.memories if m["id"] in packed_ids)
        self.assertLessEqual(total_cost, 100)

    def test_budget_safety(self):
        packer = KnapsackPacker(token_budget=10)
        packed = packer.pack(self.memories)
        packed_ids = [m["id"] if isinstance(m, dict) else m for m in packed]
        total_cost = sum(m["token_cost"] for m in self.memories if m["id"] in packed_ids)
        self.assertLessEqual(total_cost, 10)


if __name__ == '__main__':
    unittest.main()

