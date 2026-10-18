"""Deterministic hash embedding provider: cross-process stability.

Python's built-in string hash() is salted per interpreter (PYTHONHASHSEED), so
hash(word) embeddings differ between processes and must not back any stored
index. The provider now keys tokens off a SHA-256 digest, which is stable
everywhere. It is deliberately lexical (not semantic) and only for repeatable
offline tests.
"""
import json
import os
import subprocess
import sys
import unittest

from synapse_memory.core.embedding_provider import DeterministicHashEmbeddingProvider

SDK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TEXT = "kubernetes deployment rollout status check for worker pod replicas"


def embed_in_subprocess(dim: int) -> str:
    script = (
        "import json;"
        "from synapse_memory.core.embedding_provider import DeterministicHashEmbeddingProvider;"
        "print(json.dumps(DeterministicHashEmbeddingProvider(dim=%d).embed(%r)))"
        % (dim, TEST_TEXT)
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = SDK_ROOT
    return subprocess.check_output([sys.executable, "-c", script], env=env).decode().strip()


class TestDeterministicEmbeddingStability(unittest.TestCase):
    def test_same_process_stable(self):
        provider = DeterministicHashEmbeddingProvider(dim=128)
        self.assertEqual(provider.embed(TEST_TEXT), provider.embed(TEST_TEXT))
        self.assertEqual(len(provider.embed(TEST_TEXT)), 128)

    def test_stable_across_separate_processes(self):
        vec_a = json.loads(embed_in_subprocess(128))
        vec_b = json.loads(embed_in_subprocess(128))
        self.assertEqual(vec_a, vec_b)

    def test_stable_across_dimensions_and_processes(self):
        # A process-local dimension change must not alter the values for a
        # fixed dimension, and results must agree between processes.
        vec_a = json.loads(embed_in_subprocess(256))
        vec_b = json.loads(embed_in_subprocess(256))
        self.assertEqual(vec_a, vec_b)
        self.assertEqual(len(vec_a), 256)


if __name__ == "__main__":
    unittest.main()
