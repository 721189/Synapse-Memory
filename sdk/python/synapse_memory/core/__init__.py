# Expose core components for internal and public access
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker, MemoryNodeResult
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.deduplicator import Deduplicator, Action, DeduplicationResult
from synapse_memory.core.pruner import (
    PruningEngine,
    CapacityPruningPolicy,
    TTLPruningPolicy,
    ConfidencePruningPolicy,
)
from synapse_memory.core.embedding_provider import (
    EmbeddingManager,
    LocalEmbeddingProvider,
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from synapse_memory.core.encryption import EncryptedMemoryStore, FernetKeyManager

__all__ = [
    "SQLiteMemoryStore",
    "SynapseEmbedder",
    "MemoryManager",
    "HybridSearch",
    "KnapsackPacker",
    "MemoryNodeResult",
    "DecayEngine",
    "Deduplicator",
    "Action",
    "DeduplicationResult",
    "PruningEngine",
    "CapacityPruningPolicy",
    "TTLPruningPolicy",
    "ConfidencePruningPolicy",
    "EmbeddingManager",
    "LocalEmbeddingProvider",
    "GeminiEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "EncryptedMemoryStore",
    "FernetKeyManager",
]
