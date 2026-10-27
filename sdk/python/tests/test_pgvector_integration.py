import os
import time
import unittest
from typing import Any, Dict, List

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

from synapse_memory.core.encryption import FernetEncryptionProvider
from synapse_memory.core.pgvector_db import PGVectorMemoryStore


class TestPGVectorIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_PSYCOPG2:
            raise unittest.SkipTest("psycopg2 not installed; skipping PGVector integration tests.")
        cls.db_url = os.environ.get("DATABASE_URL")
        cls.app_url = os.environ.get("SYNAPSE_APP_DATABASE_URL") or cls.db_url
        if not cls.db_url or not cls.db_url.startswith("postgres"):
            raise unittest.SkipTest("DATABASE_URL environment variable is not configured for PostgreSQL; skipping integration tests.")

        # Ensure the vector extension is active. A least-privilege migration role
        # may lack CREATE EXTENSION (a DBA installs it); that is fine as long as it
        # is actually present, so verify existence instead of blindly skipping.
        conn = None
        try:
            conn = psycopg2.connect(cls.db_url)
            with conn.cursor() as cur:
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    conn.commit()
                except Exception:
                    conn.rollback()
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
                installed = cur.fetchone() is not None
        except Exception as e:
            raise unittest.SkipTest(f"Failed to connect to database: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        if not installed:
            raise unittest.SkipTest("pgvector extension is not installed in this database.")

    def _app_connection(self):
        return psycopg2.connect(self.app_url)

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
        conn = self._app_connection()
        try:
            with conn.cursor() as cur:
                # Set tenant context to tenant_a
                cur.execute("SET LOCAL app.current_tenant = 'tenant_a';")
                # Try inserting a record with tenant_id='tenant_b'
                with self.assertRaises(psycopg2.Error) as ctx:
                    cur.execute("""
                        INSERT INTO memories (id, tenant_id, content, content_plain, category, confidence, created_at, last_accessed_at, embedding)
                        VALUES ('mem_cross_tenant', 'tenant_b', 'Illegal write', 'Illegal write', 'fact', 0.9, %s, %s, %s);
                    """, (time.time(), time.time(), [0.1] * 384))
                    conn.commit()
                self.assertEqual(ctx.exception.pgcode, "42501")
        finally:
            conn.rollback()
            conn.close()

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

        conn = self._app_connection()
        try:
            with conn.cursor() as cur:
                # Set local app.current_tenant to empty
                cur.execute("SET LOCAL app.current_tenant = '';")
                cur.execute("SELECT * FROM memories;")
                rows = cur.fetchall()
                self.assertEqual(len(rows), 0)
        finally:
            conn.close()

    def test_rls_enforced_at_sql_level_bypassing_app(self):
        """Proves RLS blocks cross-tenant reads even when the app layer is bypassed.

        Inserts one row per tenant with a migration/admin connection, then runs
        plain SELECTs under each tenant's GUC context using the runtime role and asserts hard isolation.
        """
        tenants = {"tenant_rls_a": "RLS secret for tenant A", "tenant_rls_b": "RLS secret for tenant B"}
        for tid, text in tenants.items():
            self.store.insert_memory({
                "id": f"rls_{tid}",
                "content": text,
                "category": "personal",
                "confidence": 0.99,
                "created_at": time.time(),
                "embedding": [0.2] * 384,
                "tenant_id": tid,
            })

        conn = self._app_connection()
        try:
            with conn.cursor() as cur:
                # Tenant A context sees only its own row.
                cur.execute("SET LOCAL app.current_tenant = 'tenant_rls_a';")
                cur.execute("SELECT id FROM memories;")
                a_rows = [r[0] if isinstance(r, (tuple, list)) else r["id"] for r in cur.fetchall()]
                self.assertEqual(a_rows, ["rls_tenant_rls_a"])

                # Tenant B context sees only its own row.
                cur.execute("SET LOCAL app.current_tenant = 'tenant_rls_b';")
                cur.execute("SELECT id FROM memories;")
                b_rows = [r[0] if isinstance(r, (tuple, list)) else r["id"] for r in cur.fetchall()]
                self.assertEqual(b_rows, ["rls_tenant_rls_b"])

                # Empty context sees nothing at all.
                cur.execute("SET LOCAL app.current_tenant = '';")
                cur.execute("SELECT id FROM memories;")
                self.assertEqual(cur.fetchall(), [])
            conn.rollback()
        finally:
            conn.close()

    def test_rls_blocks_cross_tenant_vector_and_hybrid_search(self):
        """Vector + hybrid SQL search must never leak across tenants (HNSW path)."""
        for tid, mark in (("tenant_hyb_a", 1.0), ("tenant_hyb_b", -1.0)):
            self.store.insert_memory({
                "id": f"hyb_{tid}",
                "content": f"hybrid retrieval marker for {tid}",
                "category": "fact",
                "confidence": 0.95,
                "created_at": time.time(),
                "embedding": [mark] * 384,
                "tenant_id": tid,
            })

        a_hits = self.store.vector_search(query_vec=[1.0] * 384, top_k=10, tenant_id="tenant_hyb_a")
        self.assertEqual([m["id"] for m in a_hits], ["hyb_tenant_hyb_a"])

        a_hyb = self.store.hybrid_search_sql(
            query="hybrid retrieval marker",
            query_vec=[1.0] * 384,
            top_k=10,
            tenant_id="tenant_hyb_a",
        )
        self.assertEqual([m["id"] for m in a_hyb], ["hyb_tenant_hyb_a"])

        b_hits = self.store.vector_search(query_vec=[-1.0] * 384, top_k=10, tenant_id="tenant_hyb_b")
        self.assertEqual([m["id"] for m in b_hits], ["hyb_tenant_hyb_b"])

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
        self.assertIsNotNone(self.store.get_memory_by_id("dim_valid", tenant_id="default"))


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


class TestHybridSearchSQLPlaceholderOrder(unittest.TestCase):
    """Regression test for hybrid_search_sql positional placeholder order.

    The SQL text has different placeholder counts depending on whether a
    category filter is present, and psycopg2 binds %s positionally. If the
    params list drifts out of order, Postgres raises a type error (e.g. a
    category string bound to a ::vector slot) — or worse, silently fuses the
    wrong channels. This runs against a mock cursor so it executes in the
    plain unit-test CI job without a live database.
    """

    def _make_store_with_mock(self, captured: Dict[str, Any]) -> Any:
        store = PGVectorMemoryStore.__new__(PGVectorMemoryStore)
        # The SQL builder now selects the vector operator from configured width.
        # The query vectors in this placeholder-order test are four-dimensional.
        store.dimension = 4
        store._connected = True
        store._pool = None
        store._fallback_cache = {}

        def fake_set_tenant_context(cur: Any, tenant_id: Any) -> None:
            captured["tenant_id"] = tenant_id

        store._set_tenant_context = fake_set_tenant_context  # type: ignore[method-assign]

        class FakeCursor:
            def __init__(self) -> None:
                self.sql = ""
                self.params: Any = ()

            def execute(self, sql: str, params: Any = ()) -> None:
                self.sql = sql
                self.params = params

            def fetchall(self) -> List[Any]:
                return []

            def __enter__(self) -> "FakeCursor":
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

        fake_cur = FakeCursor()
        captured["cursor"] = fake_cur

        class FakeConn:
            def cursor(self, cursor_factory: Any = None) -> FakeCursor:
                return fake_cur

        fake_conn = FakeConn()
        store._get_connection = lambda: fake_conn  # type: ignore[method-assign]
        store._return_connection = lambda conn: None  # type: ignore[method-assign]
        store._handle_db_error = lambda op, e: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError(f"hybrid_search_sql unexpectedly failed ({op}): {e}")
        )
        store._mock_cursor = fake_cur  # type: ignore[attr-defined]
        return store

    def _expected_param_types(self, with_category: bool) -> List[str]:
        # Mirror of the CTE placeholder layout in hybrid_search_sql.
        types = ["emb"]  # dense rank
        if with_category:
            types.append("cat")  # dense filter
        types += ["emb", "limit"]  # dense order + limit
        types += ["query", "query"]  # lexical rank + where
        if with_category:
            types.append("cat")  # lexical filter
        types += ["query", "limit", "limit"]  # lexical order + limits
        return types

    def _check_binding(self, captured: Dict[str, Any], with_category: bool) -> None:
        cur = captured["cursor"]
        sql, params = cur.sql, list(cur.params)
        expected = self._expected_param_types(with_category)
        actual_placeholders = sql.count("%s")
        self.assertEqual(
            actual_placeholders, len(params),
            f"SQL has {actual_placeholders} placeholders but {len(params)} params",
        )
        self.assertEqual(
            len(params), len(expected),
            f"expected {len(expected)} params ({expected}), got {len(params)}",
        )
        for value, kind in zip(params, expected):
            if kind == "emb":
                self.assertIsInstance(value, str)
                self.assertTrue(
                    value.startswith("[") and value.endswith("]"),
                    f"embedding slot got non-vector value: {value!r}",
                )
            elif kind == "query":
                self.assertEqual(value, "hybrid retrieval marker")
            elif kind == "cat":
                self.assertEqual(value, "fact")
            elif kind == "limit":
                self.assertIsInstance(value, int)

    def test_placeholder_order_without_category(self) -> None:
        captured: Dict[str, Any] = {}
        store = self._make_store_with_mock(captured)
        store.hybrid_search_sql(
            query="hybrid retrieval marker",
            query_vec=[1.0] * 4,
            top_k=10,
            tenant_id="tenant_hyb_a",
        )
        self._check_binding(captured, with_category=False)

    def test_placeholder_order_with_category(self) -> None:
        captured: Dict[str, Any] = {}
        store = self._make_store_with_mock(captured)
        store.hybrid_search_sql(
            query="hybrid retrieval marker",
            query_vec=[1.0] * 4,
            top_k=10,
            category="fact",
            tenant_id="tenant_hyb_a",
        )
        self._check_binding(captured, with_category=True)


