"""P0: the shipped production default provider is 3072-d, not the test vectors.

The engine refuses a dimension mismatch at write time, so defaults must agree:
Helm and Compose must name a semantic provider (gemini = 3072-d) and never the
deterministic 128-d provider, which production refuses outright.
"""
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from synapse_memory.core.embedding_provider import GeminiEmbeddingProvider, OpenAIEmbeddingProvider
from synapse_memory.core.embedder import SynapseEmbedder

ROOT = Path(__file__).resolve().parents[3]



class TestConfiguredProviderOutputDimensions(unittest.TestCase):
    def test_gemini_provider_sends_requested_output_dimensionality(self):
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = json.dumps(
            {"embedding": {"values": [0.0] * 768}}
        ).encode("utf-8")
        with patch(
            "synapse_memory.core.embedding_provider.urllib.request.urlopen",
            return_value=mock_response,
        ) as urlopen:
            vector = GeminiEmbeddingProvider("test-key", dim=768).embed("test text")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["embedContentConfig"]["outputDimensionality"], 768)
        self.assertEqual(len(vector), 768)

    def test_openai_embedding_v3_sends_requested_dimensions(self):
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = json.dumps(
            {"data": [{"embedding": [0.0] * 1024}]}
        ).encode("utf-8")
        with patch(
            "synapse_memory.core.embedding_provider.urllib.request.urlopen",
            return_value=mock_response,
        ) as urlopen:
            vector = OpenAIEmbeddingProvider("test-key", dim=1024).embed("test text")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["dimensions"], 1024)
        self.assertEqual(len(vector), 1024)

    def test_embedder_passes_explicit_dimension_to_gemini_provider(self):
        with patch.dict(
            os.environ,
            {
                "NODE_ENV": "production",
                "SYNAPSE_EMBEDDING_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-key",
                "SYNAPSE_EMBEDDING_DIMENSION": "768",
            },
            clear=True,
        ):
            embedder = SynapseEmbedder()
        self.assertEqual(embedder.dimension, 768)


class TestDefaultProviderDimension(unittest.TestCase):
    def test_gemini_provider_defaults_to_3072_dimensions(self):
        defaults = GeminiEmbeddingProvider.__init__.__defaults__ or ()
        self.assertIn(3072, defaults)

    def test_helm_default_provider_is_gemini(self):
        values = (ROOT / "deploy/helm-chart/values.yaml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^\s+embeddingProvider:\s*"([^"]+)"', values)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "gemini")

    def test_helm_fails_render_on_deterministic_in_production(self):
        configmap = (ROOT / "deploy/helm-chart/templates/configmap.yaml").read_text(encoding="utf-8")
        self.assertIn("embeddingProvider=deterministic is not allowed", configmap)

    def test_compose_requires_an_explicit_provider(self):
        compose = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("SYNAPSE_EMBEDDING_PROVIDER=${SYNAPSE_EMBEDDING_PROVIDER:?", compose)
        self.assertNotIn("EMBEDDING_PROVIDER:-deterministic", compose)


if __name__ == "__main__":
    unittest.main()
