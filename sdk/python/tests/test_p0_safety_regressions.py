"""P0-1/P0-2 regression tests: NULL-safe RRF SQL + pgvector field encryption.

P0-1: hybrid RRF must use COALESCE so a missing rank in one channel
contributes zero instead of producing a NULL combined score.
P0-2: combined protection — `content` stores Fernet ciphertext while
`content_plain` feeds the tsvector index; reads decrypt back to plaintext
and a wrong key fails safely.
"""
import unittest
from unittest.mock import MagicMock

from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)
from synapse_memory.core.pgvector_db import PGVectorMemoryStore


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass


def make_store(cursor):
    key = KeyManagementProvider.get_encryption_key()
    provider = FernetEncryptionProvider(key)
    store = PGVectorMemoryStore.__new__(PGVectorMemoryStore)
    store.connection_string = "postgresql://test"
    store.dimension = 3
    store.m = 16
    store.ef_construction = 64
    store.fail_closed = True
    store._pool = None
    store._connected = True
    store.encryption_provider = provider
    store._fallback_cache = {}
    conn = FakeConn(cursor)
    store._get_connection = lambda: conn  # type: ignore[method-assign]
    store._return_connection = lambda c: None  # type: ignore[method-assign]
    store._set_tenant_context = lambda cur, tid: None  # type: ignore[method-assign]
    store._handle_db_error = lambda op, e: (_ for _ in ()).throw(e)  # type: ignore[method-assign]
    return store


class TestNullSafeRRF(unittest.TestCase):
    def test_hybrid_sql_uses_coalesce_for_missing_ranks(self):
        cursor = FakeCursor(rows=[])
        store = make_store(cursor)
        store.hybrid_search_sql("hello", [0.1, 0.2, 0.3], top_k=5, tenant_id="t1")
        sql = cursor.executed[-1][0]
        self.assertIn("COALESCE(1.0 / (60 + d.rank), 0.0)", sql)
        self.assertIn("COALESCE(1.0 / (60 + l.rank), 0.0)", sql)


class TestPgvectorFieldEncryption(unittest.TestCase):
    def test_insert_writes_ciphertext_and_searchable_plaintext(self):
        cursor = FakeCursor()
        store = make_store(cursor)
        store.insert_memory({
            "id": "m1",
            "content": "secret plans",
            "category": "fact",
            "confidence": 0.9,
            "created_at": 1.0,
            "last_accessed_at": 1.0,
            "access_count": 0,
            "token_cost": 5,
            "feedback_multiplier": 1.0,
            "embedding": [0.1, 0.2, 0.3],
            "tenant_id": "t1",
        })
        sql, params = cursor.executed[-1]
        self.assertIn("content_plain", sql)
        stored_content = params[2]
        stored_plain = params[3]
        self.assertNotIn("secret plans", stored_content)
        self.assertEqual(stored_plain, "secret plans")

    def test_read_decrypts_content(self):
        key = KeyManagementProvider.get_encryption_key()
        provider = FernetEncryptionProvider(key)
        cursor = FakeCursor(rows=[{
            "id": "m1",
            "content": provider.encrypt("secret plans"),
            "content_plain": "secret plans",
            "tenant_id": "t1",
            "embedding": "[0.1, 0.2]",
        }])
        store = make_store(cursor)
        mem = store.get_memory_by_id("m1", tenant_id="t1")
        assert mem is not None
        self.assertEqual(mem["content"], "secret plans")
        # Ciphertext and search derivatives never leak as stored fields.
        raw = cursor.rows[0]["content"]
        self.assertNotEqual(raw, "secret plans")
        self.assertNotIn("search_vector", mem)

    def test_wrong_key_fails_safely(self):
        key = KeyManagementProvider.get_encryption_key()
        provider = FernetEncryptionProvider(key)
        cursor = FakeCursor(rows=[{
            "id": "m1",
            "content": provider.encrypt("secret plans"),
            "tenant_id": "t1",
        }])
        store = make_store(cursor)
        store.encryption_provider = FernetEncryptionProvider(
            FernetEncryptionProvider.generate_key()
        )
        with self.assertRaises(RuntimeError):
            store.get_memory_by_id("m1", tenant_id="t1")


if __name__ == "__main__":
    unittest.main()
