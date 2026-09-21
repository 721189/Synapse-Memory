import unittest
from synapse_memory.core.deduplicator import Deduplicator, Action


class TestDeduplicator(unittest.TestCase):
    def setUp(self):
        self.deduplicator = Deduplicator({"interaction": 0.8, "infrastructure": 0.9}, default_threshold=0.85)

    def test_exact_match(self):
        existing = [{"id": "mem_1", "content": "hello", "category": "interaction", "embedding": [0.1, 0.2]}]
        new = {"id": "mem_1", "content": "hello", "category": "interaction", "embedding": [0.1, 0.2]}
        result = self.deduplicator.check_duplicate(new, existing)
        self.assertEqual(result.action, Action.REJECT)

    def test_semantic_match(self):
        existing = [{"id": "mem_1", "content": "AWS configuration", "category": "infrastructure", "embedding": [1.0, 0.0]}]
        # Very similar vector
        new = {"id": "mem_2", "content": "AWS settings", "category": "infrastructure", "embedding": [0.99, 0.01]}
        result = self.deduplicator.check_duplicate(new, existing)
        self.assertEqual(result.action, Action.MERGE)

    def test_distinct_memory(self):
        existing = [{"id": "mem_1", "content": "hello", "category": "interaction", "embedding": [1.0, 0.0]}]
        new = {"id": "mem_2", "content": "world", "category": "interaction", "embedding": [0.0, 1.0]}
        result = self.deduplicator.check_duplicate(new, existing)
        self.assertEqual(result.action, Action.CREATE)

    def test_contradiction(self):
        existing = [{"id": "mem_1", "content": "System status is true", "category": "interaction", "embedding": [0.1, 0.2]}]
        new = {"id": "mem_2", "content": "System status is false", "category": "interaction", "embedding": [0.1, 0.2]}
        result = self.deduplicator.check_duplicate(new, existing)
        self.assertEqual(result.action, Action.REJECT)


if __name__ == '__main__':
    unittest.main()

