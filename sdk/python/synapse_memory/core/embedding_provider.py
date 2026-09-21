from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import time
import logging
import os
import json
import urllib.request
from synapse_memory.core.observability import log_event, instrument_operation

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
    """Basic local embedding provider for deterministic testing."""
    def __init__(self, dim: int = 128):
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> List[float]:
        # Deterministic dummy embedding for testing
        vector = [float(ord(c) % 100) / 100.0 for c in text[:self._dim]]
        # Pad if text is shorter than dimension
        if len(vector) < self._dim:
            vector.extend([0.0] * (self._dim - len(vector)))
        return vector
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, dim: int = 768):
        self.api_key = api_key
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @instrument_operation("gemini_embed")
    def embed(self, text: str) -> List[float]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"content": {"parts": [{"text": text}]}}
        
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
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
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload = {"input": text, "model": "text-embedding-3-small"}
        
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["data"][0]["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

class EmbeddingManager:
    """Manager to handle provider switching, caching, and retries."""
    def __init__(self, provider: EmbeddingProvider, cache_enabled: bool = True):
        self.provider = provider
        self.cache: Dict[str, List[float]] = {}
        self.cache_enabled = cache_enabled

    def get_embedding(self, text: str) -> List[float]:
        if self.cache_enabled and text in self.cache:
            log_event("embedding_cache", "HIT", {"text_len": len(text)})
            return self.cache[text]
        
        log_event("embedding_cache", "MISS", {"text_len": len(text)})
            
        # Add simple retry logic
        retries = 3
        for i in range(retries):
            try:
                embedding = self.provider.embed(text)
                if len(embedding) != self.provider.dimension:
                    raise ValueError(f"Dimension mismatch: expected {self.provider.dimension}, got {len(embedding)}")
                
                if self.cache_enabled:
                    self.cache[text] = embedding
                return embedding
            except Exception as e:
                log_event("embedding_retry", "RETRYING", {"attempt": i+1, "error": str(e)})
                if i == retries - 1:
                    raise e
                time.sleep(1) # Linear backoff
        return []
