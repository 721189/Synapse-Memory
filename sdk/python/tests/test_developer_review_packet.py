import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "benchmark" / "create_blind_developer_review_packet.py"
SPEC = importlib.util.spec_from_file_location("create_blind_developer_review_packet", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class DeveloperReviewPacketTests(unittest.TestCase):
    def test_packet_is_blinded_and_labels_are_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "dataset.json"
            result = root / "result.json"
            dataset.write_text(json.dumps({
                "documents": [
                    {"id": "secret-a", "tenant_id": "t", "content": "Release requires a signed tag."},
                    {"id": "secret-b", "tenant_id": "t", "content": "Backups need the original encryption key."},
                ],
                "queries": [{
                    "id": "q1", "query": "How do I publish?", "tenant_id": "t",
                    "relevant_memory_ids": ["secret-a"],
                }],
            }))
            result.write_text(json.dumps({"per_query": {
                "q1": {"ranking": {"hybrid_rrf": {"top_k_ids": ["secret-a", "secret-b"]}}}
            }}))
            packet, mapping = MODULE.build_packet(dataset, [result], max_queries=1)
            packet_again, mapping_again = MODULE.build_packet(dataset, [result], max_queries=1)
            self.assertEqual(packet, packet_again)
            self.assertEqual(mapping, mapping_again)
            self.assertEqual(len(packet), 1)
            serialized = json.dumps(packet)
            self.assertNotIn("hybrid_rrf", serialized)
            self.assertNotIn("secret-a", serialized)
            self.assertNotIn("relevant_memory_ids", serialized)
            self.assertEqual(
                {candidate["reviewer_a"]["relevance"] for candidate in packet[0]["candidates"]},
                {None},
            )
            self.assertEqual(
                {candidate["reviewer_b"]["relevance"] for candidate in packet[0]["candidates"]},
                {None},
            )
            self.assertEqual(len(mapping), 2)
            self.assertIn("secret-a", json.dumps(mapping))


if __name__ == "__main__":
    unittest.main()
