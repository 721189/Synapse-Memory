"""
SynapseMemory: Universal Active RAG & Cognitive Long-Term Memory Layer
Compatible with LangChain, LlamaIndex, and standalone LLM agents.
"""

from .core.knapsack_packer import KnapsackPacker
from .core.decay_engine import DecayEngine
from .core.hybrid_search import HybridSearch

__all__ = [
    "KnapsackPacker",
    "DecayEngine",
    "HybridSearch",
]

__version__ = "1.0.0"
