import json
import math
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from synapse_memory.core.temporal import is_retrieval_eligible, validate_as_of
from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)


class _AutoClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager COMMITS/ROLLS BACK and CLOSES.

    Stock sqlite3 ``with conn:`` only commits or rolls back but leaves the file
    handle open — on Windows that locks test .db files (PermissionError on
    cleanup) and leaks WAL handles under FastAPI workers. This subclass closes
    the handle on ``__exit__`` unless the instance is flagged
    ``_sqlite_keep_open`` (used by SQLiteMemoryStore for its long-lived
    ``:memory:`` connection).
    """

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore
        # Commit (or roll back) on context exit, then close the handle UNLESS
        # this is the store's cached ':memory:' connection. SQLite ':memory:'
        # databases are per-connection, so that one must stay open for the
        # lifetime of the store (closing it would discard the schema and every
        # migration). File-based connections always close here so Windows test
        # teardown can delete the .db / -wal / -shm files without PermissionError.
        try:
            if exc_type is None:
                try:
                    self.commit()
                except Exception:
                    pass
            else:
                try:
                    self.rollback()
                except Exception:
                    pass
        finally:
            # Skip closing the flagged long-lived ':memory:' connection so its
            # schema and migrations survive this `with`; close everything else.
            if not getattr(self, '_sqlite_keep_open', False):
                try:
                    self.close()
                except Exception:
                    pass
        return False


class SQLiteMemoryStore:
    """
    Sovereign SQLite persistence backend with mandatory authenticated encryption at rest
    and database-level multi-tenant isolation.
    """

    def __init__(
        self,
        db_path: str = "synapse_memory.db",
        encryption_provider: Optional[Any] = None,
        fail_closed: bool = False
    ):
        self.db_path = db_path
        # Parity with PGVectorMemoryStore: strict callers (tests, provisioning)
        # may demand that schema bootstrap errors raise instead of warn.
        self.fail_closed = fail_closed
        if encryption_provider is not None:
            self.encryption_provider = encryption_provider
        else:
            key = KeyManagementProvider.get_encryption_key()
            self.encryption_provider = FernetEncryptionProvider(key)
        self._conn: Optional[sqlite3.Connection] = None  # cached shared connection for ':memory:'
        self._initialize_database()

    def _get_connection(self) -> sqlite3.Connection:
        # SQLite ':memory:' is per-connection, so the schema created during
        # _initialize_database must live on ONE shared connection. Cache a
        # single connection only for ':memory:' stores; file-based stores use a
        # fresh connection per query so that test teardown can remove the DB
        # file cleanly.
        if self.db_path == ":memory:" and self._conn is not None:
            return self._conn
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            check_same_thread=False,
            factory=_AutoClosingConnection,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        if self.db_path == ":memory:":
            self._conn = conn
            setattr(conn, "_sqlite_keep_open", True)
        return conn

    def close(self) -> None:
        """Release the cached connection and checkpoint WAL sidecars.

        File-based stores open a short-lived connection to run a WAL
        checkpoint so no ``-wal``/``-shm`` files linger after tests or
        shutdown; ':memory:' stores skip that (there is nothing on disk).
        Either way the cached ':memory:' connection is then torn down.
        """
        try:
            if self.db_path != ":memory:":
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass
        finally:
            self._close_conn()

    def _close_conn(self) -> None:
        """Close the store's cached ':memory:' connection, if one exists."""
        conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None

    def _return_connection(self, conn: Any) -> None:
        """Pool-compatible no-op.

        SQLiteMemoryStore owns a single cached ':memory:' connection instead of
        an external pool, so there is nothing to return. Exposing this method
        lets ``run_migrations(..., owns_connection=False)`` hand the connection
        back without closing it, keeping the ':memory:' schema alive.
        """
        pass

    def _initialize_database(self) -> None:
        """Creates the memory table with tenant isolation and index constraints on creation."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    access_count INTEGER NOT NULL,
                    token_cost INTEGER NOT NULL,
                    embedding TEXT,
                    feedback_multiplier REAL NOT NULL,
                    supersedes VARCHAR(128),
                    superseded_by VARCHAR(128),
                    source_type VARCHAR(64) NOT NULL DEFAULT 'unspecified',
                    source_id VARCHAR(256),
                    observed_at REAL,
                    valid_from REAL,
                    valid_until REAL,
                    trust_level VARCHAR(32) NOT NULL DEFAULT 'unverified',
                    supersession_reason VARCHAR(128),
                    retracted_at REAL,
                    retraction_reason VARCHAR(128)
                )
            """)

        # Migration check: Ensure tenant_id column exists if the table was
        # created by an older schema version.
        cursor.execute("PRAGMA table_info(memories)")
        columns = [info[1] for info in cursor.fetchall()]
        if "tenant_id" not in columns:
            cursor.execute(
                "ALTER TABLE memories ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant ON memories(tenant_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_category ON memories(tenant_id, category)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_category ON memories(category)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_last_accessed ON memories(last_accessed_at)"
        )
        conn.commit()

        # Apply forward-only schema migrations (adds embedding_dimension /
        # model_version + migration ledger). Idempotent + versioned.
        # ':memory:' is per-connection in SQLite, so the schema created during
        # _initialize_database lives on our cached connection and must survive
        # this run of migrations. Pass owns_connection=False so the cached
        # connection is NOT closed here.
        from synapse_memory.core.migrations import run_migrations
        if self.db_path == ":memory:":
            run_migrations(
                self._get_connection,
                is_postgres=False,
                fail_closed=self.fail_closed,
                owns_connection=False,
                close_on_exit=False,
            )
        else:
            # File-based stores hand run_migrations a callable that returns the
            # schema-init connection and let it own+close it. This also lets
            # test teardown remove the DB file cleanly.
            run_migrations(
                lambda: conn,
                is_postgres=False,
                fail_closed=self.fail_closed,
            )

    def insert_memory(self, memory: Dict[str, Any]) -> None:
        """Inserts a structured memory node into the SQLite table with tenant isolation."""
        content = self.encryption_provider.encrypt(memory["content"])
        embedding = self.encryption_provider.encrypt(
            json.dumps(memory.get("embedding", []))
        )
        tenant_id = memory.get("tenant_id") or memory.get("tenantId") or "default"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO memories (
                    id, tenant_id, content, category, confidence, created_at,
                    last_accessed_at, access_count, token_cost,
                    embedding, feedback_multiplier, embedding_dimension, model_version,
                    supersedes, superseded_by, source_type, source_id, observed_at,
                    valid_from, valid_until, trust_level, supersession_reason,
                    retracted_at, retraction_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory["id"],
                tenant_id,
                content,
                memory["category"],
                memory["confidence"],
                memory["created_at"],
                memory.get("last_accessed_at", memory["created_at"]),
                memory.get("access_count", 0),
                memory["token_cost"],
                embedding,
                memory.get("feedback_multiplier", 1.0),
                memory.get("embedding_dimension", len(memory.get("embedding", []))),
                memory.get("model_version", "unknown"),
                memory.get("supersedes"),
                memory.get("superseded_by"),
                memory.get("source_type", "unspecified"),
                memory.get("source_id"),
                memory.get("observed_at"),
                memory.get("valid_from"),
                memory.get("valid_until"),
                memory.get("trust_level", "unverified"),
                memory.get("supersession_reason"),
                memory.get("retracted_at"),
                memory.get("retraction_reason"),
            ))
            conn.commit()

    def mark_superseded(self, memory_id: str, superseded_by: str, tenant_id: Optional[str] = None) -> None:
        """Stamp `superseded_by` on an old memory that a dedup MERGE replaced.

        Scoped to ``tenant_id`` when supplied: unlike PostgreSQL (where RLS
        enforces the boundary), SQLite has no row-level security, so the tenant
        predicate must be part of the statement itself. Without it a supersede
        issued in one tenant could rewrite another tenant's row.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    "UPDATE memories SET superseded_by = ? WHERE id = ? AND tenant_id = ?",
                    (superseded_by, memory_id, tenant_id),
                )
            else:
                cursor.execute(
                    "UPDATE memories SET superseded_by = ? WHERE id = ?",
                    (superseded_by, memory_id),
                )
            conn.commit()

    def get_all_memories(
        self, tenant_id: Optional[str] = None, include_superseded: bool = False
    ) -> List[Dict[str, Any]]:
        """Retrieves memories with deserialized embeddings, optionally scoped to tenant_id.

        Superseded rows (dedup MERGE lineage) are hidden by default: they remain
        for audit/lineage only and must never be served to retrieval. Capacity
        pruning passes include_superseded=True so stale rows stay evictable.
        """
        query = "SELECT * FROM memories"
        clauses: List[str] = []
        params: List[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            memories = []
            for row in rows:
                mem = dict(row)
                mem["content"] = self.encryption_provider.decrypt(mem["content"])
                mem["embedding"] = json.loads(
                    self.encryption_provider.decrypt(mem["embedding"])
                )
                memories.append(mem)
            return memories

    def get_memory_by_id(self, memory_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single memory node by its unique primary key ID and optional tenant scope."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    "SELECT * FROM memories WHERE id = ? AND tenant_id = ?",
                    (memory_id, tenant_id)
                )
            else:
                cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if row:
                mem = dict(row)
                mem["content"] = self.encryption_provider.decrypt(mem["content"])
                mem["embedding"] = json.loads(
                    self.encryption_provider.decrypt(mem["embedding"])
                )
                return mem
            return None

    def update_access_count(
        self, memory_id: str, count_increment: int = 1, tenant_id: Optional[str] = None
    ) -> None:
        """Increases the usage metrics for Ebbinghaus stabilization weight tracking."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute("""
                    UPDATE memories
                    SET access_count = access_count + ?, last_accessed_at = ?
                    WHERE id = ? AND tenant_id = ?
                """, (count_increment, time.time(), memory_id, tenant_id))
            else:
                cursor.execute("""
                    UPDATE memories
                    SET access_count = access_count + ?, last_accessed_at = ?
                    WHERE id = ?
                """, (count_increment, time.time(), memory_id))
            conn.commit()

    def update_confidence(
        self, memory_id: str, new_confidence: float, tenant_id: Optional[str] = None
    ) -> None:
        """Saves mutated confidence values resulting from active RLAIF feedback."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute("""
                    UPDATE memories
                    SET confidence = ?
                    WHERE id = ? AND tenant_id = ?
                """, (new_confidence, memory_id, tenant_id))
            else:
                cursor.execute("""
                    UPDATE memories
                    SET confidence = ?
                    WHERE id = ?
                """, (new_confidence, memory_id))
            conn.commit()

    def retract_memory(
        self,
        memory_id: str,
        tenant_id: str,
        reason: str,
        retracted_at: Optional[float] = None,
    ) -> bool:
        """Mark a memory retracted without erasing its audit lineage."""
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason):
            raise ValueError("reason must be a controlled lowercase snake_case code")
        timestamp = time.time() if retracted_at is None else retracted_at
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("retracted_at must be a finite non-negative timestamp")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE memories
                   SET retracted_at = ?, retraction_reason = ?
                   WHERE id = ? AND tenant_id = ? AND retracted_at IS NULL""",
                (timestamp, reason, memory_id, tenant_id),
            )
            changed = cursor.rowcount > 0
            conn.commit()
            return changed

    def delete_memory(self, memory_id: str, tenant_id: Optional[str] = None) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    "DELETE FROM memories WHERE id = ? AND tenant_id = ?",
                    (memory_id, tenant_id)
                )
            else:
                cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

    def clear_memories(self, tenant_id: Optional[str] = None) -> int:
        """Clears memories for a given tenant or the entire store and returns count of deleted rows."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute("DELETE FROM memories WHERE tenant_id = ?", (tenant_id,))
            else:
                cursor.execute("DELETE FROM memories")
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count

    def get_memories_by_category(
        self, category: str, tenant_id: Optional[str] = None, include_superseded: bool = False
    ) -> List[Dict[str, Any]]:
        """Retrieves memories filtered by category, utilizing the category index.

        Superseded rows are hidden by default (see get_all_memories).
        """
        query = "SELECT * FROM memories WHERE category = ?"
        params: List[Any] = [category]
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if not include_superseded:
            query += " AND superseded_by IS NULL"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            memories = []
            for row in rows:
                mem = dict(row)
                mem["content"] = self.encryption_provider.decrypt(mem["content"])
                mem["embedding"] = json.loads(
                    self.encryption_provider.decrypt(mem["embedding"])
                )
                memories.append(mem)
            return memories

    def vector_search(
        self,
        query_vec: List[float],
        top_k: int = 10,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None,
        temporal_as_of: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Dense retrieval over the SQLite corpus using cosine similarity.

        NOTE: SQLite is a single-node dev/local backend. This is an exact O(N)
        in-memory scan, not an approximate HNSW index. For team/production scale
        use the pgvector backend which pushes `embedding <=> query` down to SQL.
        Results match the PGVectorMemoryStore.vector_search contract:
        each node carries `cosine_similarity` and `relevance_score`.
        """
        as_of = validate_as_of(time.time() if temporal_as_of is None else temporal_as_of)
        memories = (
            self.get_memories_by_category(category, tenant_id=tenant_id)
            if category
            else self.get_all_memories(tenant_id=tenant_id)
        )
        memories = [m for m in memories if is_retrieval_eligible(m, as_of)]

        mag_q = sum(a * a for a in query_vec) ** 0.5
        results: List[Dict[str, Any]] = []
        for mem in memories:
            vec = mem.get("embedding", [])
            if vec and len(vec) == len(query_vec):
                dot = sum(a * b for a, b in zip(query_vec, vec))
                mag_v = sum(b * b for b in vec) ** 0.5
                sim = dot / (mag_q * mag_v) if mag_q * mag_v > 0 else 0.0
            else:
                sim = 0.0
            mem_copy = mem.copy()
            mem_copy["cosine_similarity"] = sim
            mem_copy["relevance_score"] = sim
            results.append(mem_copy)

        results.sort(key=lambda x: x["cosine_similarity"], reverse=True)
        return results[:top_k]

    def lexical_search(
        self,
        query: str,
        top_k: int = 100,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None,
        temporal_as_of: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Sparse (lexical) retrieval over the SQLite corpus using token-overlap
        ranking — the exact-scan analogue of PostgreSQL tsvector GIN ranking.
        Matches the PGVectorMemoryStore.lexical_search contract: each node
        carries `lexical_rank` and `relevance_score`.
        """
        as_of = time.time() if temporal_as_of is None else temporal_as_of
        memories = (
            self.get_memories_by_category(category, tenant_id=tenant_id)
            if category
            else self.get_all_memories(tenant_id=tenant_id)
        )
        memories = [m for m in memories if is_retrieval_eligible(m, as_of)]

        query_terms = set(query.lower().split())
        if not query_terms:
            return []

        results: List[Dict[str, Any]] = []
        for mem in memories:
            words = set(str(mem.get("content", "")).lower().split())
            overlap = len(query_terms.intersection(words))
            if overlap > 0:
                mem_copy = mem.copy()
                mem_copy["lexical_rank"] = float(overlap) / float(len(query_terms))
                mem_copy["relevance_score"] = mem_copy["lexical_rank"]
                results.append(mem_copy)

        results.sort(key=lambda x: x.get("lexical_rank", 0.0), reverse=True)
        return results[:top_k]

    def count_memories(self, tenant_id: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    "SELECT count(*) FROM memories WHERE tenant_id = ?", (tenant_id,)
                )
            else:
                cursor.execute("SELECT count(*) FROM memories")
            return int(cursor.fetchone()[0])

    def count_active_memories(self, tenant_id: Optional[str] = None) -> int:
        """Count of retrieval-visible (non-superseded) memories."""
        query = "SELECT count(*) FROM memories WHERE superseded_by IS NULL"
        params: List[Any] = []
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return int(cursor.fetchone()[0])

    def query_candidates(
        self,
        category: Optional[str] = None,
        tenant_id: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 100,
        order_by: str = "last_accessed_at DESC"
    ) -> List[Dict[str, Any]]:
        """
        Retrieves a bounded candidate set directly using SQL indexes
        instead of loading the entire corpus into application memory.
        """
        query = "SELECT * FROM memories WHERE confidence >= ? AND superseded_by IS NULL"
        params: List[Any] = [min_confidence]
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if category:
            query += " AND category = ?"
            params.append(category)

        safe_orders = {
            "last_accessed_at DESC": "last_accessed_at DESC",
            "created_at DESC": "created_at DESC",
            "access_count DESC": "access_count DESC",
            "confidence DESC": "confidence DESC"
        }
        order_clause = safe_orders.get(order_by, "last_accessed_at DESC")
        query += f" ORDER BY {order_clause} LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            memories = []
            for row in rows:
                mem = dict(row)
                mem["content"] = self.encryption_provider.decrypt(mem["content"])
                mem["embedding"] = json.loads(
                    self.encryption_provider.decrypt(mem["embedding"])
                )
                memories.append(mem)
            return memories
