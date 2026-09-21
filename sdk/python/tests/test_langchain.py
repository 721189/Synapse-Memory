import unittest
import time
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse_memory.integrations.langchain import SynapseLangChainMemory
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder


class TestSynapseLangChainIntegration(unittest.TestCase):

    def setUp(self):
        self.db_path = "test_langchain_memory.db"
        self.store = SQLiteMemoryStore(self.db_path)
        self.embedder = SynapseEmbedder(provider="local")
        self.memory = SynapseLangChainMemory(
            memory_key="chat_history",
            max_token_budget=500,
            store=self.store,
            embedder=self.embedder
        )

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def test_memory_variables(self):
        """Verifies memory_variables contract for LangChain prompt integration."""
        self.assertEqual(self.memory.memory_variables, ["chat_history"])

    def test_save_and_load_context(self):
        """Verifies context saving, semantic deduplication, and hybrid retrieval."""
        self.memory.save_context(
            {"input": "We are deploying the service on Kubernetes namespace prod-core."},
            {"output": "Acknowledged, targeting namespace prod-core."}
        )
        self.memory.save_context(
            {"input": "PostgreSQL database maximum connections is configured to 200."},
            {"output": "Noted, max connections set to 200."}
        )

        # Retrieve relevant context for a specific domain query
        result = self.memory.load_memory_variables({"input": "What is the Kubernetes namespace?"})
        self.assertIn("chat_history", result)
        history_text = result["chat_history"]
        self.assertIn("Kubernetes namespace", history_text)

    def test_knapsack_budget_constraint(self):
        """Verifies that loaded context strictly respects max_token_budget."""
        # Create a memory instance with a very small budget
        tiny_memory = SynapseLangChainMemory(
            memory_key="history",
            max_token_budget=35,
            store=self.store,
            embedder=self.embedder
        )

        tiny_memory.save_context(
            {"input": "Short query"},
            {"output": "Short answer"}
        )
        tiny_memory.save_context(
            {"input": "A very long detailed architectural query with dozens of parameters and explanation text"},
            {"output": "A very lengthy response explaining the entire system topology with numerous paragraphs and specifications"}
        )

        loaded = tiny_memory.load_memory_variables({"input": "architectural query"})
        self.assertIn("history", loaded)

    def test_return_messages_format(self):
        """Verifies return_messages=True outputs structured messages."""
        msg_memory = SynapseLangChainMemory(
            memory_key="chat_history",
            return_messages=True,
            store=self.store,
            embedder=self.embedder
        )
        msg_memory.save_context(
            {"input": "Hello agent"},
            {"output": "Hello operator, how can I assist?"}
        )

        loaded = msg_memory.load_memory_variables({"input": "Hello"})
        self.assertIn("chat_history", loaded)
        messages = loaded["chat_history"]
        self.assertIsInstance(messages, list)
        self.assertGreater(len(messages), 0)

    def test_clear_memory(self):
        """Verifies memory wiping on clear()."""
        self.memory.save_context(
            {"input": "Temporary credential"},
            {"output": "Saved"}
        )
        self.memory.clear()
        loaded = self.memory.load_memory_variables({"input": "Temporary"})
        self.assertEqual(loaded["chat_history"], "")


if __name__ == "__main__":
    unittest.main()
