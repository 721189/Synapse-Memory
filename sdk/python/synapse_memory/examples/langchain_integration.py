"""
LangChain Integration Example and backward-compatible adapter.
Uses the production SynapseLangChainMemory from synapse_memory.integrations.langchain.
"""

from synapse_memory.integrations.langchain import (
    SynapseLangChainMemory,
    HAS_LANGCHAIN,
    BaseMemory,
)

__all__ = ["SynapseLangChainMemory", "HAS_LANGCHAIN", "BaseMemory"]
