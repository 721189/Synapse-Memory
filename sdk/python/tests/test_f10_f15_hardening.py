"""F-10 / F-11 / F-12 / F-13 / F-15 regression tests.

F-10  a cached API key is re-validated periodically, so revocation on another
      replica is honoured within a bounded window instead of the full TTL.
F-11  the engine's scrubber really redacts (no CMEK claim, no count-only hits).
F-12  a failed ALTER TABLE propagates instead of being swallowed.
F-13  per-IP auth state is bounded and shutdown flushes the audit buffer.
F-15  the offline fallback cache is bounded.
"""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import synapse_memory.api.fastapi_server as srv
import synapse_memory.core.auth as auth
from synapse_memory.core.auth import APIKeyRecord
from synapse_memory.core.migrations import _add_column_if_missing
from synapse_memory.core.pgvector_db import _FALLBACK_CACHE_MAX, PGVectorMemoryStore


def _record(role="developer", tenant="tenant_a"):
    return APIKeyRecord(
        key_id="key_test",
        key_hash="hash",
        name="test",
        organization_id="org_a",
        project_id="proj_a",
        tenant_id=tenant,
        scopes=["memories:read"],
        role=role,
        is_active=True,
        expires_at=None,
        created_at=0.0,
        last_used_at=None,
    )


class TestRevocationRevalidation(unittest.TestCase):
    """F-10: cache entries go stale after _AUTH_REVALIDATE_SECONDS."""

    def test_cached_entry_is_served_inside_the_window(self):
        sm = auth.SecurityManager(db_path=":memory:")
        try:
            sm._cache_key("h", 1000.0, _record())
            self.assertIsNotNone(
                sm._get_cached_key("h", 1000.0 + auth._AUTH_REVALIDATE_SECONDS - 0.1)
            )
        finally:
            sm.close()

    def test_cached_entry_expires_after_the_revalidation_window(self):
        sm = auth.SecurityManager(db_path=":memory:")
        try:
            sm._cache_key("h", 1000.0, _record())
            # Still far inside the 30s TTL, but past the revalidation window:
            # the entry must be dropped so the next call re-reads the database.
            self.assertIsNone(
                sm._get_cached_key("h", 1000.0 + auth._AUTH_REVALIDATE_SECONDS + 0.1)
            )
        finally:
            sm.close()

    def test_window_is_shorter_than_the_cache_ttl(self):
        self.assertLess(auth._AUTH_REVALIDATE_SECONDS, auth._AUTH_KEY_CACHE_TTL)


class TestPerIpStateIsBounded(unittest.TestCase):
    """F-13: rotating source addresses cannot grow the failure maps forever."""

    def test_failure_maps_stay_within_the_cap(self):
        sm = auth.SecurityManager(db_path=":memory:")
        original = auth._MAX_TRACKED_IPS
        auth._MAX_TRACKED_IPS = 10
        try:
            for i in range(200):
                sm._record_ip_failure(f"10.0.0.{i}")
            self.assertLessEqual(len(sm._ip_failures), 10)
        finally:
            auth._MAX_TRACKED_IPS = original
            sm.close()

    def test_close_flushes_and_clears_state(self):
        sm = auth.SecurityManager(db_path=":memory:")
        sm._cache_key("h", 1.0, _record())
        sm._record_ip_failure("10.1.1.1")
        sm.close()  # must not raise, must flush the audit buffer
        self.assertEqual(sm._key_cache, {})
        self.assertEqual(dict(sm._ip_failures), {})


