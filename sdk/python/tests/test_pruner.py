import time
import unittest
from synapse_memory.core.pruner import PruningEngine, TTLPruningPolicy, ConfidencePruningPolicy, CapacityPruningPolicy
from synapse_memory.core.decay_engine import DecayEngine


class TestPruner(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.memories = [
            {"id": "m1", "created_at": self.now - 100, "confidence": 0.9, "last_accessed_at": self.now - 100},
            {"id": "m2", "created_at": self.now - 200, "confidence": 0.5, "last_accessed_at": self.now - 200},
            {"id": "m3", "created_at": self.now - 500, "confidence": 0.8, "last_accessed_at": self.now - 500}
        ]
        self.decay_engine = DecayEngine({})

    def test_ttl_policy(self):
        # Prune memories older than 150 seconds
        policy = TTLPruningPolicy(ttl_seconds=150)
        targets = policy.identify_targets(self.memories)
        self.assertIn("m2", targets)
        self.assertIn("m3", targets)
        self.assertNotIn("m1", targets)

    def test_confidence_policy(self):
        # Prune memories below 0.7 confidence
        policy = ConfidencePruningPolicy(confidence_threshold=0.7)
        targets = policy.identify_targets(self.memories)
        self.assertIn("m2", targets)
        self.assertNotIn("m1", targets)
        self.assertNotIn("m3", targets)

    def test_capacity_policy(self):
        # Prune down to 2 memories
        policy = CapacityPruningPolicy(max_records=2, decay_engine=self.decay_engine)
        targets = policy.identify_targets(self.memories)
        self.assertEqual(len(targets), 1)

    def test_pruning_engine(self):
        engine = PruningEngine([
            TTLPruningPolicy(ttl_seconds=150),
            ConfidencePruningPolicy(confidence_threshold=0.6)
        ])
        targets = engine.run_pruning(self.memories)
        # m2 (old and low confidence), m3 (old)
        self.assertIn("m2", targets)
        self.assertIn("m3", targets)


if __name__ == '__main__':
    unittest.main()

