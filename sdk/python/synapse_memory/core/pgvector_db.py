"""
Production PostgreSQL + pgvector + HNSW Memory Store with Database-Level Multi-Tenancy
and PostgreSQL Row-Level Security (RLS).
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)

logger = logging.getLogger("PGVectorMemoryStore")


def secrets_token_hex(n: int = 6) -> str:
    """Unique suffix for named/server-side cursors (avoids psycopg2 cursor name clashes)."""
    import secrets
    return secrets.token_hex(n)


try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    try:
        import psycopg as psycopg2  # type: ignore
        from psycopg.rows import dict_row as RealDictCursor  # type: ignore
        pool = None  # type: ignore
        HAS_PSYCOPG2 = True
    except ImportError:
        HAS_PSYCOPG2 = False
        psycopg2 = None  # type: ignore
        pool = None  # type: ignore
        RealDictCursor = None  # type: ignore


class PGVectorMemoryStore:
    """
    Enterprise-grade Postgres + pgvector + HNSW storage engine.
    Executes vector similarity search directly in SQL using HNSW graph indexes
    with database-level multi-tenancy, Row-Level Security (RLS), and payload encryption.
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        dimension: int = 128,
        m: int = 16,
        ef_construction: int = 64,
        encryption_provider: Optional[Any] = None,
        auto_init: bool = True,
        fail_closed: bool = False,
        apply_schema: bool = True,
        admin_role: str = "synapse_admin",
    ):
        # SECURITY: never ship a default password. Require explicit DATABASE_URL in prod.
        # Local dev convenience default uses an obviously-insecure placeholder and logs a warning.
        self.connection_string = connection_string or os.environ.get(
            "DATABASE_URL", "postgresql://synapse_admin:CHANGEME_dev_only@127.0.0.1:5432/synapse_cognitive"
        )
        if "CHANGEME" in self.connection_string:
            logger.warning(
                "Using insecure dev-only Postgres credentials. Set DATABASE_URL with a strong password for any shared deployment."
            )
        self.dimension = dimension
        self.m = m
        self.ef_construction = ef_construction
        self.fail_closed = fail_closed
        # apply_schema=False = "migrate-then-run" production topology: DDL and the
        # migration ledger are owned by a separate migration role/step, and the
        # engine connects with a least-privilege runtime role that has DML only.
        # See scripts/provision_db_roles.py. Default True preserves the
        # single-role local/dev behaviour.
        self.apply_schema = apply_schema
        # Admin role whose current_user bypasses RLS in tenant_isolation_policy.
        # Gated on current_user (not a settable GUC) so a compromised runtime DSN
        # cannot set `app.current_tenant` to a sentinel and read all tenants.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", admin_role):
            raise ValueError(f"admin_role must be a safe identifier, got: {admin_role!r}")
        self.admin_role = admin_role
        self._pool: Optional[Any] = None
        self._connected = False

        if encryption_provider is not None:
            self.encryption_provider = encryption_provider
        else:
            key = KeyManagementProvider.get_encryption_key()
            self.encryption_provider = FernetEncryptionProvider(key)

        # In-memory shadow cache for local development/testing fallback
        self._fallback_cache: Dict[str, Dict[str, Any]] = {}

        if HAS_PSYCOPG2 and auto_init:
            self._connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def pool_stats(self) -> Dict[str, int]:
        """Live pool utilization for /metrics (no secrets)."""
        if self._pool is None:
            return {"pool_size": 0, "in_use": 0}
        try:
            # psycopg2 pools track min/max; used = checked-out count via internal queue.
            maxconn = getattr(self._pool, "maxconn", 10)
            used = maxconn - len(getattr(self._pool, "_pool", []))
            return {"pool_size": int(maxconn), "in_use": max(0, int(used))}
        except Exception:
            return {"pool_size": 0, "in_use": 0}

    def _connect(self) -> None:
        try:
            if pool is not None:
                # Thread-safe pool: FastAPI handles requests on worker threads.
                pool_cls = getattr(pool, "ThreadedConnectionPool", None) or pool.SimpleConnectionPool
                self._pool = pool_cls(
                    1, 10, self.connection_string, connect_timeout=5
                )
            self._connected = True
            if self.apply_schema:
                self._initialize_database()
            else:
                # Runtime-role topology: the operator already ran DDL + migrations.
                # Verify that, so a missing table fails loudly instead of silently
                # serving from the in-memory fallback cache.
                self._verify_schema_present()
            logger.info("PGVectorMemoryStore successfully connected to PostgreSQL.")
        except Exception as e:
            self._connected = False
            if self.fail_closed:
                logger.error(f"PostgreSQL/pgvector connection failed in fail-closed mode: {e}")
                raise RuntimeError(f"PostgreSQL connection failed: {e}")
            logger.warning(
                f"PostgreSQL/pgvector connection unviable ({e}); operating in fallback mode."
            )

    def _verify_schema_present(self) -> None:
        """Read-only guard for the ``apply_schema=False`` (runtime-role) topology.

        When the engine runs as a least-privilege DML-only role it must not attempt
        DDL, so we cannot create the schema on start-up. Instead we prove the
        schema was provisioned by the migration step; if ``memories`` is absent we
        raise rather than silently degrading to the in-memory fallback cache
        (which would fake successful writes and lose data on restart).
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.memories') IS NOT NULL;")
                row = cur.fetchone()
                present = bool(row[0]) if row else False
            if not present:
                raise RuntimeError(
                    "Schema not provisioned: table 'memories' is missing. Run the "
                    "migration step (SQLite/Postgres DDL + migrations) before starting "
                    "the engine with apply_schema=False."
                )
        finally:
            self._return_connection(conn)

    def _initialize_database(self) -> None:
        if not self._connected or not HAS_PSYCOPG2:
            return
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                # 1. Enable extensions. On a least-privilege migration role these
                # may already be installed by a DBA; CREATE EXTENSION needs
                # superuser for non-trusted extensions, so treat it as deferrable
                # rather than fatal (mirrors the FTS block below).
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                except Exception as ext_err:
                    logger.warning(
                        "Extension initialization deferred (install `vector` and "
                        "`pg_trgm` as a DBA if this role lacks CREATE EXTENSION): %s",
                        ext_err,
                    )
                    conn.rollback()

                # 2. Main cognitive memories table with tenant_id
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS memories (
                        id VARCHAR(128) PRIMARY KEY,
                        tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
                        content TEXT NOT NULL,
                        category VARCHAR(64) NOT NULL,
                        confidence REAL NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL,
                        last_accessed_at DOUBLE PRECISION NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        token_cost INTEGER NOT NULL DEFAULT 0,
                        feedback_multiplier REAL NOT NULL DEFAULT 1.0,
                        embedding vector({self.dimension})
                    );
                """)

                # 3. Column migration if table already exists
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='memories' AND column_name='tenant_id'
                        ) THEN
                            ALTER TABLE memories ADD COLUMN tenant_id VARCHAR(128) NOT NULL DEFAULT 'default';
                        END IF;
                    END $$;
                """)

                # 3b. Vector dimension migration if column dimension differs from configured target
                try:
                    cur.execute("""
                        SELECT atttypmod FROM pg_attribute
                        WHERE attrelid = 'memories'::regclass AND attname = 'embedding';
                    """)
                    row = cur.fetchone()
                    if row:
                        current_dim = row[0] if isinstance(row, (tuple, list)) else row.get("atttypmod")
                        if current_dim and current_dim > 0 and current_dim != self.dimension:
                            logger.warning(
                                f"Migrating PostgreSQL 'memories.embedding' column dimension from {current_dim} to {self.dimension}..."
                            )
                            cur.execute("DROP INDEX IF EXISTS idx_memories_hnsw;")
                            cur.execute(f"ALTER TABLE memories ALTER COLUMN embedding TYPE vector({self.dimension});")
                except Exception as mig_err:
                    logger.warning(f"Vector dimension check/migration skipped: {mig_err}")

                # 4. HNSW vector index
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_memories_hnsw
                    ON memories USING hnsw (embedding vector_cosine_ops)
                    WITH (m = {self.m}, ef_construction = {self.ef_construction});
                """)

                # 5. Relational and tenant indexes
                cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_tenant ON memories(tenant_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_tenant_cat ON memories(tenant_id, category);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_cat ON memories(category);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_access ON memories(last_accessed_at DESC);")

                # 5b. Combined protection: `content` holds Fernet ciphertext;
                # `content_plain` is the searchable projection feeding the
                # tsvector index. Threat model: volume/database encryption
                # protects everything at rest; field encryption protects the
                # authoritative content column. Embeddings + tsvector remain
                # readable derivatives by design (required for HNSW + BM25).
                try:
                    cur.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='memories' AND column_name='content_plain'
                            ) THEN
                                ALTER TABLE memories ADD COLUMN content_plain TEXT NOT NULL DEFAULT '';
                            END IF;
                        END $$;
                    """)
                    cur.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='memories' AND column_name='search_vector'
                            ) THEN
                                ALTER TABLE memories
                                ADD COLUMN search_vector tsvector
                                GENERATED ALWAYS AS (to_tsvector('english', content_plain)) STORED;
                            END IF;
                        END $$;
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_memories_search_vector
                        ON memories USING GIN(search_vector);
                    """)
                except Exception as fts_err:
                    logger.warning(f"Full-Text Search column initialization deferred: {fts_err}")

                # 6. Database Row-Level Security (RLS)
                cur.execute("ALTER TABLE memories ENABLE ROW LEVEL SECURITY;")
                cur.execute("ALTER TABLE memories FORCE ROW LEVEL SECURITY;")
                # Build a SQL string literal for the admin role. current_user is
                # evaluated at query time; only the actual admin DB role bypasses
                # RLS. A settable GUC sentinel can no longer be abused by a
                # compromised runtime DSN.
                _admin_role_sql = "current_user = '" + self.admin_role.replace("'", "''") + "'"
                _rls_sql = (
                    "DO $$\n"
                    "BEGIN\n"
                    "    IF NOT EXISTS (\n"
                    "        SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'tenant_isolation_policy'\n"
                    "    ) THEN\n"
                    "        CREATE POLICY tenant_isolation_policy ON memories\n"
                    "        FOR ALL\n"
                    "        USING (\n"
                    "            (\n"
                    "                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')\n"
                    "                AND tenant_id IS NOT NULL\n"
                    "            )\n"
                    "            OR " + _admin_role_sql + "\n"
                    "        )\n"
                    "        WITH CHECK (\n"
                    "            (\n"
                    "                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')\n"
                    "                AND tenant_id IS NOT NULL\n"
                    "            )\n"
                    "            OR " + _admin_role_sql + "\n"
                    "        );\n"
                    "    ELSE\n"
                    "        -- Migrate legacy policy (no admin bypass) to the current version.\n"
                    "        DROP POLICY IF EXISTS tenant_isolation_policy ON memories;\n"
                    "        CREATE POLICY tenant_isolation_policy ON memories\n"
                    "        FOR ALL\n"
                    "        USING (\n"
                    "            (\n"
                    "                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')\n"
                    "                AND tenant_id IS NOT NULL\n"
                    "            )\n"
                    "            OR " + _admin_role_sql + "\n"
                    "        )\n"
                    "        WITH CHECK (\n"
                    "            (\n"
                    "                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')\n"
                    "                AND tenant_id IS NOT NULL\n"
                    "            )\n"
                    "            OR " + _admin_role_sql + "\n"
                    "        );\n"
                    "    END IF;\n"
                    "END $$;\n"
                )
                cur.execute(_rls_sql)

                # Verify the current DB session's role membership. Error out with
                # a mapped 403 TenantIsolationError if the engine is running as a
                # role that is NOT a member of the configured admin role, so a
                # misconfigured runtime DSN cannot silently gain unscoped access.
                _verify_role_sql = (
                    "DO $$\n"
                    "BEGIN\n"
                    "    IF NOT pg_has_role(current_user, '" + self.admin_role.replace("'", "''") + "', 'MEMBER') THEN\n"
                    "        RAISE EXCEPTION 'security: current_user % is not a member of admin role %', current_user, '" + self.admin_role.replace("'", "''") + "' USING\n"
                    "            ERRCODE = 'P0001', -- internal_error (mapped to TenantIsolationError)\n"
                    "            MESSAGE = 'Forbidden: unscoped admin access requires membership in admin role';\n"
                    "    END IF;\n"
                    "END $$;\n"
                )
                cur.execute(_verify_role_sql)

                conn.commit()
        except Exception as e:
            conn.rollback()
            if self.fail_closed:
                raise RuntimeError(
                    f"PostgreSQL/pgvector initialization failed: {e}"
                ) from e
            logger.warning(
                f"PostgreSQL initialization failed; using fallback mode: {e}"
            )
        finally:
            self._return_connection(conn)

        # Apply forward-only schema migrations (embedding_dimension / model_version
        # + migration ledger). Versioned + idempotent across restarts.
        try:
            from synapse_memory.core.migrations import run_migrations
            run_migrations(
                self._get_connection,
                is_postgres=True,
                fail_closed=self.fail_closed,
                owns_connection=False,
            )
        except Exception as mig_err:
            logger.warning(f"Schema migrations deferred: {mig_err}")

    def _handle_db_error(self, operation: str, error: Exception) -> None:
        if self.fail_closed:
            raise RuntimeError(
                f"PostgreSQL operation failed: {operation}: {error}"
            ) from error
        logger.error(
            "PostgreSQL operation failed: %s: %s",
            operation,
            error
        )

    def _encrypt_field(self, plaintext: str) -> str:
        try:
            return self.encryption_provider.encrypt(plaintext)
        except Exception as e:
            logger.error("Field encryption failed: %s", e)
            raise RuntimeError(f"Field encryption failed: {e}") from e

    def _decrypt_field(self, ciphertext: str) -> str:
        try:
            return self.encryption_provider.decrypt(ciphertext)
        except Exception as e:
            logger.error("Field decryption failed (wrong key or corrupt payload).")
            raise RuntimeError("Field decryption failed: wrong key or corrupt payload.") from e

    def _decrypt_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        mem = dict(row)
        content = mem.get("content")
        if isinstance(content, str) and content:
            try:
                mem["content"] = self._decrypt_field(content)
            except Exception:
                # Fail safe: do not return ciphertext as if it were plaintext.
                raise
        if isinstance(mem.get("embedding"), str):
            try:
                mem["embedding"] = json.loads(mem["embedding"])
            except Exception:
                pass
        mem.pop("search_vector", None)
        return mem

    def _get_connection(self) -> Any:
        if self._pool is not None:
            return self._pool.getconn()
        if psycopg2 is not None:
            return psycopg2.connect(self.connection_string)
        raise RuntimeError("psycopg2 driver not installed.")

    def _return_connection(self, conn: Any) -> None:
        if self._pool is not None:
            try:
                # Roll back any open transaction so SET LOCAL tenant context
                # and failed statements never leak into the next checkout.
                conn.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        else:
            try:
                conn.close()
            except Exception:
                pass

    def _set_tenant_context(self, cur: Any, tenant_id: Optional[str]) -> None:
        # None / "*" means admin (unscoped) access: the RLS policy gates the
        # admin bypass on current_user = <admin_role>, not on a settable GUC
        # sentinel. Empty string means "no tenant" and must see zero rows
        # (fail-closed isolation).
        if tenant_id is None or tenant_id == "*":
            ctx = ""
        else:
            ctx = tenant_id
        cur.execute("SET LOCAL app.current_tenant = %s;", (ctx,))

    def insert_memory(self, memory: Dict[str, Any]) -> None:
        """Inserts memory with embedding vector into pgvector table with tenant isolation.

        Combined protection: `content` stores Fernet ciphertext;
        `content_plain` stores the searchable projection for tsvector/BM25.
        """
        tenant_id = memory.get("tenant_id") or memory.get("tenantId") or "default"
        mem_copy = memory.copy()
        mem_copy["tenant_id"] = tenant_id
        self._fallback_cache[memory["id"]] = mem_copy

        if not self._connected or not HAS_PSYCOPG2:
            return

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                emb = memory.get("embedding", [])
                emb_str = f"[{','.join(str(float(x)) for x in emb)}]" if emb else None
                plaintext = str(memory["content"])
                ciphertext = self._encrypt_field(plaintext)
                cur.execute("""
                    INSERT INTO memories (
                        id, tenant_id, content, content_plain, category, confidence, created_at,
                        last_accessed_at, access_count, token_cost,
                        feedback_multiplier, embedding, embedding_dimension, model_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        content = EXCLUDED.content,
                        content_plain = EXCLUDED.content_plain,
                        category = EXCLUDED.category,
                        confidence = EXCLUDED.confidence,
                        last_accessed_at = EXCLUDED.last_accessed_at,
                        access_count = EXCLUDED.access_count,
                        token_cost = EXCLUDED.token_cost,
                        feedback_multiplier = EXCLUDED.feedback_multiplier,
                        embedding = EXCLUDED.embedding,
                        embedding_dimension = EXCLUDED.embedding_dimension,
                        model_version = EXCLUDED.model_version;
                """, (
                    memory["id"],
                    tenant_id,
                    ciphertext,
                    plaintext,
                    memory["category"],
                    memory["confidence"],
                    memory["created_at"],
                    memory.get("last_accessed_at", time.time()),
                    memory.get("access_count", 0),
                    memory.get("token_cost", 0),
                    memory.get("feedback_multiplier", 1.0),
                    emb_str,
                    memory.get("embedding_dimension", len(emb or [])),
                    memory.get("model_version", "unknown")
                ))
                conn.commit()
        except Exception as e:
            conn.rollback()
            self._handle_db_error("insert_memory", e)
        finally:
            self._return_connection(conn)

    def get_memory_by_id(self, memory_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self._connected or not HAS_PSYCOPG2:
            mem = self._fallback_cache.get(memory_id)
            if mem and tenant_id and mem.get("tenant_id") != tenant_id:
                return None
            return mem

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("SELECT * FROM memories WHERE id = %s AND tenant_id = %s", (memory_id, tenant_id))
                else:
                    cur.execute("SELECT * FROM memories WHERE id = %s", (memory_id,))
                row = cur.fetchone()
                if row:
                    return self._decrypt_row(dict(row))
                return None
        except Exception as e:
            self._handle_db_error("get_memory", e)
            return None
        finally:
            self._return_connection(conn)

    def get_memories_by_category(self, category: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self._connected or not HAS_PSYCOPG2:
            return [
                m for m in self._fallback_cache.values()
                if m.get("category") == category and (tenant_id is None or m.get("tenant_id") == tenant_id)
            ]

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("SELECT * FROM memories WHERE category = %s AND tenant_id = %s", (category, tenant_id))
                else:
                    cur.execute("SELECT * FROM memories WHERE category = %s", (category,))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    results.append(self._decrypt_row(dict(r)))
                return results
        except Exception as e:
            self._handle_db_error("get_memories_by_category", e)
            return []
        finally:
            self._return_connection(conn)

    def update_access_count(self, memory_id: str, count_increment: int = 1, tenant_id: Optional[str] = None) -> None:
        if memory_id in self._fallback_cache:
            self._fallback_cache[memory_id]["access_count"] = (
                self._fallback_cache[memory_id].get("access_count", 0) + count_increment
            )
            self._fallback_cache[memory_id]["last_accessed_at"] = time.time()

        if not self._connected or not HAS_PSYCOPG2:
            return

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("""
                        UPDATE memories
                        SET access_count = access_count + %s, last_accessed_at = %s
                        WHERE id = %s AND tenant_id = %s
                    """, (count_increment, time.time(), memory_id, tenant_id))
                else:
                    cur.execute("""
                        UPDATE memories
                        SET access_count = access_count + %s, last_accessed_at = %s
                        WHERE id = %s
                    """, (count_increment, time.time(), memory_id))
                conn.commit()
        except Exception as e:
            conn.rollback()
            self._handle_db_error("update_access_count", e)
        finally:
            self._return_connection(conn)

    def update_confidence(self, memory_id: str, new_confidence: float, tenant_id: Optional[str] = None) -> None:
        if memory_id in self._fallback_cache:
            self._fallback_cache[memory_id]["confidence"] = new_confidence

        if not self._connected or not HAS_PSYCOPG2:
            return

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("UPDATE memories SET confidence = %s WHERE id = %s AND tenant_id = %s", (new_confidence, memory_id, tenant_id))
                else:
                    cur.execute("UPDATE memories SET confidence = %s WHERE id = %s", (new_confidence, memory_id))
                conn.commit()
        except Exception as e:
            conn.rollback()
            self._handle_db_error("update_confidence", e)
        finally:
            self._return_connection(conn)

    def vector_search(
        self,
        query_vec: List[float],
        top_k: int = 10,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes native pgvector cosine distance search using HNSW index:
        ORDER BY embedding <=> query_vec::vector LIMIT top_k
        """
        if not self._connected or not HAS_PSYCOPG2:
            results = []
            for mem in self._fallback_cache.values():
                if tenant_id and mem.get("tenant_id") != tenant_id:
                    continue
                if category and mem.get("category") != category:
                    continue
                v = mem.get("embedding", [])
                if v and len(v) == len(query_vec):
                    dot = sum(a * b for a, b in zip(query_vec, v))
                    mag_a = sum(a * a for a in query_vec) ** 0.5
                    mag_b = sum(b * b for b in v) ** 0.5
                    sim = dot / (mag_a * mag_b) if mag_a * mag_b > 0 else 0.0
                else:
                    sim = 0.0
                mem_copy = mem.copy()
                mem_copy["cosine_similarity"] = sim
                mem_copy["relevance_score"] = sim
                results.append(mem_copy)
            results.sort(key=lambda x: x["cosine_similarity"], reverse=True)
            return results[:top_k]

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                emb_str = f"[{','.join(str(float(x)) for x in query_vec)}]"

                conditions = []
                params: List[Any] = [emb_str]
                if tenant_id:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                if category:
                    conditions.append("category = %s")
                    params.append(category)

                where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.extend([emb_str, top_k])

                query = f"""
                    SELECT id, tenant_id, content, category, confidence, created_at,
                           last_accessed_at, access_count, token_cost, feedback_multiplier,
                           1 - (embedding <=> %s::vector) AS cosine_similarity
                    FROM memories
                    {where_clause}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    mem = self._decrypt_row(dict(r))
                    mem["relevance_score"] = mem.get("cosine_similarity", 0.0)
                    results.append(mem)
                return results
        except Exception as e:
            self._handle_db_error("vector_search", e)
            return []
        finally:
            self._return_connection(conn)

    def lexical_search(
        self,
        query: str,
        top_k: int = 100,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes native PostgreSQL Full-Text Search ranking using GIN tsvector index:
        ORDER BY ts_rank(search_vector, plainto_tsquery('english', query)) DESC
        """
        if not self._connected or not HAS_PSYCOPG2:
            query_terms = set(query.lower().split())
            results = []
            for mem in self._fallback_cache.values():
                if tenant_id and mem.get("tenant_id") != tenant_id:
                    continue
                if category and mem.get("category") != category:
                    continue
                words = set(mem.get("content", "").lower().split())
                overlap = len(query_terms.intersection(words))
                if overlap > 0:
                    mem_copy = mem.copy()
                    mem_copy["lexical_rank"] = float(overlap) / max(1.0, float(len(query_terms)))
                    results.append(mem_copy)
            results.sort(key=lambda x: x.get("lexical_rank", 0.0), reverse=True)
            return results[:top_k]

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                # SQL text order of %s placeholders:
                # 1. SELECT ts_rank's plainto_tsquery  -> query
                # 2. WHERE @@ plainto_tsquery          -> query
                # 3. WHERE tenant_id                   -> tenant_id (if present)
                # 4. WHERE category                    -> category (if present)
                # 5. LIMIT                             -> top_k
                conditions = ["search_vector @@ plainto_tsquery('english', %s)"]
                params: List[Any] = [query, query]
                if tenant_id:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                if category:
                    conditions.append("category = %s")
                    params.append(category)

                where_clause = "WHERE " + " AND ".join(conditions)

                sql = f"""
                    SELECT id, tenant_id, content, category, confidence, created_at,
                           last_accessed_at, access_count, token_cost, feedback_multiplier,
                           ts_rank(search_vector, plainto_tsquery('english', %s)) AS lexical_rank
                    FROM memories
                    {where_clause}
                    ORDER BY lexical_rank DESC
                    LIMIT %s
                """
                params.append(top_k)
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                out = []
                for r in rows:
                    mem = self._decrypt_row(dict(r))
                    mem["relevance_score"] = float(mem.get("lexical_rank", 0.0))
                    out.append(mem)
                return out
        except Exception as e:
            self._handle_db_error("lexical_search", e)
            return []
        finally:
            self._return_connection(conn)

    def hybrid_search_sql(
        self,
        query: str,
        query_vec: List[float],
        top_k: int = 25,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None,
        candidate_pool: int = 100,
    ) -> List[Dict[str, Any]]:
        """Single-query hybrid retrieval pushed down into PostgreSQL.

        Fuses dense HNSW (vector_cosine) + lexical ts_rank(tsvector) using
        Reciprocal Rank Fusion computed in SQL. This is the scale path: instead
        of loading the whole corpus into Python (O(N)), Postgres returns only the
        top `candidate_pool` per channel and we RRF-fuse the union. Drops p95
        from ~150ms@5k (Python scan) to tens of ms at 100k+.

        Returns rows with `relevance_score` (RRF) + `explanation` breakdown.
        Falls back to [] when offline so callers can use the Python engine.
        """
        if not self._connected or not HAS_PSYCOPG2:
            return []

        emb_str = f"[{','.join(str(float(x)) for x in query_vec)}]"
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                # NOTE: placeholders are positional — build params in exact SQL
                # text order: dense rank (emb), dense cat?, dense order (emb),
                # dense limit, lexical rank (q), lexical where (q), lexical
                # cat?, lexical order (q), lexical limit, final limit.
                params: List[Any] = [emb_str]
                cat_clause = ""
                if category:
                    cat_clause = "AND category = %s"
                    params.append(category)
                params.extend([emb_str, candidate_pool, query, query])
                if category:
                    params.append(category)
                params.extend([query, candidate_pool, top_k])

                sql = f"""
                WITH dense AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
                    FROM memories
                    WHERE tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                      {cat_clause}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                ),
                lexical AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY ts_rank(search_vector, plainto_tsquery('english', %s)) DESC) AS rank
                    FROM memories
                    WHERE search_vector @@ plainto_tsquery('english', %s)
                      AND tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                      {cat_clause}
                    ORDER BY ts_rank(search_vector, plainto_tsquery('english', %s)) DESC
                    LIMIT %s
                ),
                fused AS (
                    SELECT COALESCE(d.id, l.id) AS id,
                           COALESCE(1.0 / (60 + d.rank), 0.0) + COALESCE(1.0 / (60 + l.rank), 0.0) AS rrf
                    FROM dense d
                    FULL OUTER JOIN lexical l ON d.id = l.id
                )
                SELECT m.*, f.rrf AS relevance_score,
                       json_build_object(
                         'sparse_rank', l.rank,
                         'dense_rank', d.rank,
                         'fused_score', f.rrf
                       ) AS explanation
                FROM fused f
                JOIN memories m ON m.id = f.id
                LEFT JOIN dense d ON d.id = f.id
                LEFT JOIN lexical l ON l.id = f.id
                ORDER BY f.rrf DESC
                LIMIT %s;
                """
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                fused_out = []
                for r in rows:
                    fused_out.append(self._decrypt_row(dict(r)))
                return fused_out
        except Exception as e:
            self._handle_db_error("hybrid_search_sql", e)
            return []
        finally:
            self._return_connection(conn)

    def count_memories(self, tenant_id: Optional[str] = None) -> int:
        if not self._connected or not HAS_PSYCOPG2:
            if tenant_id:
                return len([m for m in self._fallback_cache.values() if m.get("tenant_id") == tenant_id])
            return len(self._fallback_cache)
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("SELECT count(*) FROM memories WHERE tenant_id = %s;", (tenant_id,))
                else:
                    cur.execute("SELECT count(*) FROM memories;")
                return int(cur.fetchone()[0])
        except Exception as e:
            self._handle_db_error("count_memories", e)
            return 0
        finally:
            self._return_connection(conn)

    def get_all_memories(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self._connected or not HAS_PSYCOPG2:
            if tenant_id:
                return [m for m in self._fallback_cache.values() if m.get("tenant_id") == tenant_id]
            return list(self._fallback_cache.values())
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("SELECT * FROM memories WHERE tenant_id = %s ORDER BY created_at DESC;", (tenant_id,))
                else:
                    cur.execute("SELECT * FROM memories ORDER BY created_at DESC;")
                rows = cur.fetchall()
                return [self._decrypt_row(dict(r)) for r in rows]
        except Exception as e:
            self._handle_db_error("get_all_memories", e)
            return []
        finally:
            self._return_connection(conn)

    def query_candidates(
        self,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 100,
        order_by: str = "last_accessed_at DESC",
    ) -> List[Dict[str, Any]]:
        """
        Retrieves a bounded candidate set directly using SQL indexes
        instead of loading the entire corpus into application memory.
        """
        if not self._connected or not HAS_PSYCOPG2:
            if tenant_id:
                return [m for m in self._fallback_cache.values() if m.get("tenant_id") == tenant_id][:limit]
            return list(self._fallback_cache.values())[:limit]

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                self._set_tenant_context(cur, tenant_id)

                conditions = ["confidence >= %s"]
                params: List[Any] = [min_confidence]
                if tenant_id:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                if category:
                    conditions.append("category = %s")
                    params.append(category)

                safe_orders = {
                    "last_accessed_at DESC": "last_accessed_at DESC",
                    "created_at DESC": "created_at DESC",
                    "access_count DESC": "access_count DESC",
                    "confidence DESC": "confidence DESC",
                }
                order_clause = safe_orders.get(order_by, "last_accessed_at DESC")

                where_clause = "WHERE " + " AND ".join(conditions)
                sql = f"""
                    SELECT * FROM memories
                    {where_clause}
                    ORDER BY {order_clause}
                    LIMIT %s
                """
                params.append(limit)
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [self._decrypt_row(dict(r)) for r in rows]
        except Exception as e:
            self._handle_db_error("query_candidates", e)
            return []
        finally:
            self._return_connection(conn)

    def delete_memory(self, memory_id: str, tenant_id: Optional[str] = None) -> None:
        self._fallback_cache.pop(memory_id, None)
        if not self._connected or not HAS_PSYCOPG2:
            return
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("DELETE FROM memories WHERE id = %s AND tenant_id = %s;", (memory_id, tenant_id))
                else:
                    cur.execute("DELETE FROM memories WHERE id = %s;", (memory_id,))
                conn.commit()
        except Exception as e:
            conn.rollback()
            self._handle_db_error("delete_memory", e)
        finally:
            self._return_connection(conn)

    def clear_memories(self, tenant_id: Optional[str] = None) -> int:
        deleted_count = 0
        if tenant_id:
            before_len = len(self._fallback_cache)
            self._fallback_cache = {k: v for k, v in self._fallback_cache.items() if v.get("tenant_id") != tenant_id}
            deleted_count = before_len - len(self._fallback_cache)
        else:
            deleted_count = len(self._fallback_cache)
            self._fallback_cache.clear()

        if not self._connected or not HAS_PSYCOPG2:
            return deleted_count

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("DELETE FROM memories WHERE tenant_id = %s;", (tenant_id,))
                else:
                    cur.execute("DELETE FROM memories;")
                deleted_count = cur.rowcount
                conn.commit()
                return deleted_count
        except Exception as e:
            conn.rollback()
            self._handle_db_error("clear_memories", e)
            return deleted_count
        finally:
            self._return_connection(conn)

    def reembed_all_memories(self, embedder: Any, batch_size: int = 500) -> int:
        """Regenerates vector embeddings for all stored memory records.

        Production-safe: streams rows in batches via a named (server-side) cursor
        and commits per batch, so a 100k+ row corpus re-embed does not load the
        whole table into memory or hold one giant transaction open. Also records
        the new embedding dimension + model version on each row so future schema
        checks can detect drift instead of silently corrupting the HNSW index.
        """
        if not self._connected or not HAS_PSYCOPG2:
            return 0

        target_dim = getattr(embedder, "dimension", self.dimension)
        model_version = getattr(embedder, "model_name", None) or getattr(embedder, "provider_name", "unknown")

        conn = self._get_connection()
        updated_count = 0
        try:
            # Admin (unscoped) context so RLS does not hide rows from this
            # maintenance scan. Uses a regular cursor first because SET LOCAL
            # must run before the named cursor opens its transaction.
            setup_cur = conn.cursor()
            try:
                self._set_tenant_context(setup_cur, None)
            finally:
                try:
                    setup_cur.close()
                except Exception:
                    pass
            # Named cursor streams results server-side instead of fetchall().
            cursor_name = f"reembed_{secrets_token_hex(6)}"
            read_cur = conn.cursor(name=cursor_name)
            read_cur.itersize = batch_size
            read_cur.execute("SELECT id, content FROM memories ORDER BY created_at;")

            write_cur = conn.cursor()
            batch = 0
            while True:
                rows = read_cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    mem_id = row[0] if isinstance(row, (tuple, list)) else row["id"]
                    raw_content = row[1] if isinstance(row, (tuple, list)) else row["content"]
                    try:
                        content = self._decrypt_field(raw_content)
                    except Exception:
                        content = raw_content
                    new_vector = embedder.embed_query(content)
                    vec_str = f"[{','.join(str(x) for x in new_vector)}]"
                    write_cur.execute(
                        "UPDATE memories SET embedding = %s, embedding_dimension = %s, model_version = %s, content_plain = %s WHERE id = %s;",
                        (vec_str, len(new_vector), model_version, content, mem_id),
                    )
                    updated_count += 1
                conn.commit()
                batch += 1
                logger.info("Re-embed batch %d complete (running total: %d rows).", batch, updated_count)

            read_cur.close()
            logger.info(f"Re-embedded {updated_count} memories to dimension {target_dim} (model={model_version}).")
        except Exception as e:
            conn.rollback()
            self._handle_db_error("reembed_all_memories", e)
        finally:
            self._return_connection(conn)
        return updated_count
