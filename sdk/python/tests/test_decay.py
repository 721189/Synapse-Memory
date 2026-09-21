import unittest
import time
from synapse_memory.core.decay_engine import DecayEngine


class TestDecayEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DecayEngine({
            'interaction': {'base_strength': 86400.0, 'reinforcement_factor': 0.5}
        })
        self.base_time = 1700000000.0
        self.memory = {
            "id": "m1",
            "category": "interaction",
            "confidence": 1.0,
            "created_at": self.base_time - 1000,
            "last_accessed_at": self.base_time - 1000,
            "access_count": 0,
            "feedback_multiplier": 1.0
        }

    def test_relevance_decay(self):
        # Relevance at base time should be higher than in the future
        score_now = self.engine.calculate_relevance(self.memory, now=self.base_time)
        score_future = self.engine.calculate_relevance(self.memory, now=self.base_time + 86400)
        self.assertLess(score_future, score_now)

    def test_reinforcement(self):
        eval_time = self.base_time + 500
        score_initial = self.engine.calculate_relevance(self.memory, now=eval_time)

        # Reinforce
        reinforced_memory = self.engine.reinforce(self.memory, feedback_value=1.0)
        reinforced_memory["last_accessed_at"] = self.base_time - 1000  # Keep age fixed to isolate strength effect

        score_reinforced = self.engine.calculate_relevance(reinforced_memory, now=eval_time)
        self.assertGreater(score_reinforced, score_initial)

    def test_negative_feedback(self):
        neg_memory = self.engine.reinforce(self.memory, feedback_value=-1.0)
        self.assertLess(neg_memory["feedback_multiplier"], 1.0)


if __name__ == '__main__':
    unittest.main()

