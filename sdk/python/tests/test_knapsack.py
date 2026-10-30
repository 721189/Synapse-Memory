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

    def test_reserved_context_is_subtracted_before_memory_selection(self):
        packed = self.packer.pack(self.memories, token_budget=100, reserved_token_cost=60)
        total_memory_cost = sum(int(memory["token_cost"]) for memory in packed)
        self.assertLessEqual(total_memory_cost, 40)

    def test_reservation_can_consume_entire_budget_without_dropping_reserved_context(self):
        packed = self.packer.pack(self.memories, token_budget=100, reserved_token_cost=100)
        self.assertEqual(packed, [])

    def test_reservation_over_budget_fails_even_when_there_are_no_memories(self):
        with self.assertRaisesRegex(ValueError, "Protected context requires"):
            self.packer.pack([], token_budget=50, reserved_token_cost=51)

    def test_invalid_reservation_is_rejected(self):
        for value in (-1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "reserved_token_cost"):
                    self.packer.pack(self.memories, token_budget=100, reserved_token_cost=value)

    def test_greedy_fallback_uses_only_the_unreserved_budget(self):
        packer = KnapsackPacker(token_budget=100)
        packer.DP_OPS_THRESHOLD = 1
        packed = packer.pack(self.memories, token_budget=100, reserved_token_cost=60)
        self.assertLessEqual(sum(int(memory["token_cost"]) for memory in packed), 40)


if __name__ == '__main__':
    unittest.main()

