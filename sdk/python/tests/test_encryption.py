import os
import sqlite3
import unittest
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.encryption import FernetEncryptionProvider


class TestEncryption(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_encryption.db"
        self.encryption = FernetEncryptionProvider()
        self.store = SQLiteMemoryStore(db_path=self.db_path, encryption_provider=self.encryption)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_encryption_at_rest(self):
        memory = {
            "id": "mem_1",
            "content": "Secret cognitive memory",
            "category": "private",
            "confidence": 1.0,
            "created_at": 100.0,
            "token_cost": 10,
            "embedding": [0.1, 0.2, 0.3]
        }
        self.store.insert_memory(memory)

        # Verify content in DB is encrypted
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE id='mem_1'")
        raw_content = cursor.fetchone()[0]
        self.assertNotEqual(raw_content, "Secret cognitive memory")
        conn.close()

        # Retrieve and verify decryption
        retrieved = self.store.get_memory_by_id("mem_1")
        self.assertEqual(retrieved["content"], "Secret cognitive memory")
        self.assertEqual(retrieved["embedding"], [0.1, 0.2, 0.3])


if __name__ == '__main__':
    unittest.main()

