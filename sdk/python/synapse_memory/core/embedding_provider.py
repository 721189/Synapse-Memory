import json
import logging
import math
import re
import time
import urllib.request
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import List

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


class LocalEmbeddingProvider(EmbeddingProvider):
    """
    Local embedding provider for deterministic testing, offline operation,
    and fast semantic similarity without heavy external model binaries.
    """
    def __init__(self, dim: int = 128):
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

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


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """
    Production-grade local embedding provider using sentence-transformers.
    Gracefully falls back to LocalEmbeddingProvider if the heavy dependency is absent.
    """
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._fallback = None
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self._dim = int(self._model.get_sentence_embedding_dimension())
        except Exception as err:
            logger.warning(
                f"sentence-transformers unavailable ({err}); using local fallback."
            )
            self._fallback = LocalEmbeddingProvider()
            self._dim = self._fallback.dimension

    @property
    def dimension(self) -> int:
        return self._dim

    @instrument_operation("st_embed")
    def embed(self, text: str) -> List[float]:
        if self._model is not None:
            return self._model.encode(text).tolist()
        if self._fallback is None:
            self._fallback = LocalEmbeddingProvider()
        return self._fallback.embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if self._model is not None:
            return self._model.encode(texts).tolist()
        if self._fallback is None:
            self._fallback = LocalEmbeddingProvider()
        return self._fallback.embed_batch(texts)


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, dim: int = 768):
        self.api_key = api_key
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

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
    def __init__(self, api_key: str, dim: int = 1536):
        self.api_key = api_key
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @instrument_operation("openai_embed")
    def embed(self, text: str) -> List[float]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {"input": text, "model": "text-embedding-3-small"}

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
    """Manager to handle provider switching, caching, and retries."""
    def __init__(self, provider: EmbeddingProvider, cache_size: int = 1000):
        self.provider = provider
        self.cache_size = cache_size
        self._cached_embed = lru_cache(maxsize=cache_size)(self.provider.embed)

    def get_embedding(self, text: str) -> List[float]:
        log_event("embedding_manager", "REQUEST", {"text_len": len(text)})

        retries = 3
        for i in range(retries):
            try:
                embedding = self._cached_embed(text)
                if len(embedding) != self.provider.dimension:
                    raise ValueError(
                        f"Dimension mismatch: expected {self.provider.dimension}, "
                        f"got {len(embedding)}"
                    )
                return embedding
            except Exception as e:
                log_event(
                    "embedding_retry",
                    "RETRYING",
                    {"attempt": i + 1, "error": str(e)}
                )
                if i == retries - 1:
                    raise e
                time.sleep(1)
        return []

