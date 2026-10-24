"""F-05 / F-06 — data-at-rest claims and embedding-provider honesty.

F-05: `content_plain` is a plaintext search projection. These tests pin the
      behaviour so no document can claim the searchable copy is encrypted, and
      cover the opt-out that stops the plaintext from being persisted at all.
F-06: the deterministic hash provider is not semantic; production must refuse
      it unless the operator explicitly opts in.
"""
import os
import unittest

from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.pgvector_db import searchable_projection


class TestSearchableProjectionIsPlaintext(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("SYNAPSE_STORE_SEARCHABLE_PLAINTEXT")
        os.environ.pop("SYNAPSE_STORE_SEARCHABLE_PLAINTEXT", None)

    def tearDown(self):
        os.environ.pop("SYNAPSE_STORE_SEARCHABLE_PLAINTEXT", None)
        if self._saved is not None:
            os.environ["SYNAPSE_STORE_SEARCHABLE_PLAINTEXT"] = self._saved

    def test_default_projection_is_the_raw_plaintext(self):
        """The documented trade-off: lexical search needs readable text."""
        secret = "wire transfer instructions for account 4111-2222"
        self.assertEqual(searchable_projection(secret), secret)

    def test_opt_out_stores_no_plaintext(self):
        os.environ["SYNAPSE_STORE_SEARCHABLE_PLAINTEXT"] = "false"
        secret = "wire transfer instructions for account 4111-2222"
        self.assertEqual(searchable_projection(secret), "")

    def test_opt_out_is_case_and_space_tolerant(self):
        os.environ["SYNAPSE_STORE_SEARCHABLE_PLAINTEXT"] = " FALSE "
        self.assertEqual(searchable_projection("anything"), "")


class TestDeterministicProviderIsNotSemantic(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in (
            "NODE_ENV",
            "SYNAPSE_EMBEDDING_PROVIDER",
            "SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING",
        )}

    def tearDown(self):
        for key, value in self._env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_deterministic_provider_reports_itself_non_semantic(self):
        os.environ.pop("NODE_ENV", None)
        emb = SynapseEmbedder(provider="deterministic")
        self.assertFalse(emb.is_semantic)

    def test_production_refuses_deterministic_provider_by_default(self):
        os.environ["NODE_ENV"] = "production"
        os.environ.pop("SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING", None)
        with self.assertRaises(RuntimeError) as ctx:
            SynapseEmbedder(provider="deterministic")
        self.assertIn("not a semantic model", str(ctx.exception))

    def test_production_allows_explicit_opt_in_and_still_flags_non_semantic(self):
        os.environ["NODE_ENV"] = "production"
        os.environ["SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING"] = "true"
        emb = SynapseEmbedder(provider="deterministic")
        self.assertFalse(emb.is_semantic)
        self.assertEqual(emb.provider_name, "deterministic")


if __name__ == "__main__":
    unittest.main()
