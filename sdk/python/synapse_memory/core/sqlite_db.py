import sqlite3
import json
import os
from typing import List, Dict, Any, Optional

class SQLiteMemoryStore:
    """
    Sovereign SQLite persistence backend for SynapseMemory nodes.
    Ensures zero-loss local storage of vectorized cognitive memories.
    """

    def __init__(self, db_path: str = "synapse_memory.db"):
        self.db_path = db_path
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
                    access_count INTEGER NOT NULL,
                    token_cost INTEGER NOT NULL,
                    embedding TEXT -- JSON array of floats
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
            conn.commit()

    def insert_memory(self, memory: Dict[str, Any]) -> None:
        """Inserts a structured memory node into the SQLite table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            embedding_json = json.dumps(memory.get("embedding", []))
            cursor.execute("""
                INSERT OR REPLACE INTO memories (
                    id, content, category, confidence, created_at, access_count, token_cost, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory["id"],
                memory["content"],
                memory["category"],
                memory["confidence"],
                memory["created_at"],
                memory["access_count"],
                memory["token_cost"],
                embedding_json
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
                mem["embedding"] = json.loads(row["embedding"]) if row["embedding"] else []
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
                mem["embedding"] = json.loads(row["embedding"]) if row["embedding"] else []
                return mem
            return None

    def update_access_count(self, memory_id: str, count_increment: int = 1) -> None:
        """Increases the usage metrics for Ebbinghaus stabilization weight tracking."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories 
                SET access_count = access_count + ? 
                WHERE id = ?
            """, (count_increment, memory_id))
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

    def delete_memories_by_tenant(self, text_pattern: str) -> int:
        """
        Executes a targeted wipe of memories matching a text pattern 
        to comply with GDPR GDPR criteria.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE content LIKE ?", (f"%{text_pattern}%",))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def delete_memory(self, memory_id: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

    def count_memories(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memories")
            return cursor.fetchone()[0]
