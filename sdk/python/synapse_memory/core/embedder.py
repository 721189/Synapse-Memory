import logging
import os
from typing import List, Optional
from synapse_memory.core.embedding_provider import (
    EmbeddingManager,
    LocalEmbeddingProvider,
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformerEmbeddingProvider
)

logger = logging.getLogger("SynapseEmbedder")


class SynapseEmbedder:
    """
    Multi-model embedding client. Provides seamless failover between Google Gemini API,
    OpenAI API, SentenceTransformers, and deterministic hash vector providers.
    """

    def __init__(self, provider: Optional[str] = None, allow_fallback: Optional[bool] = None):
        env_provider = os.getenv("SYNAPSE_EMBEDDING_PROVIDER")
        self.provider_name = (provider or env_provider or "deterministic").lower()
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
        if self.provider_name in ("deterministic", "hash", "local"):
            # Deterministic hash provider: always available, no external deps,
            # safe default for production when no semantic provider is configured.
            dim = int(os.getenv("SYNAPSE_EMBEDDING_DIMENSION", "128"))
            return LocalEmbeddingProvider(dim=dim)
        elif self.provider_name == "gemini":
            if not self.gemini_key:
                if allow_fallback:
                    logger.warning(
                        "GEMINI_API_KEY not set; falling back to deterministic hash provider."
                    )
                    dim = int(os.getenv("SYNAPSE_EMBEDDING_DIMENSION", "128"))
                    return LocalEmbeddingProvider(dim=dim)
                raise RuntimeError(
                    "SYNAPSE_EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set. "
                    "Set GEMINI_API_KEY or switch to provider=deterministic."
                )
            return GeminiEmbeddingProvider(self.gemini_key)
        elif self.provider_name == "openai":
            if not self.openai_key:
                if allow_fallback:
                    logger.warning(
                        "OPENAI_API_KEY not set; falling back to deterministic hash provider."
                    )
                    dim = int(os.getenv("SYNAPSE_EMBEDDING_DIMENSION", "128"))
                    return LocalEmbeddingProvider(dim=dim)
                raise RuntimeError(
                    "SYNAPSE_EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set. "
                    "Set OPENAI_API_KEY or switch to provider=deterministic."
                )
            return OpenAIEmbeddingProvider(self.openai_key)
        elif self.provider_name == "sentence-transformers":
            # Explicit opt-in: requires [local-models] extra and model availability.
            return SentenceTransformerEmbeddingProvider(
                model_name=self.model_name,
                allow_fallback=allow_fallback
            )
        # Unknown provider: fail closed in production, fall back in dev.
        if allow_fallback:
            logger.warning(
                f"Unknown embedding provider '{self.provider_name}'; falling back to deterministic hash provider."
            )
            dim = int(os.getenv("SYNAPSE_EMBEDDING_DIMENSION", "128"))
            return LocalEmbeddingProvider(dim=dim)
        raise RuntimeError(
            f"Unknown embedding provider '{self.provider_name}'. "
            "Use one of: deterministic, gemini, openai, sentence-transformers."
        )

    def embed_query(self, text: str) -> List[float]:
        """Generates a dense float vector representation for text strings."""
        if not text.strip():
            return [0.0] * self.manager.provider.dimension
        return self.manager.get_embedding(text)
