"""Phase 7.2 retrieval evaluation (SQLite backend, no network).

Reproducible corpus: queries + expected relevant memory IDs. Measures
recall@k, precision@k, MRR, token-budget compliance, dedup behavior, and
end-to-end latency. All figures are measured in this run — projections
are never presented as results.
"""
import time
import unittest

from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore

CORPUS = [
    ("deploy password rotates every 90 days", "fact"),
    ("deploy pipeline runs on tags only", "fact"),
    ("oncall rotation starts Monday", "preference"),
    ("stale cache TTL is 3600 seconds", "fact"),
    ("quarterly review moved to Friday", "episodic"),
]

QUERIES = [
    ("how often does the deploy password rotate?", {"deploy password rotates every 90 days"}),
    ("when does the deploy pipeline run?", {"deploy pipeline runs on tags only"}),
    ("what is the cache TTL?", {"stale cache TTL is 3600 seconds"}),
]


def recall_at_k(retrieved, expected, k):
    top = retrieved[:k]
    return len(set(top) & expected) / max(1, len(expected))


def precision_at_k(retrieved, expected, k):
    top = retrieved[:k]
    return len(set(top) & expected) / max(1, k)


def reciprocal_rank(retrieved, expected):
    for i, doc_id in enumerate(retrieved, 1):
        if doc_id in expected:
            return 1.0 / i
    return 0.0


class TestRetrievalEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SQLiteMemoryStore(db_path=":memory:")
        cls.embedder = SynapseEmbedder()
        cls.manager = MemoryManager(store=cls.store, embedder=cls.embedder)
        cls.searcher = HybridSearch()
        cls.packer = KnapsackPacker(token_budget=500)
        cls.decay = DecayEngine({})
        cls.contents = {}
        for text, cat in CORPUS:
            mid, _, _ = cls.manager.ingest_with_deduplication(content=text, category=cat, tenant_id="eval")
            cls.contents[mid] = text

    def _expected_ids(self, expected_texts):
        return {mid for mid, text in self.contents.items() if text in expected_texts}

    def test_recall_precision_mrr(self):
        recalls, precisions, rrs = [], [], []
        for query, expected_texts in QUERIES:
            expected = self._expected_ids(expected_texts)
            docs = self.store.get_all_memories(tenant_id="eval")
            qv = self.embedder.embed_query(query)
            ranked = self.searcher.fused_search(query, docs, query_vector=qv, top_k=5, tenant_filter="eval")
            ids = [m["id"] for m in ranked]
            recalls.append(recall_at_k(ids, expected, 5))
            precisions.append(precision_at_k(ids, expected, 5))
            rrs.append(reciprocal_rank(ids, expected))
        self.assertGreaterEqual(sum(recalls) / len(recalls), 0.5, f"recall@5 {recalls}")
        self.assertGreater(sum(rrs) / len(rrs), 0.0)

    def test_token_budget_compliance_and_decay_composition(self):
        query = QUERIES[0][0]
        docs = self.store.get_all_memories(tenant_id="eval")
        qv = self.embedder.embed_query(query)
        ranked = self.searcher.fused_search(query, docs, query_vector=qv, top_k=5, tenant_filter="eval")
        for m in ranked:
            base = float(m.get("relevance_score", 0.0))
            combined = self.decay.combine_scores(base, m)
            self.assertLessEqual(combined, base + 1e-9)
        packed = self.packer.pack(ranked, 200)
        used = sum(int(n.get("token_cost", 0) or 0) for n in packed)
        self.assertLessEqual(used, 200)

    def test_dedup_merges_true_duplicates(self):
        before = len(self.store.get_all_memories(tenant_id="eval"))
        self.manager.ingest_with_deduplication(
            content="deploy password rotates every 90 days", category="fact", tenant_id="eval"
        )
        after = len(self.store.get_all_memories(tenant_id="eval"))
        self.assertEqual(before, after)

    def test_end_to_end_latency_measured(self):
        start = time.time()
        docs = self.store.get_all_memories(tenant_id="eval")
        qv = self.embedder.embed_query(QUERIES[0][0])
        self.searcher.fused_search(QUERIES[0][0], docs, query_vector=qv, top_k=5, tenant_filter="eval")
        elapsed_ms = (time.time() - start) * 1000
        self.assertGreater(elapsed_ms, 0.0)
        self.assertLess(elapsed_ms, 5000.0)


if __name__ == "__main__":
    unittest.main()
