import os
from typing import List
from synapse_memory.core.embedding_provider import (
    EmbeddingManager, 
    LocalEmbeddingProvider, 
    GeminiEmbeddingProvider, 
    OpenAIEmbeddingProvider
)

class SynapseEmbedder:
    """
    Multi-model embedding client. Provides seamless failover between Google Gemini API,
    OpenAI API, and an offline local mathematical text vectorizer to ensure consistent 
    operation across all network states.
    """

    def __init__(self, provider: str = "local"):
        self.provider_name = provider.lower()
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")
        
        provider_instance = self._get_provider()
        self.manager = EmbeddingManager(provider_instance)

    def _get_provider(self):
        if self.provider_name == "gemini" and self.gemini_key:
            return GeminiEmbeddingProvider(self.gemini_key)
        elif self.provider_name == "openai" and self.openai_key:
            return OpenAIEmbeddingProvider(self.openai_key)
        return LocalEmbeddingProvider()

    def embed_query(self, text: str) -> List[float]:
        """Generates a dense float vector representation for text strings."""
        if not text.strip():
            return [0.0] * self.manager.provider.dimension
        return self.manager.get_embedding(text)
