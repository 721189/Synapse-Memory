import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "benchmark" / "longmemeval_external_eval.py"
SPEC = importlib.util.spec_from_file_location("longmemeval_external_eval", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class LongMemEvalExternalEvalTests(unittest.TestCase):
    def test_temporal_filter_excludes_future_sessions_and_keeps_reviewed_evidence(self):
        rows = [{
            "question_id": "q1",
            "question": "What version was released?",
            "question_type": "knowledge-update",
            "question_date": "2023/05/30 (Tue) 23:40",
            "answer": "1.2.0",
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": [
                "2023/05/20 (Sat) 02:21",
                "2023/06/01 (Thu) 10:00",
            ],
            "haystack_sessions": [
                [{"role": "user", "content": "We released version 1.2.0."}],
                [{"role": "user", "content": "Future version 1.3.0 is planned."}],
            ],
        }, {
            "question_id": "q2",
            "question": "Unknown fact?",
            "question_type": "single-session-user",
            "question_date": "2023/05/30 (Tue) 23:40",
            "answer": "The information provided is not enough.",
            "answer_session_ids": [],
            "haystack_session_ids": ["s3"],
            "haystack_dates": ["2023/05/20 (Sat) 02:21"],
            "haystack_sessions": [[{"role": "user", "content": "Unrelated memory."}]],
        }]
        docs, data = MODULE.build_query_corpora(rows)
        self.assertEqual(len(data["queries"]), 1)
        self.assertEqual(data["queries"][0]["id"], "q1")
        self.assertEqual(data["queries"][0]["relevant_memory_ids"], ["q1::s1"])
        self.assertEqual({doc["id"] for doc in docs}, {"q1::s1"})
        self.assertEqual(data["excluded"]["unanswerable_no_evidence"], 1)
        self.assertNotIn("Future version", docs[0]["content"])

    def test_relevance_metrics_are_computed_against_reviewed_session_ids(self):
        got = MODULE.metrics(["a", "b", "c"], ["b"], 2)
        self.assertEqual(got["recall_at_k"], 1.0)
        self.assertEqual(got["precision_at_k"], 0.5)
        self.assertEqual(got["mrr_at_k"], 0.5)
        self.assertEqual(got["ndcg_at_k"], 0.63093)

    def test_date_parser_rejects_unexpected_date_format(self):
        with self.assertRaises(ValueError):
            MODULE.parse_question_date("2023-05-30T23:40:00")


if __name__ == "__main__":
    unittest.main()
