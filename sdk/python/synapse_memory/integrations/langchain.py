"""
Production-grade LangChain drop-in memory integration for Synapse Memory.
Compliant with langchain_core.memory.BaseMemory.
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

logger = logging.getLogger("SynapseLangChain")

# Dynamic import for langchain_core with full schema parity fallback
try:
    from langchain_core.memory import BaseMemory
    from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
    HAS_LANGCHAIN = True
except ImportError:
    class BaseMemory:
        pass

    class BaseMessage:
        def __init__(self, content: str):
            self.content = content

    class HumanMessage(BaseMessage):
        pass

    class AIMessage(BaseMessage):
        pass

    HAS_LANGCHAIN = False


class SynapseLangChainMemory(BaseMemory):
    """
    Drop-in production memory module for LangChain chains and agents.
    Inherits directly from langchain_core.memory.BaseMemory when available.

    Features:
    - Backed by persistent encrypted SQLite store.
    - Automatic semantic deduplication on save_context.
    - Real hybrid BM25 + dense vector search on load_memory_variables.
    - Ebbinghaus continuous decay scoring.
    - Exact Knapsack token budget packing into max_token_budget.
    - Support for both string-formatted history and message list formats.
    """

    memory_key: str = "chat_history"
    input_key: Optional[str] = None
    output_key: Optional[str] = None
    return_messages: bool = False
    human_prefix: str = "Human"
    ai_prefix: str = "AI"
    max_token_budget: int = 2048

    def __init__(
        self,
        memory_key: str = "chat_history",
        input_key: Optional[str] = None,
        output_key: Optional[str] = None,
        return_messages: bool = False,
        human_prefix: str = "Human",
        ai_prefix: str = "AI",
        max_token_budget: int = 2048,
        db_path: str = ":memory:",
        store: Optional[SQLiteMemoryStore] = None,
        embedder: Optional[SynapseEmbedder] = None,
        memory_manager: Optional[MemoryManager] = None,
        **kwargs: Any
    ):
        if HAS_LANGCHAIN:
            super().__init__(**kwargs)

        self.memory_key = memory_key
        self.input_key = input_key
        self.output_key = output_key
        self.return_messages = return_messages
        self.human_prefix = human_prefix
        self.ai_prefix = ai_prefix
        self.max_token_budget = max_token_budget

        # Initialize underlying Synapse core substrate
        self.store = store or SQLiteMemoryStore(db_path=db_path)
        self.embedder = embedder or SynapseEmbedder(provider="local")
        self.manager = memory_manager or MemoryManager(
            store=self.store,
            embedder=self.embedder,
            max_record_limit=kwargs.get("max_record_limit", 500)
        )
        self.searcher = HybridSearch(alpha=0.6, k=60)
        self.packer = KnapsackPacker(token_budget=max_token_budget)
        self.decay = DecayEngine()

    @property
    def memory_variables(self) -> List[str]:
        """Exposes memory variables to LangChain prompt templates."""
        return [self.memory_key]

    def _get_input_output(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any]
    ) -> tuple[str, str]:
        if self.input_key:
            user_input = inputs.get(self.input_key, "")
        else:
            prompt_keys = [k for k in inputs.keys() if k not in self.memory_variables]
            user_input = str(inputs[prompt_keys[0]]) if prompt_keys else ""

        if self.output_key:
            model_output = outputs.get(self.output_key, "")
        else:
            out_keys = list(outputs.keys())
            model_output = str(outputs[out_keys[0]]) if out_keys else ""

        return str(user_input), str(model_output)

    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves relevant historical memories, applies Ebbinghaus decay relevance scores,
        solves context limits via Knapsack DP, and returns formatted context.
        """
        query_text = (
            inputs.get("input")
            or inputs.get("question")
            or inputs.get("query")
            or (inputs.get(self.input_key) if self.input_key else None)
            or ""
        )

        all_memories = self.store.get_all_memories()
        if not all_memories:
            return {self.memory_key: [] if self.return_messages else ""}

        # If query is provided, perform full Hybrid Search
        if query_text.strip():
            query_vector = self.embedder.embed_query(query_text)
            ranked_nodes = self.searcher.search(
                query=query_text,
                corpus=all_memories,
                query_vector=query_vector,
                top_k=min(len(all_memories), 20)
            )
        else:
            # Fallback to recency order if no active query
            ranked_nodes = sorted(
                all_memories,
                key=lambda m: m.get("created_at", 0),
                reverse=True
            )[:20]

        # Apply continuous temporal decay relevance scoring
        now = time.time()
        for node in ranked_nodes:
            base_score = float(node.get("score", node.get("confidence", 0.8)))
            node["relevance_score"] = self.decay.calculate_relevance(node, now=now) * base_score

        # Solve Knapsack token constraint
        packed_memories = self.packer.pack(ranked_nodes, token_budget=self.max_token_budget)

        # Update access counters
        for node in packed_memories:
            self.store.update_access_count(node["id"], 1)

        # Format output as messages or formatted string
        if self.return_messages:
            messages: List[Any] = []
            for node in packed_memories:
                content = node.get("content", "")
                if " -> " in content and (self.human_prefix in content or "User:" in content):
                    parts = content.split(" -> ", 1)
                    u_text = parts[0].replace(f"{self.human_prefix}: ", "").replace("User: ", "")
                    a_text = parts[1].replace(f"{self.ai_prefix}: ", "").replace("Agent: ", "").replace("AI: ", "")
                    messages.append(HumanMessage(content=u_text))
                    messages.append(AIMessage(content=a_text))
                else:
                    messages.append(HumanMessage(content=content))
            return {self.memory_key: messages}
        else:
            formatted_nodes = [f"[{node.get('category', 'context').upper()}]: {node.get('content', '')}" for node in packed_memories]
            return {self.memory_key: "\n".join(formatted_nodes)}

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> None:
        """
        Saves user/assistant conversation turn into encrypted memory store with deduplication.
        """
        user_input, model_output = self._get_input_output(inputs, outputs)
        if not user_input and not model_output:
            return

        combined_content = f"{self.human_prefix}: {user_input} -> {self.ai_prefix}: {model_output}"

        # Ingest through memory manager to trigger semantic deduplication and auto-pruning
        self.manager.ingest_with_deduplication(
            content=combined_content,
            category="conversation",
            confidence=0.95
        )

    def clear(self) -> None:
        """Clears all stored memories."""
        all_mems = self.store.get_all_memories()
        for mem in all_mems:
            self.store.delete_memory(mem["id"])
