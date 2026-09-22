import unittest
from synapse_memory.core.pgvector_db import PGVectorMemoryStore


class TestPGVectorMemoryStore(unittest.TestCase):
    def setUp(self):
        # Initialize in disconnected/fallback mode for isolated unit testing
        self.store = PGVectorMemoryStore(auto_init=False)

    def test_insert_and_get(self):
        mem = {
            "id": "pg_mem_1",
            "content": "User prefers strict PostgreSQL schema validation.",
            "category": "preference",
            "confidence": 0.95,
            "created_at": 1700000000.0,
            "token_cost": 30,
            "embedding": [0.1] * 128
        }
        self.store.insert_memory(mem)
        retrieved = self.store.get_memory_by_id("pg_mem_1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], "pg_mem_1")
        self.assertEqual(retrieved["category"], "preference")

    def test_vector_search_fallback(self):
        v1 = [1.0] + [0.0] * 127
        v2 = [0.0] * 128
        self.store.insert_memory({
            "id": "mem_close",
            "content": "Close vector match",
            "category": "test",
            "confidence": 0.9,
            "created_at": 100.0,
            "embedding": v1
        })
        self.store.insert_memory({
            "id": "mem_far",
            "content": "Far vector match",
            "category": "test",
            "confidence": 0.9,
            "created_at": 100.0,
            "embedding": v2
        })

        results = self.store.vector_search(query_vec=v1, top_k=5)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], "mem_close")
        self.assertAlmostEqual(results[0]["cosine_similarity"], 1.0, places=3)

    def test_fail_closed_mode_raises_on_connection_failure(self):
        from synapse_memory.core.pgvector_db import HAS_PSYCOPG2
        if not HAS_PSYCOPG2:
            self.skipTest("psycopg2 not installed; skipping connection failure assertion.")
        with self.assertRaises(RuntimeError):
            PGVectorMemoryStore(
                connection_string="postgresql://invalid_user:invalid_pass@127.0.0.1:9999/nonexistent",
                fail_closed=True,
                auto_init=True
            )


if __name__ == "__main__":
    unittest.main()
