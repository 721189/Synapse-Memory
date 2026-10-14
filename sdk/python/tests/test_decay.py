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

    def test_combine_scores_preserves_relevance_ordering(self):
        # P0-4: S(m,q,t) = R(m,q) * D(m,t). An old but highly relevant
        # memory must still outrank a new but irrelevant one.
        now = self.base_time
        old_relevant = dict(self.memory)
        old_relevant["last_accessed_at"] = now - 30 * 86400
        new_irrelevant = dict(self.memory)
        new_irrelevant["last_accessed_at"] = now
        old_score = self.engine.combine_scores(0.95, old_relevant, now=now)
        new_score = self.engine.combine_scores(0.05, new_irrelevant, now=now)
        self.assertGreater(old_score, new_score)

    def test_temporal_multiplier_bounded(self):
        mult = self.engine.temporal_multiplier(self.memory, now=self.base_time)
        self.assertGreaterEqual(mult, self.engine.decay_floor)
        self.assertLessEqual(mult, 1.0)

    def test_combine_scores_scales_monotonically(self):
        low = self.engine.combine_scores(0.2, self.memory, now=self.base_time)
        high = self.engine.combine_scores(0.9, self.memory, now=self.base_time)
        self.assertLess(low, high)
        self.assertAlmostEqual(high / 0.9, low / 0.2, places=9)


if __name__ == '__main__':
    unittest.main()

