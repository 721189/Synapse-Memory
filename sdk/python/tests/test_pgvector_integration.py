import os
import time
import unittest
from typing import Any, Dict, List

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

from synapse_memory.core.pgvector_db import PGVectorMemoryStore


class TestPGVectorIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_PSYCOPG2:
            raise unittest.SkipTest("psycopg2 not installed; skipping PGVector integration tests.")
        cls.db_url = os.environ.get("DATABASE_URL")
        if not cls.db_url or not cls.db_url.startswith("postgres"):
            raise unittest.SkipTest("DATABASE_URL environment variable is not configured for PostgreSQL; skipping integration tests.")
        
        # Ensure the vector extension is active
        try:
            conn = psycopg2.connect(cls.db_url)
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.commit()
            conn.close()
        except Exception as e:
            raise unittest.SkipTest(f"Failed to connect to database or install vector extension: {e}")

    def setUp(self):
        self.store = PGVectorMemoryStore(
            connection_string=self.db_url,
            dimension=384,
            auto_init=True,
            fail_closed=True
        )
        if self.store.is_connected:
            self.store.clear_memories()

    def tearDown(self):
        if self.store.is_connected:
            self.store.clear_memories()

    def test_rls_isolation_and_cross_tenant_rejection(self):
        # 1. Tenant A inserts a memory
        mem_a = {
            "id": "mem_tenant_a_1",
            "content": "Tenant A highly sensitive cognitive profile details.",
            "category": "personal",
            "confidence": 0.99,
            "created_at": time.time(),
            "embedding": [0.1] * 384,
            "tenant_id": "tenant_a"
        }
        self.store.insert_memory(mem_a)

        # 2. Query as Tenant B -> Should return zero rows (isolation)
        mems_b = self.store.get_all_memories(tenant_id="tenant_b")
        self.assertEqual(len(mems_b), 0)

        # 3. Cross-tenant write rejection:
        # Tenant A tries to write as tenant_b while tenant_a context is enforced.
        conn = self.store._get_connection()
        try:
            with conn.cursor() as cur:
                # Set tenant context to tenant_a
                self.store._set_tenant_context(cur, "tenant_a")
                # Try inserting a record with tenant_id='tenant_b'
                with self.assertRaises(psycopg2.Error):
                    cur.execute("""
                        INSERT INTO memories (id, tenant_id, content, category, confidence, created_at, embedding)
                        VALUES ('mem_cross_tenant', 'tenant_b', 'Illegal write', 'fact', 0.9, %s, %s);
                    """, (time.time(), [0.1] * 384))
                    conn.commit()
        finally:
            conn.rollback()
            self.store._return_connection(conn)

    def test_missing_tenant_context_zero_access(self):
        # Insert a memory under tenant_a
        mem_a = {
            "id": "mem_tenant_a_2",
            "content": "Tenant A details.",
            "category": "personal",
            "confidence": 0.99,
            "created_at": time.time(),
            "embedding": [0.1] * 384,
            "tenant_id": "tenant_a"
        }
        self.store.insert_memory(mem_a)

        conn = self.store._get_connection()
        try:
            with conn.cursor() as cur:
                # Set local app.current_tenant to empty
                cur.execute("SET LOCAL app.current_tenant = '';")
                cur.execute("SELECT * FROM memories;")
                rows = cur.fetchall()
                self.assertEqual(len(rows), 0)
        finally:
            self.store._return_connection(conn)

    def test_hnsw_large_dataset_and_nearest_neighbors(self):
        # Insert 1000+ vectors
        num_records = 1005
        memories = []
        for i in range(num_records):
            val = 1.0 if i == 42 else 0.0
            vec = [val] * 384
            memories.append({
                "id": f"hnsw_mem_{i}",
                "content": f"Structured cluster item number {i}.",
                "category": "fact",
                "confidence": 0.95,
                "created_at": time.time() - i,
                "embedding": vec,
                "tenant_id": "tenant_hnsw"
            })

        for m in memories:
            self.store.insert_memory(m)

        # Query using a vector close to the targeted nearest neighbor [1.0, 1.0, ...]
        query_vec = [1.0] * 384
        results = self.store.vector_search(query_vec=query_vec, top_k=5, tenant_id="tenant_hnsw")
        
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], "hnsw_mem_42")

    def test_dimension_constraints_accepted_and_rejected(self):
        # Dimension is configured to 384
        valid_mem = {
            "id": "dim_valid",
            "content": "Valid dimensions.",
            "category": "fact",
            "confidence": 0.9,
            "created_at": time.time(),
            "embedding": [0.1] * 384,
            "tenant_id": "default"
        }
        self.store.insert_memory(valid_mem)
        self.assertIsNotNone(self.store.get_memory_by_id("dim_valid"))

        # 383 vector -> rejected due to dimension mismatch exception or DB error
        invalid_mem = {
            "id": "dim_invalid",
            "content": "Invalid dimensions.",
            "category": "fact",
            "confidence": 0.9,
            "created_at": time.time(),
            "embedding": [0.1] * 383,
            "tenant_id": "default"
        }
        with self.assertRaises(Exception):
            self.store.insert_memory(invalid_mem)

    def test_failure_mode_postgres_unavailable(self):
        # If Postgres is unavailable and fail_closed=True, production startup fails
        with self.assertRaises(RuntimeError):
            PGVectorMemoryStore(
                connection_string="postgresql://nonexistent_user:wrong_pass@127.0.0.1:9999/nonexistent",
                dimension=384,
                fail_closed=True,
                auto_init=True
            )


if __name__ == "__main__":
    unittest.main()
