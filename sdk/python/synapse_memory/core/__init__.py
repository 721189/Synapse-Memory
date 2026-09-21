# Expose core components for internal access
from synapse_memory.core.embedding_provider import EmbeddingManager, LocalEmbeddingProvider, GeminiEmbeddingProvider, OpenAIEmbeddingProvider
from synapse_memory.core.deduplicator import Deduplicator
from synapse_memory.core.pruner import PruningEngine, CapacityPruningPolicy
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.knapsack_packer import KnapsackPacker

__all__ = [
    "EmbeddingManager", "LocalEmbeddingProvider", "GeminiEmbeddingProvider", 
    "OpenAIEmbeddingProvider", "Deduplicator", "PruningEngine", 
    "CapacityPruningPolicy", "DecayEngine", "KnapsackPacker"
]
