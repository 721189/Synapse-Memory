import unittest
import time
from synapse_memory.core.decay_engine import DecayEngine

class TestDecayEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DecayEngine({
            'interaction': {'base_strength': 86400.0, 'reinforcement_factor': 0.5}
        })
        self.memory = {
            "id": "m1",
            "category": "interaction",
            "confidence": 1.0,
            "created_at": time.time() - 1000,
            "last_accessed_at": time.time() - 1000,
            "access_count": 0,
            "feedback_multiplier": 1.0
        }

    def test_relevance_decay(self):
        # Relevance at now should be near 1.0
        score_now = self.engine.calculate_relevance(self.memory, now=time.time())
        # Relevance in the future should be lower
        score_future = self.engine.calculate_relevance(self.memory, now=time.time() + 86400)
        self.assertLess(score_future, score_now)

    def test_reinforcement(self):
        # Calculate initial score
        now = time.time()
        score_initial = self.engine.calculate_relevance(self.memory, now=now)

        # Reinforce
        reinforced_memory = self.engine.reinforce(self.memory, feedback_value=1.0)

        # Calculate new score - should be higher due to increased strength
        score_reinforced = self.engine.calculate_relevance(reinforced_memory, now=now)

        self.assertGreater(score_reinforced, score_initial)

    def test_negative_feedback(self):
        # Reinforce negatively
        neg_memory = self.engine.reinforce(self.memory, feedback_value=-1.0)

        self.assertLess(neg_memory["feedback_multiplier"], 1.0)

if __name__ == '__main__':
    unittest.main()
