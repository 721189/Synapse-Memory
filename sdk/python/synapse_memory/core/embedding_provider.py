import json
import logging
import math
import re
import time
import urllib.request
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import List, Optional

from synapse_memory.core.observability import instrument_operation, log_event

logger = logging.getLogger("EmbeddingProvider")


class EmbeddingProvider(ABC):
    """Base class for all embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        pass

    @property
    def is_semantic(self) -> bool:
        return True


class DeterministicHashEmbeddingProvider(EmbeddingProvider):
    """
    Local deterministic hash provider for offline unit testing and development.
    NOTE: This generates deterministic word-frequency hash projections, NOT neural semantic embeddings.
    """
    def __init__(self, dim: int = 128):
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_semantic(self) -> bool:
        return False

    def embed(self, text: str) -> List[float]:
        # Normalize text (lowercase and strip non-alphanumeric characters)
        normalized = re.sub(r"[^\w\s]", "", text.lower()).strip()
        words = normalized.split()
        if not words:
            return [0.0] * self._dim

        vector = [0.0] * self._dim
        for i, word in enumerate(words):
            h = hash(word)
            idx = abs(h) % self._dim
            sign = 1.0 if (h > 0) else -1.0
            vector[idx] += sign * (1.0 / (1.0 + 0.05 * i))

        # L2 normalization for accurate cosine similarities
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]
        return vector

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


# Alias for backward compatibility
LocalEmbeddingProvider = DeterministicHashEmbeddingProvider


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """
    Production-grade local neural semantic embedding provider using sentence-transformers.
    Requires: sentence-transformers (pip install 'synapse-memory[local-models]')
    """
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", allow_fallback: bool = False):
        self.model_name = model_name
        self._model = None
        self._fallback: Optional[DeterministicHashEmbeddingProvider] = None
        self._is_using_real_model = False

        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self._dim = int(self._model.get_sentence_embedding_dimension())
            self._is_using_real_model = True
            logger.info(f"Loaded neural embedding model '{model_name}' (dim: {self._dim}).")
        except Exception as err:
            if not allow_fallback:
                raise ImportError(
                    f"Failed to load semantic embedding model '{model_name}': {err}. "
                    "For production neural embeddings, install sentence-transformers or set provider='deterministic'."
                ) from err
            logger.warning(
                f"[SynapseEmbedder Warning] sentence-transformers not installed ({err}). "
                "Falling back to DeterministicHashEmbeddingProvider for non-production development. "
                "For production neural embeddings, install with: pip install sentence-transformers"
            )
            self._fallback = DeterministicHashEmbeddingProvider()
            self._dim = self._fallback.dimension

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_semantic(self) -> bool:
        return self._is_using_real_model

    @instrument_operation("st_embed")
    def embed(self, text: str) -> List[float]:
        if self._model is not None:
            return self._model.encode(text).tolist()
        if self._fallback is None:
            self._fallback = DeterministicHashEmbeddingProvider()
        return self._fallback.embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if self._model is not None:
            return self._model.encode(texts).tolist()
        if self._fallback is None:
            self._fallback = DeterministicHashEmbeddingProvider()
        return self._fallback.embed_batch(texts)


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, dim: int = 768):
        self.api_key = api_key
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_semantic(self) -> bool:
        return True

    @instrument_operation("gemini_embed")
    def embed(self, text: str) -> List[float]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"text-embedding-004:embedContent?key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}
        payload = {"content": {"parts": [{"text": text}]}}

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["embedding"]["values"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small", dim: int = 1536):
        self.api_key = api_key
        self.model = model
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_semantic(self) -> bool:
        return True

    @instrument_operation("openai_embed")
    def embed(self, text: str) -> List[float]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "input": text,
            "model": self.model
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["data"][0]["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class EmbeddingManager:
    """
    Manages embedding provider operations with caching and observability.
    """
    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider

    @lru_cache(maxsize=1024)
    def get_embedding(self, text: str) -> List[float]:
        return self.provider.embed(text)

    def get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self.provider.embed_batch(texts)