class TestRuntimeRoleIsolation(unittest.TestCase):
    """P0-5 acceptance proof using the ACTUAL least-privilege runtime role.

    PostgreSQL superusers and roles with BYPASSRLS bypass row-level security, and
    table owners bypass it unless FORCE ROW LEVEL SECURITY is set. Proving Row
    Level Security "with a database administrator" therefore proves nothing about
    production traffic. These tests connect as the real runtime role.

    Environment:
      DATABASE_URL               migration/admin DSN (runs DDL, seeds rows)
      SYNAPSE_APP_DATABASE_URL   runtime-role DSN (the subject under test)
      SYNAPSE_DB_RUNTIME_ROLE    default "synapse_app"
      SYNAPSE_DB_ADMIN_ROLE      default "synapse_admin"

    Provision both roles with ``scripts/provision_db_roles.py`` (the CI
    python-pgvector-integration-tests job does this). Skipped when the runtime
    DSN is absent so local runs without Postgres still pass.
    """

    KEY = "3pQ8Zm1vT4yW7cR2nB5kL9xJ6hG0dS8aF1uE4iO7pM2="
    TENANT_A = "runtime_role_tenant_a"
    TENANT_B = "runtime_role_tenant_b"

    @classmethod
    def setUpClass(cls):
        if not HAS_PSYCOPG2:
            raise unittest.SkipTest("psycopg2 not installed; skipping runtime-role tests.")
        cls.admin_url = os.environ.get("DATABASE_URL")
        cls.app_url = os.environ.get("SYNAPSE_APP_DATABASE_URL")
        if not cls.admin_url or not cls.admin_url.startswith("postgres"):
            raise unittest.SkipTest("DATABASE_URL is not configured; skipping runtime-role tests.")
        if not cls.app_url or not cls.app_url.startswith("postgres"):
            raise unittest.SkipTest(
                "SYNAPSE_APP_DATABASE_URL is not configured; run "
                "scripts/provision_db_roles.py and point it at the runtime-role DSN."
            )
        cls.runtime_role = os.environ.get("SYNAPSE_DB_RUNTIME_ROLE", "synapse_app")
        cls.admin_role = os.environ.get("SYNAPSE_DB_ADMIN_ROLE", "synapse_admin")

        # One explicit key shared by both stores: proves the runtime role reads
        # rows it is allowed to see, not that we got lucky with the default key.
        try:
            key = os.environ.get("SYNAPSE_ENCRYPTION_KEY") or cls.KEY
            cls.provider = FernetEncryptionProvider(key)
        except Exception as exc:  # pragma: no cover - only on a malformed key
            raise unittest.SkipTest(f"encryption key unusable: {exc}")

        cls.admin = PGVectorMemoryStore(
            connection_string=cls.admin_url, dimension=384, auto_init=True,
            fail_closed=True, encryption_provider=cls.provider)
        if not cls.admin.is_connected:
            raise unittest.SkipTest("admin connection unavailable; skipping runtime-role tests.")

    @classmethod
    def tearDownClass(cls):
        store = getattr(cls, "admin", None)
        if store is not None:
            try:
                store.clear_memories()
            except Exception:
                pass

    def setUp(self):
        self.admin.clear_memories()
        for mem in (
            self._mem("rt_a1", self.TENANT_A, "tenant A private runtime record one"),
            self._mem("rt_a2", self.TENANT_A, "tenant A private runtime record two"),
            self._mem("rt_b1", self.TENANT_B, "tenant B private runtime record"),
        ):
            self.admin.insert_memory(mem)

    def _mem(self, mem_id, tenant, content):
        return {
            "id": mem_id,
            "content": content,
            "category": "fact",
            "confidence": 0.9,
            "created_at": time.time(),
            "token_cost": 6,
            "embedding": [0.5] * 384,
            "tenant_id": tenant,
        }

    def _runtime_store(self):
        """Real engine code path, connected as the DML-only runtime role.

        apply_schema=False is required (and is the documented production mode):
        a non-owner runtime role cannot execute DDL.
        """
        store = PGVectorMemoryStore(
            connection_string=self.app_url, dimension=384, fail_closed=True,
            apply_schema=False, encryption_provider=self.provider)
        self.assertTrue(store.is_connected,
                        "runtime role could not connect/verify schema; did "
                        "scripts/provision_db_roles.py run?")
        return store

    def _raw_rows(self, tenant_context=None):
        """Read `memories` over a raw runtime-role connection (app layer bypassed)."""
        conn = psycopg2.connect(self.app_url)
        try:
            with conn.cursor() as cur:
                if tenant_context is not None:
                    cur.execute("SET LOCAL app.current_tenant = %s;", (tenant_context,))
                cur.execute("SELECT id FROM memories;")
                return sorted(r[0] for r in cur.fetchall())
        finally:
            conn.close()
    def test_runtime_role_is_a_non_owner_without_bypassrls(self):
        """The role the engine connects as must be structurally incapable of
        bypassing row-level security."""
        conn = psycopg2.connect(self.app_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user;")
                self.assertEqual(cur.fetchone()[0], self.runtime_role)
                cur.execute(
                    "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                    "FROM pg_roles WHERE rolname = %s;", (self.runtime_role,))
                row = cur.fetchone()
                self.assertIsNotNone(row, "runtime role does not exist")
                self.assertFalse(row[0], "runtime role must not be a SUPERUSER")
                self.assertFalse(row[1], "runtime role must not have BYPASSRLS")
                self.assertFalse(row[2], "runtime role must not have CREATEROLE")
                self.assertFalse(row[3], "runtime role must not have CREATEDB")
                cur.execute(
                    "SELECT tableowner FROM pg_tables "
                    "WHERE schemaname='public' AND tablename='memories';")
                owner = cur.fetchone()[0]
                self.assertNotEqual(owner, self.runtime_role,
                                    "runtime role must NOT own memories")
                cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE');",
                            (self.runtime_role,))
                self.assertFalse(cur.fetchone()[0],
                                 "runtime role must not hold schema CREATE (DDL)")
        finally:
            conn.close()

    def test_runtime_role_denied_on_destructive_and_ddl_operations(self):
        """Privilege separation: runtime is DML-only, never destructive/DDL."""
        conn = psycopg2.connect(self.app_url)
        try:
            with conn.cursor() as cur:
                for priv in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                    cur.execute(
                        "SELECT has_table_privilege(%s, 'public.memories', %s);",
                        (self.runtime_role, priv))
                    self.assertFalse(cur.fetchone()[0],
                                     "runtime role must not hold " + priv)
                for ddl in ("ALTER TABLE memories ADD COLUMN rt_probe INTEGER;",
                            "DROP TABLE memories;",
                            "TRUNCATE TABLE memories;"):
                    with self.assertRaises(psycopg2.Error, msg=ddl):
                        cur.execute(ddl)
                    conn.rollback()
        finally:
            conn.close()


    def test_runtime_role_raw_sql_cannot_read_another_tenant(self):
        """Isolation holds even with the application layer completely bypassed."""
        # No tenant context at all -> fail closed, zero rows.
        self.assertEqual(self._raw_rows(), [])
        # Explicitly scoped to B -> only B.
        self.assertEqual(self._raw_rows(self.TENANT_B), ["rt_b1"])
        # Explicitly scoped to A -> only A.
        self.assertEqual(self._raw_rows(self.TENANT_A), ["rt_a1", "rt_a2"])
        # Empty-string context -> zero rows (never "everything").
        self.assertEqual(self._raw_rows(""), [])

    def test_runtime_store_reads_only_its_own_tenant(self):
        store = self._runtime_store()
        self.assertEqual(len(store.get_all_memories(tenant_id=self.TENANT_A)), 2)
        self.assertEqual(len(store.get_all_memories(tenant_id=self.TENANT_B)), 1)
        self.assertEqual(store.get_all_memories(tenant_id="tenant_with_nothing"), [])
        self.assertIsNone(store.get_memory_by_id("rt_b1", tenant_id=self.TENANT_A))
        self.assertIsNotNone(store.get_memory_by_id("rt_a1", tenant_id=self.TENANT_A))
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_A), 2)
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_B), 1)
        # Category-scoped reads and searches must stay inside the tenant too.
        self.assertEqual(
            len(store.get_memories_by_category("fact", tenant_id=self.TENANT_A)), 2)
        self.assertEqual(
            len(store.vector_search([0.5] * 384, top_k=10,
                                    tenant_id=self.TENANT_A)), 2)
        self.assertEqual(
            len(store.lexical_search("runtime", top_k=10,
                                     tenant_id=self.TENANT_B)), 1)
        self.assertEqual(
            len(store.query_candidates(tenant_id=self.TENANT_A, limit=50)), 2)

    def test_runtime_store_cross_tenant_write_rejected(self):
        """A write whose row tenant does not match the session context is refused
        by the RLS WITH CHECK clause, not merely by application logic."""
        conn = psycopg2.connect(self.app_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s;", (self.TENANT_A,))
                with self.assertRaises(psycopg2.Error) as ctx:
                    cur.execute(
                        "INSERT INTO memories (id, tenant_id, content, content_plain, "
                        "category, confidence, created_at, last_accessed_at, "
                        "access_count, token_cost, feedback_multiplier) "
                        "VALUES ('rt_evil', %s, 'x', 'x', 'fact', 0.9, %s, %s, 0, 1, 1.0);",
                        (self.TENANT_B, time.time(), time.time()))
                    conn.commit()
                self.assertEqual(ctx.exception.pgcode, "42501")
            conn.rollback()
        finally:
            conn.close()
        # The rejected row must not exist for anyone.
        self.assertEqual(self._raw_rows(self.TENANT_B), ["rt_b1"])


    def test_runtime_store_writes_and_deletes_stay_tenant_scoped(self):
        store = self._runtime_store()
        store.insert_memory(self._mem("rt_a3", self.TENANT_A, "new tenant A record"))
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_A), 3)
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_B), 1)
        store.update_access_count("rt_a3", 1, tenant_id=self.TENANT_A)
        store.update_confidence("rt_a3", 0.5, tenant_id=self.TENANT_A)
        # A scoped clear must not touch another tenant's rows.
        deleted = store.clear_memories(tenant_id=self.TENANT_A)
        self.assertEqual(deleted, 3)
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_A), 0)
        self.assertEqual(store.count_memories(tenant_id=self.TENANT_B), 1)

    def test_admin_access_requires_admin_role_membership(self):
        """Unscoped access must be role-gated, not GUC-gatable.

        An ordinary unauthorized SELECT under RLS filters out rows (asserts zero rows).
        Cross-tenant INSERT or UPDATE violates the WITH CHECK policy and raises SQLSTATE 42501.
        """
        # Raw connection as runtime role (no tenant context set)
        conn = psycopg2.connect(self.app_url)
        try:
            with conn.cursor() as cur:
                # 1. Unauthorized SELECT with empty tenant context returns zero rows
                cur.execute("SET LOCAL app.current_tenant = '';")
                cur.execute(
                    "SELECT 1 FROM memories WHERE tenant_id IS NULL "
                    "OR tenant_id = 'spoofed' LIMIT 1;"
                )
                self.assertEqual(cur.fetchall(), [])

                # 2. Attempting to set admin bypass flag as runtime role does not bypass RLS
                cur.execute("SET LOCAL app.admin_bypass_rls = 'on';")
                cur.execute("SELECT id FROM memories;")
                self.assertEqual(cur.fetchall(), [])

                # 3. Cross-tenant write violates WITH CHECK and raises SQLSTATE 42501 (InsufficientPrivilege)
                with self.assertRaises(psycopg2.Error) as ctx:
                    cur.execute(
                        "INSERT INTO memories (id, tenant_id, content, content_plain, "
                        "category, confidence, created_at, last_accessed_at, "
                        "access_count, token_cost, feedback_multiplier) "
                        "VALUES ('rt_unauth', 'spoofed', 'x', 'x', 'fact', 0.9, %s, %s, 0, 1, 1.0);",
                        (time.time(), time.time())
                    )
                self.assertEqual(ctx.exception.pgcode, "42501")
        finally:
            conn.close()

    def test_runtime_start_refuses_to_run_without_provisioned_schema(self):
        """apply_schema=False with a missing schema must fail closed rather than
        silently degrading to the in-memory cache (which would fake write
        success and lose data on restart)."""
        admin_conn = psycopg2.connect(self.admin_url)
        try:
            admin_conn.autocommit = True
            with admin_conn.cursor() as cur:
                cur.execute("ALTER TABLE memories RENAME TO memories_hidden;")
            try:
                with self.assertRaises(RuntimeError) as caught:
                    PGVectorMemoryStore(
                        connection_string=self.app_url, dimension=384,
                        fail_closed=True, apply_schema=False,
                        encryption_provider=self.provider)
                self.assertIn("memories", str(caught.exception))
            finally:
                with admin_conn.cursor() as cur:
                    cur.execute("ALTER TABLE memories_hidden RENAME TO memories;")
        finally:
            admin_conn.close()
        # The renamed table is back and readable again.
        self.assertEqual(self._raw_rows(self.TENANT_A), ["rt_a1", "rt_a2"])
