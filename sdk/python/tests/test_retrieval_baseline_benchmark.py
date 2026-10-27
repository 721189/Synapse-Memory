"""Smoke tests for the reproducible offline retrieval benchmark CLI."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SDK_ROOT = REPO_ROOT / "sdk" / "python"
BENCHMARK_SCRIPT = SDK_ROOT / "benchmark" / "retrieval_baselines.py"
BENCHMARK_DATASET = SDK_ROOT / "benchmark" / "retrieval_dataset.json"
PACKING_STRESS_DATASET = SDK_ROOT / "benchmark" / "packing_stress_dataset.json"


class TestRetrievalBaselineBenchmark(unittest.TestCase):
    def test_cli_writes_measured_results_and_checks_safety_invariants(self):
        with tempfile.TemporaryDirectory(prefix="synapse-benchmark-test-") as temp_dir:
            output_path = Path(temp_dir) / "results.json"
            env = os.environ.copy()
            python_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (str(SDK_ROOT), python_path) if part
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(BENCHMARK_SCRIPT),
                    "--dataset",
                    str(BENCHMARK_DATASET),
                    "--output",
                    str(output_path),
                    "--repeats",
                    "1",
                    "--warmups",
                    "0",
                    "--top-k",
                    "3",
                    "--candidate-k",
                    "5",
                    "--token-budget",
                    "70",
                ],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                check=False,
                text=True,
                timeout=60,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg="Benchmark CLI failed:\n{0}\n{1}".format(
                    result.stdout, result.stderr
                ),
            )
            self.assertTrue(output_path.exists(), result.stdout)
            data = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(data["benchmark"], "synapse_retrieval_baselines")
            self.assertEqual(data["dataset"]["document_count"], 25)
            self.assertEqual(data["dataset"]["query_count"], 14)
            self.assertFalse(data["environment"]["embedding_is_semantic"])
            self.assertEqual(
                set(data["ranking"]),
                {"lexical_bm25", "dense_cosine", "hybrid_rrf"},
            )
            self.assertEqual(
                set(data["context_packing"]),
                {
                    "top_k_unbudgeted",
                    "ranked_fit",
                    "greedy_ratio",
                    "knapsack_dp",
                    "rank_discount_dp",
                },
            )
            for values in data["ranking"].values():
                self.assertEqual(values["latency_ms"]["samples"], 14)
                self.assertIn("mean_recall_at_k", values)
                self.assertEqual(len(values["latency_samples_ms"]), 14)

            for values in data["context_packing"].values():
                self.assertIn("mean_candidate_recall", values)
                self.assertIn("mean_retained_candidate_relevant_recall", values)
                self.assertIn("mean_score_utility_retained_pct", values)
                for query_values in values["per_query"].values():
                    self.assertIn("candidate_recall", query_values)
                    self.assertIn("retained_candidate_relevant_recall", query_values)
                    self.assertIn("score_utility_retained_pct", query_values)

            self.assertEqual(data["tenant_isolation"]["status"], "passed")
            self.assertEqual(data["tenant_isolation"]["wrong_tenant_row_count"], 0)
            self.assertEqual(data["deletion"]["status"], "passed")
            self.assertTrue(data["deletion"]["sentinel_removed_from_tenant_store"])


    def test_paraphrase_heavy_multi_fact_stress_fixture(self):
        with tempfile.TemporaryDirectory(prefix="synapse-packing-stress-") as temp_dir:
            output_path = Path(temp_dir) / "packing-stress.json"
            env = os.environ.copy()
            python_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (str(SDK_ROOT), python_path) if part
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(BENCHMARK_SCRIPT),
                    "--dataset",
                    str(PACKING_STRESS_DATASET),
                    "--output",
                    str(output_path),
                    "--repeats",
                    "1",
                    "--warmups",
                    "0",
                    "--top-k",
                    "5",
                    "--candidate-k",
                    "10",
                    "--token-budget",
                    "90",
                ],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg="Stress benchmark failed:\n{0}\n{1}".format(
                    result.stdout, result.stderr
                ),
            )
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["dataset"]["document_count"], 96)
            self.assertEqual(data["dataset"]["query_count"], 24)
            self.assertEqual(data["dataset"]["tenant_count"], 3)
            self.assertIn("stress", data["dataset"]["name"])
            for values in data["context_packing"].values():
                self.assertIn("mean_candidate_recall", values)
                self.assertIn("mean_retained_candidate_relevant_recall", values)
                self.assertEqual(len(values["per_query"]), 24)
            self.assertEqual(data["tenant_isolation"]["status"], "passed")
            self.assertEqual(data["deletion"]["status"], "passed")


    def test_semantic_evaluation_fixture_is_complete_and_tenant_scoped(self):
        semantic_dataset = SDK_ROOT / "benchmark" / "semantic_packing_eval_dataset.json"
        data = json.loads(semantic_dataset.read_text(encoding="utf-8"))
        documents = {item["id"]: item for item in data["documents"]}
        queries = data["queries"]

        self.assertEqual(data["name"], "synapse-semantic-packing-eval-v1")
        self.assertEqual(len(documents), 96)
        self.assertEqual(len(queries), 24)
        self.assertEqual({item["tenant_id"] for item in documents.values()}, {
            "tenant-alpha", "tenant-beta", "tenant-gamma"
        })
        self.assertEqual(len({item["query"] for item in queries}), len(queries))

        for query in queries:
            self.assertEqual(len(query["relevant_memory_ids"]), 2)
            self.assertEqual(len(set(query["relevant_memory_ids"])), 2)
            for memory_id in query["relevant_memory_ids"]:
                self.assertIn(memory_id, documents)
                self.assertEqual(
                    documents[memory_id]["tenant_id"], query["tenant_id"]
                )

        self.assertIn("not user-derived", data["purpose"])
        self.assertIn("independently reviewed", data["purpose"])

    def test_semantic_model_missing_dependency_fails_without_fallback(self):
        # This is a lightweight contract check; model downloads belong in the
        # dedicated semantic CI job, not the ordinary offline unit-test suite.
        sys.path.insert(0, str(SDK_ROOT / "benchmark"))
        from retrieval_baselines import _SentenceTransformerBenchmarkEmbedder

        import unittest.mock as mock

        with mock.patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaisesRegex(RuntimeError, "optional local-models extra"):
                _SentenceTransformerBenchmarkEmbedder(
                    "sentence-transformers/all-MiniLM-L6-v2",
                    "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
                )

    def test_hybrid_ranking_is_independent_of_input_row_order(self):
        # BM25 and cosine ties are common in the deterministic fixture. They
        # must not inherit whatever order SQLite happened to return.
        sys.path.insert(0, str(SDK_ROOT / "benchmark"))
        from retrieval_baselines import _rank_documents
        from synapse_memory.core.embedder import SynapseEmbedder
        from synapse_memory.core.hybrid_search import HybridSearch

        embedder = SynapseEmbedder(provider="deterministic")
        searcher = HybridSearch()
        query = "shared common keyword"
        vector = embedder.embed_query(query)
        documents = [
            {
                "id": "doc-z",
                "tenant_id": "test",
                "content": "shared common keyword",
                "embedding": embedder.embed_query("shared common keyword"),
                "category": "fact",
                "token_cost": 18,
            },
            {
                "id": "doc-a",
                "tenant_id": "test",
                "content": "shared common keyword",
                "embedding": embedder.embed_query("shared common keyword"),
                "category": "fact",
                "token_cost": 18,
            },
            {
                "id": "doc-m",
                "tenant_id": "test",
                "content": "shared common keyword",
                "embedding": embedder.embed_query("shared common keyword"),
                "category": "fact",
                "token_cost": 18,
            },
        ]

        forward = _rank_documents("hybrid_rrf", query, vector, documents, searcher)
        reversed_rows = _rank_documents(
            "hybrid_rrf", query, vector, list(reversed(documents)), searcher
        )
        self.assertEqual(
            [item["id"] for item in forward],
            [item["id"] for item in reversed_rows],
        )


if __name__ == "__main__":
    unittest.main()
