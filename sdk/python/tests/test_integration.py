import unittest
from synapse_memory import MemoryManager, SQLiteMemoryStore, SynapseEmbedder
import asyncio

class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_integration.db"
        self.store = SQLiteMemoryStore(db_path=self.db_path)
        self.embedder = SynapseEmbedder(provider="local")
        self.manager = MemoryManager(store=self.store, embedder=self.embedder, max_record_limit=5)

    def tearDown(self):
        import os
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_full_pipeline(self):
        # 1. Ingest
        mid, status, cost = self.manager.ingest_with_deduplication("Hello World")
        self.assertEqual(status, "CREATED")
        
        # 2. Ingest duplicate
        mid2, status2, cost2 = self.manager.ingest_with_deduplication("Hello World")
        self.assertEqual(status2, "MERGED")
        
        # 3. Verify store
        memories = self.store.get_all_memories()
        self.assertEqual(len(memories), 1)

if __name__ == '__main__':
    unittest.main()
