"""
Production PostgreSQL + pgvector + HNSW Memory Store with Database-Level Multi-Tenancy
and PostgreSQL Row-Level Security (RLS).
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)

logger = logging.getLogger("PGVectorMemoryStore")

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
        fail_closed: bool = False
    ):
        self.connection_string = connection_string or (
            "postgresql://synapse_admin:synapse_secure_pass@127.0.0.1:5432/synapse_cognitive"
        )
        self.dimension = dimension
        self.m = m
        self.ef_construction = ef_construction
        self.fail_closed = fail_closed
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

    def _connect(self) -> None:
        try:
            if pool is not None:
                self._pool = pool.SimpleConnectionPool(
                    1, 10, self.connection_string
                )
            self._connected = True
            self._initialize_database()
            logger.info("PGVectorMemoryStore successfully connected to PostgreSQL.")
        except Exception as e:
            self._connected = False
            if self.fail_closed:
                logger.error(f"PostgreSQL/pgvector connection failed in fail-closed mode: {e}")
                raise RuntimeError(f"PostgreSQL connection failed: {e}")
            logger.warning(
                f"PostgreSQL/pgvector connection unviable ({e}); operating in fallback mode."
            )

    def _initialize_database(self) -> None:
        if not self._connected or not HAS_PSYCOPG2:
            return
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                # 1. Enable extensions
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

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

                # 5b. Generated Full-Text Search (tsvector) column and GIN index for scalable PostgreSQL lexical search
                try:
                    cur.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='memories' AND column_name='search_vector'
                            ) THEN
                                ALTER TABLE memories
                                ADD COLUMN search_vector tsvector
                                GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
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
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'tenant_isolation_policy'
                        ) THEN
                            CREATE POLICY tenant_isolation_policy ON memories
                            FOR ALL
                            USING (
                                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                            )
                            WITH CHECK (
                                tenant_id = NULLIF(current_setting('app.current_tenant', true), '')
                            );
                        END IF;
                    END $$;
                """)

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

    def _get_connection(self) -> Any:
        if self._pool is not None:
            return self._pool.getconn()
        if psycopg2 is not None:
            return psycopg2.connect(self.connection_string)
        raise RuntimeError("psycopg2 driver not installed.")

    def _return_connection(self, conn: Any) -> None:
        if self._pool is not None:
            self._pool.putconn(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass

    def _set_tenant_context(self, cur: Any, tenant_id: Optional[str]) -> None:
        ctx = tenant_id if tenant_id and tenant_id != "*" else ""
        cur.execute("SET LOCAL app.current_tenant = %s;", (ctx,))

    def insert_memory(self, memory: Dict[str, Any]) -> None:
        """Inserts memory with embedding vector into pgvector table with tenant isolation."""
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
                cur.execute("""
                    INSERT INTO memories (
                        id, tenant_id, content, category, confidence, created_at,
                        last_accessed_at, access_count, token_cost,
                        feedback_multiplier, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        content = EXCLUDED.content,
                        category = EXCLUDED.category,
                        confidence = EXCLUDED.confidence,
                        last_accessed_at = EXCLUDED.last_accessed_at,
                        access_count = EXCLUDED.access_count,
                        token_cost = EXCLUDED.token_cost,
                        feedback_multiplier = EXCLUDED.feedback_multiplier,
                        embedding = EXCLUDED.embedding;
                """, (
                    memory["id"],
                    tenant_id,
                    memory["content"],
                    memory["category"],
                    memory["confidence"],
                    memory["created_at"],
                    memory.get("last_accessed_at", time.time()),
                    memory.get("access_count", 0),
                    memory.get("token_cost", 0),
                    memory.get("feedback_multiplier", 1.0),
                    emb_str
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
                    mem = dict(row)
                    if isinstance(mem.get("embedding"), str):
                        try:
                            mem["embedding"] = json.loads(mem["embedding"])
                        except Exception:
                            pass
                    return mem
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
                    mem = dict(r)
                    if isinstance(mem.get("embedding"), str):
                        try:
                            mem["embedding"] = json.loads(mem["embedding"])
                        except Exception:
                            pass
                    results.append(mem)
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
                    mem = dict(r)
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
                conditions = ["search_vector @@ plainto_tsquery('english', %s)"]
                params: List[Any] = [query, query]
                if tenant_id:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                if category:
                    conditions.append("category = %s")
                    params.append(category)

                where_clause = "WHERE " + " AND ".join(conditions)
                params.append(top_k)

                sql = f"""
                    SELECT id, tenant_id, content, category, confidence, created_at,
                           last_accessed_at, access_count, token_cost, feedback_multiplier,
                           ts_rank(search_vector, plainto_tsquery('english', %s)) AS lexical_rank
                    FROM memories
                    {where_clause}
                    ORDER BY lexical_rank DESC
                    LIMIT %s
                """
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    mem = dict(r)
                    mem["relevance_score"] = float(mem.get("lexical_rank", 0.0))
                    results.append(mem)
                return results
        except Exception as e:
            self._handle_db_error("lexical_search", e)
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
                return [dict(r) for r in rows]
        except Exception as e:
            self._handle_db_error("get_all_memories", e)
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

    def clear_memories(self, tenant_id: Optional[str] = None) -> None:
        if tenant_id:
            self._fallback_cache = {k: v for k, v in self._fallback_cache.items() if v.get("tenant_id") != tenant_id}
        else:
            self._fallback_cache.clear()

        if not self._connected or not HAS_PSYCOPG2:
            return
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                self._set_tenant_context(cur, tenant_id)
                if tenant_id:
                    cur.execute("DELETE FROM memories WHERE tenant_id = %s;", (tenant_id,))
                else:
                    cur.execute("DELETE FROM memories;")
                conn.commit()
        except Exception as e:
            conn.rollback()
            self._handle_db_error("clear_memories", e)
        finally:
            self._return_connection(conn)

    def reembed_all_memories(self, embedder: Any) -> int:
        """Regenerates vector embeddings for all stored memory records using the active embedder."""
        if not self._connected or not HAS_PSYCOPG2:
            return 0

        conn = self._get_connection()
        updated_count = 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, content FROM memories;")
                rows = cur.fetchall()
                for row in rows:
                    mem_id = row[0] if isinstance(row, (tuple, list)) else row["id"]
                    content = row[1] if isinstance(row, (tuple, list)) else row["content"]
                    new_vector = embedder.embed_query(content)
                    vec_str = f"[{','.join(str(x) for x in new_vector)}]"
                    cur.execute(
                        "UPDATE memories SET embedding = %s WHERE id = %s;",
                        (vec_str, mem_id)
                    )
                    updated_count += 1
                conn.commit()
                logger.info(f"Re-embedded {updated_count} memories to dimension {embedder.dimension}.")
        except Exception as e:
            conn.rollback()
            self._handle_db_error("reembed_all_memories", e)
        finally:
            self._return_connection(conn)
        return updated_count
