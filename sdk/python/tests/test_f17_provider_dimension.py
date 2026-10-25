"""P0: the shipped production default provider is 3072-d, not the test vectors.

The engine refuses a dimension mismatch at write time, so defaults must agree:
Helm and Compose must name a semantic provider (gemini = 3072-d) and never the
deterministic 128-d provider, which production refuses outright.
"""
import re
import unittest
from pathlib import Path

try:
    from synapse_memory.core.embedding_provider import GeminiEmbeddingProvider
except ImportError:  # pragma: no cover
    GeminiEmbeddingProvider = None

ROOT = Path(__file__).resolve().parents[3]


class TestDefaultProviderDimension(unittest.TestCase):
    def test_gemini_provider_defaults_to_3072_dimensions(self):
        if GeminiEmbeddingProvider is None:
            self.skipTest("Gemini provider not importable")
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
