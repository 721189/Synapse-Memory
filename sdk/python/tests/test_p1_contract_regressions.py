"""Phase 3/4 regression tests: API validation limits, v4 migration, live metrics."""
import os
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from synapse_memory.api.fastapi_server import app, security_manager
from synapse_memory.core.migrations import run_migrations


class TestApiValidationLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        token, _ = security_manager.generate_api_key(
            name="Validation Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_val",
            scopes=["memories:read", "memories:write", "memories:delete"],
            role="developer",
        )
        cls.headers = {"X-Synapse-API-Key": token, "X-Tenant-ID": "tenant_val"}

    def test_oversized_content_rejected(self):
        r = self.client.post("/api/memories/add", json={"content": "x" * 32001}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_bad_category_rejected(self):
        r = self.client.post("/api/memories/add", json={"content": "ok", "category": "BAD CATEGORY!"}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_bad_tenant_rejected(self):
        r = self.client.post("/api/memories/add", json={"content": "ok", "tenantId": "evil tenant!!"}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_bad_feedback_type_rejected(self):
        r = self.client.post("/feedback", json={"memory_id": "m1", "feedback_type": "meh"}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_oversized_query_rejected(self):
        r = self.client.post("/query", json={"prompt": "y" * 8001}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_oversized_bulk_rejected(self):
        r = self.client.post("/api/memories/bulk", json={"items": [{"content": "z" * 10000}] * 60}, headers=self.headers)
        self.assertIn(r.status_code, (413, 422))

    def test_envelope_shape_on_success(self):
        r = self.client.get("/api/memories", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("success"))
        self.assertIn("data", body)
        self.assertIn("request_id", body)


class TestMigrationV4(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',"
            " content TEXT NOT NULL, category TEXT NOT NULL, confidence REAL NOT NULL,"
            " created_at REAL NOT NULL, last_accessed_at REAL NOT NULL, access_count INTEGER NOT NULL,"
            " token_cost INTEGER NOT NULL, embedding TEXT, feedback_multiplier REAL NOT NULL);"
        )
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy1", "t1", "plain hello world", "fact", 0.9, 1.0, 1.0, 0, 3, "[]", 1.0),
        )
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("cipher1", "t1", "gAAAAAfaketoken123", "fact", 0.9, 1.0, 1.0, 0, 3, "[]", 1.0),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def test_v4_backfills_plaintext_only_and_idempotent(self):
        first = run_migrations(self._get_conn, is_postgres=False)
        self.assertGreaterEqual(first, 1)
        conn = self._get_conn()
        try:
            legacy = conn.execute("SELECT content_plain FROM memories WHERE id='legacy1';").fetchone()[0]
            cipher = conn.execute("SELECT content_plain FROM memories WHERE id='cipher1';").fetchone()[0]
            versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations;").fetchall()}
        finally:
            conn.close()
        self.assertEqual(legacy, "plain hello world")
        self.assertEqual(cipher, "")
        self.assertIn(4, versions)
        self.assertEqual(run_migrations(self._get_conn, is_postgres=False), 0)


class TestLiveMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        token, _ = security_manager.generate_api_key(
            name="Metrics Key", organization_id="org_test", project_id="proj_test",
            tenant_id="tenant_metrics", scopes=["memories:read", "memories:write"], role="developer",
        )
        cls.headers = {"X-Synapse-API-Key": token, "X-Tenant-ID": "tenant_metrics"}

    def test_metrics_reflect_live_traffic(self):
        before = self.client.get("/metrics").text
        self.client.post("/api/memories/add", json={"content": "metrics probe memory"}, headers=self.headers)
        self.client.post("/query", json={"prompt": "metrics probe"}, headers=self.headers)
        after = self.client.get("/metrics").text
        self.assertIn("synapse_requests_total", after)
        self.assertIn("synapse_ingest_total", after)
        self.assertIn("synapse_retrieval_total", after)
        self.assertIn("synapse_request_latency_seconds", after)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
