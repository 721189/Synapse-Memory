"""
Production-grade LlamaIndex drop-in memory integration for Synapse Memory.
Compliant with llama_index.core.memory.BaseMemory.
"""

from typing import Dict, Any, List, Optional
import time
import logging

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine

logger = logging.getLogger("SynapseLlamaIndex")

try:
    from llama_index.core.memory import BaseMemory
    from llama_index.core.llms import ChatMessage, MessageRole
    HAS_LLAMAINDEX = True
except ImportError:
    class BaseMemory:
        pass

    class MessageRole:
        USER = "user"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    class ChatMessage:
        def __init__(self, role: str = MessageRole.USER, content: str = "", additional_kwargs: Optional[Dict[str, Any]] = None):
            self.role = role
            self.content = content
            self.additional_kwargs = additional_kwargs or {}

        def __repr__(self) -> str:
            return f"ChatMessage(role={self.role}, content={self.content[:30]}...)"

    HAS_LLAMAINDEX = False


class SynapseLlamaIndexMemory(BaseMemory):
    """
    Drop-in production memory module for LlamaIndex query and chat engines.
    Inherits directly from llama_index.core.memory.BaseMemory when available.

    Features:
    - Backed by persistent encrypted SQLite store.
    - Automatic semantic deduplication on message ingestion.
    - Real hybrid BM25 + dense vector retrieval.
    - Knapsack token packing within max_token_limit.
    - Full ChatMessage role preservation.
    """

    def __init__(
        self,
        max_token_limit: int = 3072,
        db_path: str = ":memory:",
        store: Optional[SQLiteMemoryStore] = None,
        embedder: Optional[SynapseEmbedder] = None,
        memory_manager: Optional[MemoryManager] = None,
        **kwargs: Any
    ):
        if HAS_LLAMAINDEX:
            super().__init__(**kwargs)

        self.max_token_limit = max_token_limit
        self.store = store or SQLiteMemoryStore(db_path=db_path)
        self.embedder = embedder or SynapseEmbedder(provider="local")
        self.manager = memory_manager or MemoryManager(
            store=self.store,
            embedder=self.embedder,
            max_record_limit=kwargs.get("max_record_limit", 500)
        )
        self.searcher = HybridSearch(alpha=0.6, k=60)
        self.packer = KnapsackPacker(token_budget=max_token_limit)
        self.decay = DecayEngine()

    def get(self, input: Optional[str] = None, **kwargs: Any) -> List[ChatMessage]:
        """
        Retrieves relevant historical chat messages, applying hybrid search and knapsack packing.
        """
        all_memories = self.store.get_all_memories()
        if not all_memories:
            return []

        if input and input.strip():
            query_vector = self.embedder.embed_query(input)
            ranked_nodes = self.searcher.search(
                query=input,
                corpus=all_memories,
                query_vector=query_vector,
                top_k=min(len(all_memories), 20)
            )
        else:
            ranked_nodes = sorted(
                all_memories,
                key=lambda m: m.get("created_at", 0),
                reverse=True
            )[:20]

        now = time.time()
        for node in ranked_nodes:
            base_score = float(node.get("score", node.get("confidence", 0.8)))
            node["relevance_score"] = self.decay.calculate_relevance(node, now=now) * base_score

        packed_memories = self.packer.pack(ranked_nodes, token_budget=self.max_token_limit)

        messages: List[ChatMessage] = []
        for node in packed_memories:
            self.store.update_access_count(node["id"], 1)
            content = node.get("content", "")
            role = str(node.get("category", "user"))
            if role not in ["user", "assistant", "system"]:
                role = "user"
            messages.append(ChatMessage(role=role, content=content))

        return messages

    def get_all(self) -> List[ChatMessage]:
        """Returns all messages currently persisted in the memory store."""
        all_memories = sorted(
            self.store.get_all_memories(),
            key=lambda m: m.get("created_at", 0)
        )
        messages: List[ChatMessage] = []
        for node in all_memories:
            role = str(node.get("category", "user"))
            if role not in ["user", "assistant", "system"]:
                role = "user"
            messages.append(ChatMessage(role=role, content=node.get("content", "")))
        return messages

    def put(self, message: Any) -> None:
        """
        Stores a ChatMessage (or plain text) into the encrypted store with semantic deduplication.
        """
        if isinstance(message, ChatMessage):
            content = message.content
            role = str(message.role)
        elif isinstance(message, dict):
            content = message.get("content", "")
            role = message.get("role", "user")
        else:
            content = str(message)
            role = "user"

        if not content.strip():
            return

        self.manager.ingest_with_deduplication(
            content=content,
            category=role,
            confidence=0.95
        )

    def set(self, messages: List[Any]) -> None:
        """Resets the store and inserts the provided message sequence."""
        self.reset()
        for msg in messages:
            self.put(msg)

    def reset(self) -> None:
        """Clears all stored memories."""
        all_mems = self.store.get_all_memories()
        for mem in all_mems:
            self.store.delete_memory(mem["id"])
