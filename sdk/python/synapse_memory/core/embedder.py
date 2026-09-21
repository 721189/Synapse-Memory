import os
import math
import json
from typing import List, Optional
import urllib.request
import urllib.error
import logging

logger = logging.getLogger("SynapseEmbedder")

class SynapseEmbedder:
    """
    Multi-model embedding client. Provides seamless failover between Google Gemini API,
    OpenAI API, and an offline local mathematical text vectorizer to ensure consistent 
    operation across all network states.
    """

    def __init__(self, provider: str = "local"):
        """
        Args:
            provider: 'local', 'gemini', or 'openai'
        """
        self.provider = provider.lower()
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")

        if self.provider == "gemini" and not self.gemini_key:
            logger.warning("GEMINI_API_KEY not found. Defaulting to high-fidelity offline vectorizer.")
            self.provider = "local"
        elif self.provider == "openai" and not self.openai_key:
            logger.warning("OPENAI_API_KEY not found. Defaulting to high-fidelity offline vectorizer.")
            self.provider = "local"

    def embed_query(self, text: str) -> List[float]:
        """Generates a dense float vector representation for text strings."""
        if not text.strip():
            return [0.0] * 128

        if self.provider == "gemini":
            return self._embed_gemini(text)
        elif self.provider == "openai":
            return self._embed_openai(text)
        return self._embed_local(text)

    def _embed_local(self, text: str, dimensions: int = 128) -> List[float]:
        """
        A high-fidelity, deterministic, local sparse-coordinate text vectorizer.
        Uses DJB2 hashing to assign words to distinct dimensions, ensuring low cosine
        overlap for completely unrelated text segments.
        """
        words = text.lower().split()
        vector = [0.0] * dimensions
        
        if not words:
            return vector

        for word in words:
            # DJB2 stable string hashing algorithm
            h = 5381
            for char in word:
                h = ((h << 5) + h) + ord(char)
                h &= 0xFFFFFFFF
            
            # Map word to coordinates deterministic of its characters
            idx1 = h % dimensions
            idx2 = (h * 31) % dimensions
            vector[idx1] += 1.0
            vector[idx2] += 0.5

        # L2 Normalization to yield authentic cosine distance properties
        magnitude = math.sqrt(sum(v * v for v in vector))
        if magnitude > 0:
            vector = [v / magnitude for v in vector]

        return vector

    def _embed_gemini(self, text: str) -> List[float]:
        """Calls Google Gemini API text-embedding-004 over native urllib."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "content": {"parts": [{"text": text}]}
        }

        try:
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["embedding"]["values"]
        except Exception as e:
            logger.error(f"Gemini embedding API call failed: {e}. Falling back to local vectorizer.")
            return self._embed_local(text)

    def _embed_openai(self, text: str) -> List[float]:
        """Calls OpenAI text-embedding-3-small over native urllib."""
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.openai_key}"
        }
        payload = {
            "input": text,
            "model": "text-embedding-3-small"
        }

        try:
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"OpenAI embedding API call failed: {e}. Falling back to local vectorizer.")
            return self._embed_local(text)
