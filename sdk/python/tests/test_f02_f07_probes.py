"""F-02 / F-07 / F-08 — probe and metrics contracts.

F-02: /liveness must be an actual registered route (it once existed as a bare
      function with no decorator, so the Docker healthcheck and the Helm
      livenessProbe 404'd and restarted a healthy engine).
F-07: /readiness must fail closed (503) when storage disappears AFTER startup,
      not merely report the startup-time connection flag.
F-08: metric labels must stay bounded regardless of request-path cardinality.
"""
import unittest

from fastapi.testclient import TestClient

import synapse_memory.api.fastapi_server as srv


class TestLivenessRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv.app)

    def test_liveness_is_a_registered_route(self):
        paths = {getattr(r, "path", None) for r in srv.app.routes}
        self.assertIn("/liveness", paths, "GET /liveness must be registered")

    def test_liveness_returns_200(self):
        resp = self.client.get("/liveness")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "OK")

    def test_liveness_survives_storage_outage(self):
        """Liveness is process-only: a dead database must not fail it."""
        original = srv.store

        class DeadStore:
            def _get_connection(self):
                raise RuntimeError("connection refused")

            def _return_connection(self, _conn):
                pass

            def count_memories(self, *_a, **_k):
                raise RuntimeError("connection refused")

        try:
            srv.store = DeadStore()
            resp = self.client.get("/liveness")
            self.assertEqual(resp.status_code, 200)
        finally:
            srv.store = original


class TestReadinessFailsClosed(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv.app)

    def test_readiness_reports_503_when_postgres_drops_after_startup(self):
        original_backend, original_store = srv.ACTIVE_BACKEND, srv.store

        class DroppedStore:
            def _get_connection(self):
                raise ConnectionError("server closed the connection unexpectedly")

            def _return_connection(self, _conn):
                pass

        try:
            srv.ACTIVE_BACKEND = "pgvector"
            srv.store = DroppedStore()
            resp = self.client.get("/readiness")
            self.assertEqual(resp.status_code, 503, resp.text)
            # Shape-tolerant: the app wraps HTTPException details in the
            # canonical error envelope, so assert on the payload content.
            self.assertIn("UNHEALTHY", resp.text)
        finally:
            srv.store = original_store
            srv.ACTIVE_BACKEND = original_backend

    def test_readiness_recovers_to_200(self):
        resp = self.client.get("/readiness")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "READY")


class TestMetricsCardinalityBounded(unittest.TestCase):
    """A high-cardinality path stream must not grow the metric maps (F-08)."""

    def test_thousands_of_distinct_paths_collapse_into_other(self):
        srv._REQUEST_COUNTS.clear()
        srv._LATENCY_SAMPLES.clear()
        srv._METRICS_TTL.clear()

        for i in range(5000):
            srv._record_request(f"/api/memories/mem_{i:06d}", 200, 0.001)

        # Every unknown path is bucketed: one series for the "other" template.
        self.assertEqual(len(srv._REQUEST_COUNTS), 1)
        self.assertIn(("other", "200"), srv._REQUEST_COUNTS)
        self.assertLessEqual(len(srv._LATENCY_SAMPLES), len(srv._ROUTE_TEMPLATE_MAP) + 1)

        # Known templates still get their own label.
        srv._record_request("/api/memories/{memory_id}", 200, 0.002)
        self.assertIn(("/api/memories/{memory_id}", "200"), srv._REQUEST_COUNTS)
        self.assertLessEqual(len(srv._REQUEST_COUNTS), 2)


if __name__ == "__main__":
    unittest.main()
