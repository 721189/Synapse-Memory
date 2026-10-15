"""
Production-grade LlamaIndex drop-in memory integration for Synapse Memory.
Compliant with llama_index.core.memory.BaseMemory.
"""

from typing import Dict, Any, List, Optional, cast
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
    from llama_index.core.bridge.pydantic import Field, PrivateAttr
    HAS_LLAMAINDEX = True
except ImportError:
    class BaseMemory:  # type: ignore[no-redef]
        pass

    def Field(default: Any = None, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return default

    def PrivateAttr(default: Any = None, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return default

    class MessageRole:  # type: ignore[no-redef]
        USER = "user"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    class ChatMessage:  # type: ignore[no-redef]
        def __init__(
            self,
            role: Any = MessageRole.USER,
            content: str = "",
            additional_kwargs: Optional[Dict[str, Any]] = None,
        ):
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

    max_token_limit: int = Field(default=3072)
    db_path: str = Field(default=":memory:")
    max_record_limit: int = Field(default=500)

    _store: Optional[SQLiteMemoryStore] = PrivateAttr(default=None)
    _embedder: Optional[SynapseEmbedder] = PrivateAttr(default=None)
    _manager: Optional[MemoryManager] = PrivateAttr(default=None)
    _searcher: Optional[HybridSearch] = PrivateAttr(default=None)
    _packer: Optional[KnapsackPacker] = PrivateAttr(default=None)
    _decay: Optional[DecayEngine] = PrivateAttr(default=None)

    def __init__(
        self,
        max_token_limit: int = 3072,
        db_path: str = ":memory:",
        store: Optional[SQLiteMemoryStore] = None,
        embedder: Optional[SynapseEmbedder] = None,
        memory_manager: Optional[MemoryManager] = None,
        max_record_limit: int = 500,
        **kwargs: Any
    ):
        if HAS_LLAMAINDEX:
            super().__init__(
                max_token_limit=max_token_limit,
                db_path=db_path,
                max_record_limit=max_record_limit,
                **kwargs,
            )
        else:
            self.max_token_limit = max_token_limit
            self.db_path = db_path
            self.max_record_limit = max_record_limit

        self._store = store or SQLiteMemoryStore(db_path=db_path)
        self._embedder = embedder or SynapseEmbedder(provider="local")
        self._manager = memory_manager or MemoryManager(
            store=self.store,
            embedder=self.embedder,
            max_record_limit=kwargs.get("max_record_limit", max_record_limit),
        )
        self._searcher = HybridSearch(alpha=0.6, k=60)
        self._packer = KnapsackPacker(token_budget=max_token_limit)
        self._decay = DecayEngine()

    @property
    def store(self) -> SQLiteMemoryStore:
        store = self._store
        assert store is not None
        return store

    @store.setter
    def store(self, value: SQLiteMemoryStore) -> None:
        self._store = value

    @property
    def embedder(self) -> SynapseEmbedder:
        embedder = self._embedder
        assert embedder is not None
        return embedder

    @embedder.setter
    def embedder(self, value: SynapseEmbedder) -> None:
        self._embedder = value

    @property
    def manager(self) -> MemoryManager:
        manager = self._manager
        assert manager is not None
        return manager

    @manager.setter
    def manager(self, value: MemoryManager) -> None:
        self._manager = value

    @property
    def searcher(self) -> HybridSearch:
        searcher = self._searcher
        assert searcher is not None
        return searcher

    @searcher.setter
    def searcher(self, value: HybridSearch) -> None:
        self._searcher = value

    @property
    def packer(self) -> KnapsackPacker:
        packer = self._packer
        assert packer is not None
        return packer

    @packer.setter
    def packer(self, value: KnapsackPacker) -> None:
        self._packer = value

    @property
    def decay(self) -> DecayEngine:
        decay = self._decay
        assert decay is not None
        return decay

    @decay.setter
    def decay(self, value: DecayEngine) -> None:
        self._decay = value

    @classmethod
    def from_defaults(cls, **kwargs: Any) -> "SynapseLlamaIndexMemory":
        """Create a memory instance from defaults (llama_index BaseMemory contract)."""
        chat_history = kwargs.pop("chat_history", None)
        instance = cls(**kwargs)
        if chat_history:
            for msg in chat_history:
                instance.put(msg)
        return instance

    @staticmethod
    def _role_value(role: Any) -> str:
        value = getattr(role, "value", role)
        return str(value) if value is not None else "user"

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    text = getattr(block, "text", None)
                    parts.append(str(text) if text is not None else str(block))
            return "".join(parts)
        return str(content) if content is not None else ""

    def _to_canonical_role(self, role: Any) -> str:
        normalized = self._role_value(role).lower()
        if normalized in ("user", "assistant", "system"):
            return normalized
        return "user"

    def get(self, input: Optional[str] = None, **kwargs: Any) -> List[ChatMessage]:  # type: ignore[override]
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
            role = self._to_canonical_role(node.get("category", "user"))
            messages.append(ChatMessage(role=role, content=content))  # type: ignore[arg-type]

        return messages

    def get_all(self) -> List[ChatMessage]:
        """Returns all messages currently persisted in the memory store."""
        all_memories = sorted(
            self.store.get_all_memories(),
            key=lambda m: m.get("created_at", 0),
        )
        messages: List[ChatMessage] = []
        for node in all_memories:
            role = self._to_canonical_role(node.get("category", "user"))
            messages.append(ChatMessage(role=role, content=node.get("content", "")))  # type: ignore[arg-type]
        return messages

    def put(self, message: Any) -> None:  # type: ignore[override]
        """
        Stores a ChatMessage (or plain text) into the encrypted store with semantic deduplication.
        """
        if isinstance(message, ChatMessage):
            content = self._content_text(message.content)
            role = self._to_canonical_role(message.role)
        elif isinstance(message, dict):
            content = self._content_text(message.get("content", ""))
            role = self._to_canonical_role(message.get("role", "user"))
        else:
            content = self._content_text(message)
            role = "user"

        if not content.strip():
            return

        self.manager.ingest_with_deduplication(
            content=content,
            category=role,
            confidence=0.95
        )

    def set(self, messages: List[Any]) -> None:  # type: ignore[override]
        """Resets the store and inserts the provided message sequence."""
        self.reset()
        for msg in messages:
            self.put(msg)

    def reset(self) -> None:  # type: ignore[override]
        """Clears all stored memories."""
        all_mems = self.store.get_all_memories()
        for mem in all_mems:
            self.store.delete_memory(mem["id"])
