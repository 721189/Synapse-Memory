import unittest
import time
import os
import sys

# Correct path adjustments
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.hybrid_search import HybridSearch

class TestSynapseCore(unittest.TestCase):

    def setUp(self):
        # Use a localized test database
        self.test_db_path = "test_synapse_memory.db"
        self.store = SQLiteMemoryStore(self.test_db_path)
        self.embedder = SynapseEmbedder(provider="local")
        self.manager = MemoryManager(store=self.store, embedder=self.embedder, max_record_limit=5)
        self.decay = DecayEngine()
        self.packer = KnapsackPacker()
        self.searcher = HybridSearch()

    def tearDown(self):
        # Erase test DB artifacts cleanly
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_knapsack_packer_dp(self):
        """Tests optimal dynamic programming allocation under tight budgets."""
        memories = [
            {"id": "m1", "token_cost": 10, "relevance_score": 0.90},
            {"id": "m2", "token_cost": 20, "relevance_score": 0.85},
            {"id": "m3", "token_cost": 30, "relevance_score": 0.70}
        ]
        # DP Allocation with 30 capacity should pick m1 and m2 (value = 1.75) instead of m3 alone
        packed = self.packer.pack(memories, 30)
        self.assertEqual(len(packed), 2)
        packed_ids = [m["id"] for m in packed]
        self.assertIn("m1", packed_ids)
        self.assertIn("m2", packed_ids)

    def test_ebbinghaus_decay(self):
        """Tests decay scores over elapsed intervals using near floating-point precision."""
        now = time.time()
        retention_fresh = self.decay.calculate_retention(
            base_relevance=0.90,
            created_epoch=now,
            access_count=0
        )
        self.assertAlmostEqual(retention_fresh, 0.90, places=4)

        # Aged memory should decay towards the floor limit
        two_days_ago = now - (86400 * 2)
        retention_aged = self.decay.calculate_retention(
            base_relevance=0.90,
            created_epoch=two_days_ago,
            access_count=0
        )
        self.assertLess(retention_aged, 0.90)
        self.assertGreaterEqual(retention_aged, self.decay.decay_floor)

    def test_sqlite_operations(self):
        """Tests structured inserts, select counters, and primary key updates in SQLite."""
        mem = {
            "id": "mem_test",
            "content": "Verify persistence engine",
            "category": "test",
            "confidence": 0.90,
            "created_at": time.time(),
            "access_count": 0,
            "token_cost": 20,
            "embedding": [0.1] * 128
        }
        self.store.insert_memory(mem)
        self.assertEqual(self.store.count_memories(), 1)

        fetched = self.store.get_memory_by_id("mem_test")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["content"], "Verify persistence engine")

    def test_semantic_deduplication(self):
        """Tests that similar ingested texts do not cause database-node fragmentation."""
        content = "Developers must configure secure certificates on AWS load balancers."
        
        # 1st Ingestion
        id1, action1, _ = self.manager.ingest_with_deduplication(content, "infrastructure")
        self.assertEqual(action1, "CREATED")

        # 2nd Ingestion with highly overlapping casing and punctuation variants (triggers deduplication)
        similar_content = "DEVELOPERS MUST CONFIGURE SECURE CERTIFICATES ON AWS LOAD BALANCERS!!!"
        id2, action2, _ = self.manager.ingest_with_deduplication(similar_content, "infrastructure")
        
        self.assertEqual(action2, "MERGED")
        self.assertEqual(id1, id2)  # IDs must align exactly due to merge mapping
        self.assertEqual(self.store.count_memories(), 1)

    def test_auto_pruning_limit(self):
        """Tests active eviction once limit borders are breached using distinct semantic sentences."""
        distinct_topics = [
            "Apple pie dessert recipes require flour, cinnamon, and fresh fruit.",
            "SpaceX rockets fly into low earth orbits to deliver Starlink assets.",
            "The monetary yen is the primary legal tender across Japanese banks.",
            "Docker orchestrates containers cleanly inside isolated Linux hypervisors.",
            "Microservice backends communicate dynamically using fast gRPC streams.",
            "Newton formulated gravity laws to analyze physics forces on earth."
        ]
        
        # Ingest 6 completely unrelated topics into a 5-record maximum database
        for sentence in distinct_topics:
            self.manager.ingest_with_deduplication(sentence, "test")
        
        # Total counts must remain strictly capped at 5
        self.assertEqual(self.store.count_memories(), 5)

if __name__ == "__main__":
    unittest.main()
