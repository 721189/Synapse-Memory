import os
from typing import List
from synapse_memory.core.embedding_provider import (
    EmbeddingManager,
    LocalEmbeddingProvider,
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformerEmbeddingProvider
)

class SynapseEmbedder:
    """
    Multi-model embedding client. Provides seamless failover between Google Gemini API,
    OpenAI API, SentenceTransformers, and deterministic hash vector providers.
    """

    def __init__(self, provider: Optional[str] = None, allow_fallback: Optional[bool] = None):
        env_provider = os.getenv("SYNAPSE_EMBEDDING_PROVIDER")
        self.provider_name = (provider or env_provider or "local").lower()
        self.model_name = os.getenv("SYNAPSE_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")

        is_production = os.getenv("NODE_ENV", "").lower() == "production"
        if allow_fallback is None:
            allow_fallback = not is_production

        provider_instance = self._get_provider(allow_fallback=allow_fallback)
        self.manager = EmbeddingManager(provider_instance)

        # Validate configured dimension if explicitly set
        configured_dim = os.getenv("SYNAPSE_EMBEDDING_DIMENSION")
        if configured_dim:
            expected = int(configured_dim)
            actual = self.manager.provider.dimension
            if actual != expected:
                raise RuntimeError(
                    f"Embedding dimension mismatch: provider '{self.provider_name}' produces {actual}-d vectors, "
                    f"but SYNAPSE_EMBEDDING_DIMENSION is configured as {expected}-d."
                )

    @property
    def dimension(self) -> int:
        return self.manager.provider.dimension

    @property
    def is_semantic(self) -> bool:
        return self.manager.provider.is_semantic

    def _get_provider(self, allow_fallback: bool = False):
        if self.provider_name in ("deterministic", "hash"):
            dim = int(os.getenv("SYNAPSE_EMBEDDING_DIMENSION", "128"))
            return LocalEmbeddingProvider(dim=dim)
        elif self.provider_name == "gemini" and self.gemini_key:
            return GeminiEmbeddingProvider(self.gemini_key)
        elif self.provider_name == "openai" and self.openai_key:
            return OpenAIEmbeddingProvider(self.openai_key)
        # Default to production-grade semantic local embedding
        return SentenceTransformerEmbeddingProvider(
            model_name=self.model_name,
            allow_fallback=allow_fallback
        )

    def embed_query(self, text: str) -> List[float]:
        """Generates a dense float vector representation for text strings."""
        if not text.strip():
            return [0.0] * self.manager.provider.dimension
        return self.manager.get_embedding(text)
