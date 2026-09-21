import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from synapse_memory.core.encryption import (
    FernetEncryptionProvider,
    KeyManagementProvider,
)


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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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

    def clear_memories(self, tenant_id: Optional[str] = None) -> None:
        """Clears memories for a given tenant or the entire store."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute("DELETE FROM memories WHERE tenant_id = ?", (tenant_id,))
            else:
                cursor.execute("DELETE FROM memories")
            conn.commit()

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
