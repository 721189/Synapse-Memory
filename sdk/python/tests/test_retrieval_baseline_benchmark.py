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
            self.assertEqual(data["dataset"]["document_count"], 23)
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
                },
            )
            for values in data["ranking"].values():
                self.assertEqual(values["latency_ms"]["samples"], 14)
                self.assertIn("mean_recall_at_k", values)
                self.assertEqual(len(values["latency_samples_ms"]), 14)

            self.assertEqual(data["tenant_isolation"]["status"], "passed")
            self.assertEqual(data["tenant_isolation"]["wrong_tenant_row_count"], 0)
            self.assertEqual(data["deletion"]["status"], "passed")
            self.assertTrue(data["deletion"]["sentinel_removed_from_tenant_store"])


if __name__ == "__main__":
    unittest.main()
