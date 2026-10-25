"""P0: 3072-d production embedding path against a real pgvector database.

Gated: runs only when DATABASE_URL points at PostgreSQL *and* the operator opts
in with SYNAPSE_LIVE_GEMINI=1 (a real provider call is expensive and needs a
key). Otherwise it skips, so CI without a key still passes.

Why this exists: the rest of the suite uses 128- or 384-dimensional vectors,
which never exercises the shipped default (gemini, 3072-d) or the HNSW index
built at that width.
"""
import os
import unittest

import pytest

from synapse_memory.core.embedding_provider import GeminiEmbeddingProvider

DATABASE_URL = os.environ.get("DATABASE_URL", "")
LIVE = os.environ.get("SYNAPSE_LIVE_GEMINI") == "1" and bool(DATABASE_URL)


@pytest.mark.skipif(not LIVE, reason="needs DATABASE_URL + SYNAPSE_LIVE_GEMINI=1")
class TestProductionDimension(unittest.TestCase):
    def test_3072_dimensional_round_trip_and_vector_search(self):
        from synapse_memory.core.pgvector_db import PGVectorMemoryStore

        store = PGVectorMemoryStore(connection_string=DATABASE_URL, dimension=3072)
        self.assertTrue(store.is_connected, "pgvector store failed to connect")

        provider = GeminiEmbeddingProvider(api_key=os.environ["GEMINI_API_KEY"])
        self.assertEqual(provider.dimension, 3072)

        text = "The deployment region is eu-west-1 for cluster alpha."
        vector = provider.embed(text)
        self.assertEqual(len(vector), 3072, "provider did not return 3072 dimensions")

        store.insert_memory({
            "id": "mem_3072_probe",
            "tenant_id": "tenant_3072",
            "content": text,
            "category": "fact",
            "confidence": 0.99,
            "created_at": 1.0,
            "access_count": 0,
            "token_cost": 20,
            "embedding": vector,
        })

        results = store.vector_search(vector, top_k=1, tenant_id="tenant_3072")
        self.assertTrue(results, "vector_search returned nothing at 3072 dimensions")
        self.assertEqual(results[0]["id"], "mem_3072_probe")

        # A mismatched width must fail closed rather than silently corrupt the index.
        with self.assertRaises(Exception):
            store.insert_memory({
                "id": "mem_3072_bad",
                "tenant_id": "tenant_3072",
                "content": "wrong width",
                "category": "fact",
                "confidence": 0.5,
                "created_at": 1.0,
                "access_count": 0,
                "token_cost": 5,
                "embedding": [0.0] * 384,
            })


if __name__ == "__main__":
    unittest.main()
