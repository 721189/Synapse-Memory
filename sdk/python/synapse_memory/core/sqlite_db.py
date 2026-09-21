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
    Sovereign SQLite persistence backend with mandatory authenticated encryption at rest.
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
        """Creates the memory table with index constraints on creation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
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
        """Inserts a structured memory node into the SQLite table."""
        content = self.encryption_provider.encrypt(memory["content"])
        embedding = self.encryption_provider.encrypt(
            json.dumps(memory.get("embedding", []))
        )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO memories (
                    id, content, category, confidence, created_at,
                    last_accessed_at, access_count, token_cost,
                    embedding, feedback_multiplier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory["id"],
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

    def get_all_memories(self) -> List[Dict[str, Any]]:
        """Retrieves all memories with deserialized embeddings."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
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

    def get_memory_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single memory node by its unique primary key ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
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
        self, memory_id: str, count_increment: int = 1
    ) -> None:
        """Increases the usage metrics for Ebbinghaus stabilization weight tracking."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories 
                SET access_count = access_count + ?, last_accessed_at = ?
                WHERE id = ?
            """, (count_increment, time.time(), memory_id))
            conn.commit()

    def update_confidence(self, memory_id: str, new_confidence: float) -> None:
        """Saves mutated confidence values resulting from active RLAIF feedback."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories 
                SET confidence = ? 
                WHERE id = ?
            """, (new_confidence, memory_id))
            conn.commit()

    def delete_memory(self, memory_id: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

    def get_memories_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Retrieves memories filtered by category, utilizing the category index."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
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

    def count_memories(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM memories")
            return int(cursor.fetchone()[0])

