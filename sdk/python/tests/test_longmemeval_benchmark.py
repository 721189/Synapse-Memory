import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "benchmark" / "evaluate_longmemeval.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("evaluate_longmemeval", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(question_id, evidence, session_ids, dates, sessions):
    return {
        "question_id": question_id,
        "question": "What release was mentioned?",
        "question_type": "knowledge-update",
        "question_date": "2023/05/30 (Tue) 23:40",
        "answer": "1.2.0" if evidence else "The information provided is not enough.",
        "answer_session_ids": evidence,
        "haystack_session_ids": session_ids,
        "haystack_dates": dates,
        "haystack_sessions": sessions,
    }


class LongMemEvalBenchmarkTests(unittest.TestCase):
    def test_reviewed_evidence_is_present_and_future_sessions_are_excluded(self):
        records = [
            row(
                "q1",
                ["s1"],
                ["s1", "s2"],
                ["2023/05/20 (Sat) 02:21", "2023/06/01 (Thu) 10:00"],
                [
                    [{"role": "user", "content": "We released version 1.2.0."}],
                    [{"role": "user", "content": "Future version 1.3.0 is planned.", "has_answer": True}],
                ],
            ),
            row(
                "q2",
                [],
                ["s3"],
                ["2023/05/20 (Sat) 02:21"],
                [[{"role": "user", "content": "Unrelated memory."}]],
            ),
        ]
        prepared, stats = MODULE._validate_source_records(records, expected_count=2)
        q1 = next(item for item in prepared if item["id"] == "q1")
        self.assertEqual([doc["id"] for doc in q1["documents"]], ["s1"])
        self.assertEqual(q1["relevant_ids"], {"s1"})
        self.assertEqual(stats["future_sessions_excluded_before_query_time"], 1)
        self.assertEqual(stats["no_evidence_question_count"], 1)
        self.assertNotIn("has_answer", q1["documents"][0]["content"])

    def test_exact_duplicate_sessions_are_collapsed_and_counted(self):
        records = [
            row(
                "q1",
                ["s1"],
                ["s1", "s1"],
                ["2023/05/20 (Sat) 02:21", "2023/05/20 (Sat) 02:21"],
                [
                    [{"role": "user", "content": "We released version 1.2.0."}],
                    [{"content": "We released version 1.2.0.", "role": "user"}],
                ],
            )
        ]
        prepared, stats = MODULE._validate_source_records(records, expected_count=1)
        self.assertEqual(len(prepared[0]["documents"]), 1)
        self.assertEqual(prepared[0]["relevant_ids"], {"s1"})
        self.assertEqual(stats["duplicate_session_entries_collapsed"], 1)

    def test_conflicting_non_evidence_sessions_are_preserved_separately(self):
        records = [
            row(
                "q1",
                [],
                ["s1", "s1"],
                ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 02:21"],
                [
                    [{"role": "user", "content": "First distinct distractor."}],
                    [{"role": "user", "content": "Second distinct distractor."}],
                ],
            )
        ]
        prepared, stats = MODULE._validate_source_records(records, expected_count=1)
        documents = prepared[0]["documents"]
        self.assertEqual(len(documents), 2)
        self.assertEqual([doc["id"] for doc in documents], ["s1", "s1::duplicate-2"])
        self.assertEqual({doc["source_session_id"] for doc in documents}, {"s1"})
        self.assertEqual(stats["conflicting_duplicate_session_entries_preserved"], 1)

    def test_conflicting_duplicate_sessions_fail_if_referenced_as_evidence(self):
        records = [
            row(
                "q1",
                ["s1"],
                ["s1", "s1"],
                ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 02:21"],
                [
                    [{"role": "user", "content": "First possible evidence."}],
                    [{"role": "user", "content": "Conflicting possible evidence."}],
                ],
            )
        ]
        with self.assertRaisesRegex(ValueError, "referenced by reviewed evidence"):
            MODULE._validate_source_records(records, expected_count=1)

    def test_future_dated_gold_evidence_fails_closed(self):
        records = [
            row(
                "q1",
                ["future"],
                ["future"],
                ["2023/06/01 (Thu) 10:00"],
                [[{"role": "user", "content": "Future evidence."}]],
            )
        ]
        with self.assertRaisesRegex(ValueError, "missing or occurs after"):
            MODULE._validate_source_records(records, expected_count=1)

    def test_metric_values_match_session_relevance_labels(self):
        values = MODULE._metrics(["a", "b", "c"], {"b"})
        self.assertEqual(values["recall_at_5"], 1.0)
        self.assertEqual(values["precision_at_5"], 0.2)
        self.assertEqual(values["mrr"], 0.5)
        self.assertEqual(values["ndcg_at_10"], 0.6309)

    def test_source_timestamp_formats_are_strictly_parsed(self):
        self.assertEqual(
            MODULE._parse_timestamp("2023/05/30 (Tue) 23:40").isoformat(),
            "2023-05-30T23:40:00",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported LongMemEval timestamp"):
            MODULE._parse_timestamp("not-a-date")


if __name__ == "__main__":
    unittest.main()
