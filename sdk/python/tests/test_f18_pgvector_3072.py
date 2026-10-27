"""High-dimensional pgvector coverage without paid embedding API calls.

Static tests always run. The live test is enabled by SYNAPSE_LIVE_PGVECTOR_3072=1
and DATABASE_URL, and should point at an isolated, disposable PostgreSQL database.
It uses synthetic vectors because this test is about pgvector type/index/query
compatibility, not semantic embedding quality.
"""
import os
import unittest

import pytest

from synapse_memory.core.pgvector_db import PGVectorMemoryStore

DATABASE_URL = os.environ.get("DATABASE_URL", "")
LIVE = os.environ.get("SYNAPSE_LIVE_PGVECTOR_3072") == "1" and bool(DATABASE_URL)


def _store_without_connection(dimension: int) -> PGVectorMemoryStore:
    store = object.__new__(PGVectorMemoryStore)
    store.dimension = dimension
    store.m = 16
    store.ef_construction = 64
    return store


class TestHNSWDimensionContract(unittest.TestCase):
    def test_standard_vector_index_is_used_up_to_2000_dimensions(self) -> None:
        store = _store_without_connection(2000)
        self.assertIn("embedding vector_cosine_ops", store._hnsw_index_sql())
        self.assertNotIn("halfvec_cosine_ops", store._hnsw_index_sql())
        self.assertEqual(store._vector_distance_sql(), "embedding <=> %s::vector")

    def test_halfvec_expression_index_is_used_above_2000_dimensions(self) -> None:
        store = _store_without_connection(3072)
        ddl = store._hnsw_index_sql()
        self.assertIn("(embedding::halfvec(3072)) halfvec_cosine_ops", ddl)
        self.assertIn("(embedding::halfvec(3072)) <=> (%s)::halfvec(3072)", store._vector_distance_sql())

    def test_dimensions_above_halfvec_limit_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "1–4000 dimensions"):
            PGVectorMemoryStore(
                dimension=4001, auto_init=False, encryption_provider=object()
            )


@pytest.mark.skipif(
    not LIVE,
    reason="set DATABASE_URL and SYNAPSE_LIVE_PGVECTOR_3072=1 for isolated live pgvector validation",
)
class TestProductionDimension(unittest.TestCase):
    def test_3072_dimensional_hnsw_index_insert_and_query(self) -> None:
        import psycopg2

        store = PGVectorMemoryStore(
            connection_string=DATABASE_URL,
            dimension=3072,
            fail_closed=True,
        )
        self.assertTrue(store.is_connected, "pgvector store failed to connect")
        tenant = "tenant_3072_ci"
        memory_id = "mem_3072_synthetic_probe"
        vector = [0.0] * 3072
        vector[17] = 1.0

        try:
            store.delete_memory(memory_id, tenant_id=tenant)
            store.insert_memory(
                {
                    "id": memory_id,
                    "tenant_id": tenant,
                    "content": "Synthetic 3072-dimensional index compatibility probe.",
                    "category": "test",
                    "confidence": 0.99,
                    "created_at": 1.0,
                    "access_count": 0,
                    "token_cost": 8,
                    "embedding": vector,
                }
            )

            result = store.vector_search(vector, top_k=1, tenant_id=tenant)
            self.assertTrue(result, "3072-dimensional vector_search returned no records")
            self.assertEqual(result[0]["id"], memory_id)

            with psycopg2.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname = 'public' AND indexname = 'idx_memories_hnsw';"
                    )
                    row = cur.fetchone()
            self.assertIsNotNone(row, "HNSW index was not created")
            self.assertIn("halfvec_cosine_ops", row[0])
        finally:
            store.delete_memory(memory_id, tenant_id=tenant)


if __name__ == "__main__":
    unittest.main()
