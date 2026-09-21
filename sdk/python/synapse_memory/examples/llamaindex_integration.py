"""
LlamaIndex Integration Example and backward-compatible adapter.
Uses the production SynapseLlamaIndexMemory from synapse_memory.integrations.llamaindex.
"""

from synapse_memory.integrations.llamaindex import (
    SynapseLlamaIndexMemory,
    HAS_LLAMAINDEX,
    BaseMemory,
)

__all__ = ["SynapseLlamaIndexMemory", "HAS_LLAMAINDEX", "BaseMemory"]
