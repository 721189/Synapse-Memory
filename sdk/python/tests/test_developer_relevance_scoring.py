import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "benchmark" / "score_developer_relevance_reviews.py"
SPEC = importlib.util.spec_from_file_location("score_developer_relevance_reviews", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def fixtures(disagree=False):
    packet = {
        "label_status": "reviewed",
        "cases": [{
            "case_id": "CASE-1",
            "candidates": [
                {
                    "candidate_id": "CAND-001",
                    "reviewer_a": {"relevance": 2, "notes": ""},
                    "reviewer_b": {"relevance": 1 if disagree else 2, "notes": ""},
                    **({"adjudicated_relevance": 2, "adjudication_note": "Direct evidence answers the query."}
                       if disagree else {}),
                },
                {
                    "candidate_id": "CAND-002",
                    "reviewer_a": {"relevance": 0, "notes": ""},
                    "reviewer_b": {"relevance": 0, "notes": ""},
                },
                {
                    "candidate_id": "CAND-003",
                    "reviewer_a": {"relevance": 2, "notes": ""},
                    "reviewer_b": {"relevance": 2, "notes": ""},
                },
            ],
        }],
    }
    mapping = [
        {"case_id": "CASE-1", "candidate_id": "CAND-001", "query_id": "q1", "memory_id": "m1"},
        {"case_id": "CASE-1", "candidate_id": "CAND-002", "query_id": "q1", "memory_id": "m2"},
        {"case_id": "CASE-1", "candidate_id": "CAND-003", "query_id": "q1", "memory_id": "m3"},
    ]
    result = {
        "environment": {"embedding_provider": "test-provider"},
        "per_query": {
            "q1": {
                "ranking": {
                    "lexical_bm25": {"top_k_ids": ["m1", "m2", "m3"]},
                    "dense_cosine": {"top_k_ids": ["m2", "m1", "m3"]},
                    "hybrid_rrf": {"top_k_ids": ["m3", "m1", "m2"]},
                }
            }
        },
    }
    return packet, mapping, result


class DeveloperRelevanceScoringTests(unittest.TestCase):
    def test_scores_rankings_and_reports_pool_limited_scope(self):
        packet, mapping, result = fixtures()
        scored = MODULE.score_reviews(packet, mapping, [("fixture.json", result)])
        self.assertEqual(scored["label_status"], "adjudicated")
        self.assertEqual(scored["reviewed_candidate_count"], 3)
        self.assertEqual(scored["judgments"]["exact_agreement_rate"], 1.0)
        self.assertIn("recall within the reviewed candidate pool", " ".join(scored["limitations"]))
        hybrid = scored["ranking_metrics"]["test-provider / hybrid_rrf"]
        self.assertEqual(hybrid["mean_ndcg_at_5"], 1.0)
        self.assertEqual(hybrid["mean_mrr_at_5_direct_useful"], 1.0)

    def test_disagreement_requires_adjudication(self):
        packet, mapping, result = fixtures(disagree=True)
        del packet["cases"][0]["candidates"][0]["adjudicated_relevance"]
        del packet["cases"][0]["candidates"][0]["adjudication_note"]
        with self.assertRaisesRegex(ValueError, "Unresolved reviewer disagreement"):
            MODULE.score_reviews(packet, mapping, [("fixture.json", result)])

    def test_adjudication_is_reported_and_requires_note(self):
        packet, mapping, result = fixtures(disagree=True)
        del packet["cases"][0]["candidates"][0]["adjudication_note"]
        with self.assertRaisesRegex(ValueError, "adjudication_note"):
            MODULE.score_reviews(packet, mapping, [("fixture.json", result)])
        packet, mapping, result = fixtures(disagree=True)
        scored = MODULE.score_reviews(packet, mapping, [("fixture.json", result)])
        self.assertEqual(scored["judgments"]["reviewer_disagreement_count"], 1)
        self.assertAlmostEqual(scored["judgments"]["reviewer_disagreement_rate"], 1 / 3, places=6)

    def test_invalid_grades_and_unjudged_ranked_candidates_fail_closed(self):
        packet, mapping, result = fixtures()
        packet["cases"][0]["candidates"][0]["reviewer_a"]["relevance"] = 3
        with self.assertRaisesRegex(ValueError, "grade 0, 1, or 2"):
            MODULE.score_reviews(packet, mapping, [("fixture.json", result)])
        packet, mapping, result = fixtures()
        result["per_query"]["q1"]["ranking"]["hybrid_rrf"]["top_k_ids"][0] = "unknown"
        with self.assertRaisesRegex(ValueError, "unjudged ranked candidates"):
            MODULE.score_reviews(packet, mapping, [("fixture.json", result)])

    def test_mapping_must_match_every_packet_candidate(self):
        packet, mapping, result = fixtures()
        mapping.pop()
        with self.assertRaisesRegex(ValueError, "no matching mapping-key row"):
            MODULE.score_reviews(packet, mapping, [("fixture.json", result)])


if __name__ == "__main__":
    unittest.main()
