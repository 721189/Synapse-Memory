import unittest
import time
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse_memory.integrations.llamaindex import SynapseLlamaIndexMemory, ChatMessage
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder


class TestSynapseLlamaIndexIntegration(unittest.TestCase):

    def setUp(self):
        self.db_path = "test_llamaindex_memory.db"
        self.store = SQLiteMemoryStore(self.db_path)
        self.embedder = SynapseEmbedder(provider="local")
        self.memory = SynapseLlamaIndexMemory(
            max_token_limit=600,
            store=self.store,
            embedder=self.embedder
        )

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def test_put_and_get_messages(self):
        """Tests ChatMessage storage, semantic deduplication, and hybrid retrieval."""
        self.memory.put(ChatMessage(role="user", content="Configure CORS origins to allow https://app.internal"))
        self.memory.put(ChatMessage(role="assistant", content="CORS allowlist updated for https://app.internal"))
        self.memory.put(ChatMessage(role="user", content="Database read replicas are located in us-east-1 and us-west-2"))

        # Query for CORS settings
        retrieved = self.memory.get(input="CORS origin configuration")
        self.assertGreater(len(retrieved), 0)
        contents = [m.content for m in retrieved]
        self.assertTrue(any("CORS" in c for c in contents))

    def test_get_all(self):
        """Tests retrieval of all chronological interaction messages."""
        self.memory.put(ChatMessage(role="user", content="Message 1"))
        self.memory.put(ChatMessage(role="assistant", content="Message 2"))

        all_msgs = self.memory.get_all()
        self.assertEqual(len(all_msgs), 2)
        self.assertEqual(all_msgs[0].content, "Message 1")
        self.assertEqual(all_msgs[1].content, "Message 2")

    def test_reset(self):
        """Tests memory flushing on reset()."""
        self.memory.put(ChatMessage(role="user", content="Ephemera"))
        self.memory.reset()
        self.assertEqual(len(self.memory.get_all()), 0)

    def test_set_messages(self):
        """Tests atomic message replacement via set()."""
        initial = [ChatMessage(role="user", content="Old 1"), ChatMessage(role="assistant", content="Old 2")]
        self.memory.set(initial)
        self.assertEqual(len(self.memory.get_all()), 2)

        replacement = [ChatMessage(role="system", content="New system prompt")]
        self.memory.set(replacement)
        all_msgs = self.memory.get_all()
        self.assertEqual(len(all_msgs), 1)
        self.assertEqual(all_msgs[0].content, "New system prompt")


if __name__ == "__main__":
    unittest.main()
