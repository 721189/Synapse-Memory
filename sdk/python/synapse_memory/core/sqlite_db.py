import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)


class _AutoClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager COMMITS + CLOSES.

    Stock sqlite3 `with conn:` only commits/rollbacks but leaves the file handle
    open — on Windows that locks test .db files (PermissionError on cleanup) and
    leaks WAL handles under FastAPI workers. This subclass closes on __exit__.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore
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
        encryption_provider: Optional[Any] = None
    ):
        self.db_path = db_path
        if encryption_provider is not None:
            self.encryption_provider = encryption_provider
        else:
            key = KeyManagementProvider.get_encryption_key()
            self.encryption_provider = FernetEncryptionProvider(key)
        self._initialize_database()

    def _get_connection(self) -> sqlite3.Connection:
        # WAL mode + busy timeout: required for concurrent FastAPI workers.
        # NOTE: SQLite is single-node dev/local only. Use pgvector for teams.
        # Uses _AutoClosingConnection so `with` blocks commit AND close the handle
        # (stock sqlite3 leaves it open -> Windows file locks + WAL handle leaks).
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            check_same_thread=False,
            factory=_AutoClosingConnection,
        )
        conn.row_factory = sqlite3.Row
        try:
            # checkpoint on close keeps test -wal/-shm sidecars from lingering
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        return conn

    def close(self) -> None:
        """Checkpoint WAL so no -wal/-shm sidecars linger after tests or shutdown."""
        if self.db_path == ":memory:":
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _initialize_database(self) -> None:
        """Creates the memory table with tenant isolation and index constraints on creation."""
        with self._get_connection() as conn:
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
                    feedback_multiplier REAL NOT NULL
                )
            """)

            # Migration check: Ensure tenant_id column exists if table was created in older version
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
                    embedding, feedback_multiplier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                memory.get("feedback_multiplier", 1.0)
            ))
            conn.commit()

    def get_all_memories(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves memories with deserialized embeddings, optionally scoped to tenant_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute("SELECT * FROM memories WHERE tenant_id = ?", (tenant_id,))
            else:
                cursor.execute("SELECT * FROM memories")
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
        self, category: str, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves memories filtered by category, utilizing the category index."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    "SELECT * FROM memories WHERE category = ? AND tenant_id = ?",
                    (category, tenant_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM memories WHERE category = ?", (category,)
                )
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
        tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Dense retrieval over the SQLite corpus using cosine similarity.

        NOTE: SQLite is a single-node dev/local backend. This is an exact O(N)
        in-memory scan, not an approximate HNSW index. For team/production scale
        use the pgvector backend which pushes `embedding <=> query` down to SQL.
        Results match the PGVectorMemoryStore.vector_search contract:
        each node carries `cosine_similarity` and `relevance_score`.
        """
        memories = (
            self.get_memories_by_category(category, tenant_id=tenant_id)
            if category
            else self.get_all_memories(tenant_id=tenant_id)
        )

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
        tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Sparse (lexical) retrieval over the SQLite corpus using token-overlap
        ranking — the exact-scan analogue of PostgreSQL tsvector GIN ranking.
        Matches the PGVectorMemoryStore.lexical_search contract: each node
        carries `lexical_rank` and `relevance_score`.
        """
        memories = (
            self.get_memories_by_category(category, tenant_id=tenant_id)
            if category
            else self.get_all_memories(tenant_id=tenant_id)
        )

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
        query = "SELECT * FROM memories WHERE confidence >= ?"
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