class TestScrubActuallyRedacts(unittest.TestCase):
    """F-11: representative secrets are removed, not just counted."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(srv.app)
        token, _ = srv.security_manager.generate_api_key(
            name="scrub-key",
            organization_id="org_scrub",
            project_id="proj_scrub",
            tenant_id="tenant_scrub",
            scopes=["memories:read"],
            role="developer",
        )
        cls.headers = {"X-Synapse-API-Key": token, "X-Tenant-ID": "tenant_scrub"}

    def _scrub(self, text):
        resp = self.client.post(
            "/api/security/scrub", json={"text": text}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def test_each_category_is_removed_from_the_output(self):
        payload = {
            "email": "alice@example.com",
            "phone": "+91 98765 43210",
            "card": "4111 1111 1111 1111",
            "aadhaar": "1234 5678 9012",
            "pan": "ABCDE1234F",
            "ssn": "123-45-6789",
            "api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz0123",
        }
        text = (
            f"mail {payload['email']} phone {payload['phone']} card {payload['card']} "
            f"aadhaar {payload['aadhaar']} pan {payload['pan']} ssn {payload['ssn']} "
            f"key {payload['api_key']}"
        )
        data = self._scrub(text)
        scrubbed = data["scrubbedText"]
        for label, value in payload.items():
            self.assertNotIn(value, scrubbed, f"{label} was not redacted")
        # Structured numbers must keep their specific labels rather than being
        # swallowed by the generic phone pattern.
        self.assertIn("[REDACTED_CARD]", scrubbed)
        self.assertIn("[REDACTED_AADHAAR]", scrubbed)
        self.assertIn("[REDACTED_PAN]", scrubbed)
        self.assertEqual(data["redactionCount"], 7)

    def test_response_makes_no_encryption_claim(self):
        data = self._scrub("hello alice@example.com")
        self.assertNotIn("encryptionCMEK", data)

    def test_oversized_input_is_rejected(self):
        resp = self.client.post(
            "/api/security/scrub", json={"text": "a" * 20001}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 413, resp.text)


class TestMigrationFailsClosed(unittest.TestCase):
    """F-12: only a genuine duplicate-column condition is tolerated."""

    class _Cursor:
        def __init__(self, existing, alter_error=None):
            self.existing = existing
            self.alter_error = alter_error
            self._rows = []
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append(sql)
            if sql.startswith("SELECT 1 FROM information_schema"):
                self._rows = [(1,)] if self.existing else []
                return
            if sql.startswith("PRAGMA table_info"):
                self._rows = [("id",), ("content",)] if self.existing else []
                return
            if sql.startswith("ALTER TABLE") and self.alter_error:
                raise self.alter_error

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    def test_existing_column_is_a_noop(self):
        cur = self._Cursor(existing=True)
        _add_column_if_missing(cur, "memories", "content", "TEXT")
        self.assertFalse(any(s.startswith("ALTER") for s in cur.executed))

    def test_missing_column_is_added(self):
        cur = self._Cursor(existing=False)
        _add_column_if_missing(cur, "memories", "age", "INTEGER")
        self.assertTrue(any(s.startswith("ALTER") for s in cur.executed))

    def test_ddl_failure_propagates(self):
        cur = self._Cursor(existing=False, alter_error=PermissionError("must be owner"))
        with self.assertRaises(PermissionError):
            _add_column_if_missing(cur, "memories", "age", "INTEGER")

    def test_sqlite_real_table_gains_the_column(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        import sqlite3

        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            cur = conn.cursor()
            _add_column_if_missing(cur, "t", "extra", "TEXT")
            cols = [r[1] for r in cur.execute("PRAGMA table_info(t);").fetchall()]
            self.assertIn("extra", cols)
            # Idempotent second call.
            _add_column_if_missing(cur, "t", "extra", "TEXT")
        finally:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass


class TestFallbackCacheIsBounded(unittest.TestCase):
    """F-15: the offline cache cannot grow without limit."""

    def test_cache_evicts_oldest_entries(self):
        store = PGVectorMemoryStore.__new__(PGVectorMemoryStore)
        store._fallback_cache = {}
        for i in range(_FALLBACK_CACHE_MAX + 25):
            store._cache_fallback({"id": f"m{i}", "content": "x"})
        self.assertLessEqual(len(store._fallback_cache), _FALLBACK_CACHE_MAX)
        self.assertIn(f"m{_FALLBACK_CACHE_MAX + 24}", store._fallback_cache)


if __name__ == "__main__":
    unittest.main()
