#!/usr/bin/env python3
"""Reproducible offline retrieval and context-packing baseline benchmark.

This harness is deliberately small and transparent. It compares lexical BM25,
dense cosine, and reciprocal-rank-fusion (hybrid) rankings on one fixed dataset,
then measures unbudgeted top-k, rank-order budget fitting, greedy ratio packing,
and dynamic-programming packing. Timings are local measurements, not SLAs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from cryptography.fernet import Fernet

_SCRIPT_DIR = Path(__file__).resolve().parent
_SDK_ROOT = _SCRIPT_DIR.parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from synapse_memory import __version__ as sdk_version
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.sqlite_db import SQLiteMemoryStore


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return a nearest-rank percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)
    return round(ordered[min(index, len(ordered) - 1)], 4)


def _latency_summary(values: Sequence[float]) -> Dict[str, Any]:
    """Summarize measured milliseconds while retaining sample count."""
    if not values:
        return {
            "samples": 0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "mean_ms": 0.0,
        }
    return {
        "samples": len(values),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "p99_ms": _percentile(values, 99),
        "mean_ms": round(statistics.fmean(values), 4),
    }


def _estimate_tokens(text: str) -> int:
    """Use the same lightweight word-count estimator as this benchmark family."""
    return max(15, int(len(text.split()) * 1.3) + 8)


def _validate_dataset(dataset: Dict[str, Any]) -> None:
    documents = dataset.get("documents")
    queries = dataset.get("queries")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Dataset must contain a non-empty 'documents' list.")
    if not isinstance(queries, list) or not queries:
        raise ValueError("Dataset must contain a non-empty 'queries' list.")

    ids = [str(item.get("id", "")) for item in documents]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("Every document must have a unique, non-empty id.")

    by_id = {str(item["id"]): item for item in documents}
    for query in queries:
        query_id = str(query.get("id", ""))
        tenant_id = str(query.get("tenant_id", ""))
        relevant = query.get("relevant_memory_ids")
        if not query_id or not tenant_id or not str(query.get("query", "")).strip():
            raise ValueError("Every query needs id, tenant_id, and non-empty query text.")
        if not isinstance(relevant, list) or not relevant:
            raise ValueError(
                "Query {0} needs at least one judged relevant document.".format(query_id)
            )
        missing = set(str(item) for item in relevant) - set(by_id)
        if missing:
            raise ValueError(
                "Query {0} references missing document ids: {1}".format(
                    query_id, sorted(missing)
                )
            )
        wrong_tenant = [
            memory_id
            for memory_id in relevant
            if by_id[str(memory_id)].get("tenant_id") != tenant_id
        ]
        if wrong_tenant:
            raise ValueError(
                "Query {0} has relevant documents outside its tenant: {1}".format(
                    query_id, wrong_tenant
                )
            )


@contextmanager
def _pinned_offline_environment(key_file: Path) -> Iterator[None]:
    """Pin deterministic 128-d embeddings and temporary encryption for this run."""
    names = (
        "SYNAPSE_EMBEDDING_PROVIDER",
        "SYNAPSE_EMBEDDING_DIMENSION",
        "SYNAPSE_EMBEDDING_MODEL",
        "SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "SYNAPSE_ENCRYPTION_KEY",
        "SYNAPSE_ENCRYPTION_KEYS",
        "SYNAPSE_ENCRYPTION_KEY_B64_FILE",
        "SYNAPSE_KEY_FILE",
        "NODE_ENV",
        "SYNAPSE_STORAGE_BACKEND",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        os.environ["SYNAPSE_EMBEDDING_PROVIDER"] = "deterministic"
        os.environ["SYNAPSE_EMBEDDING_DIMENSION"] = "128"
        os.environ["SYNAPSE_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
        os.environ["SYNAPSE_KEY_FILE"] = str(key_file)
        os.environ["SYNAPSE_STORAGE_BACKEND"] = "sqlite"
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _query_terms(text: str) -> List[str]:
    """Mirror HybridSearch's tokenizer for the BM25-only baseline."""
    return [
        word
        for word in re.findall(r"[a-zA-Z0-9_]+", text.lower())
        if len(word) > 1
    ]


def _rank_documents(
    method: str,
    query_text: str,
    query_vector: List[float],
    documents: List[Dict[str, Any]],
    searcher: HybridSearch,
) -> List[Dict[str, Any]]:
    """Rank all documents using one named method with deterministic tie-breaking."""
    if method == "lexical_bm25":
        scores = searcher._calculate_bm25_scores(_query_terms(query_text), documents)
        ranked = []
        for document in documents:
            item = document.copy()
            item["relevance_score"] = scores[str(item["id"])]
            ranked.append(item)
        ranked.sort(
            key=lambda item: (-float(item["relevance_score"]), str(item["id"]))
        )
        return ranked

    if method == "dense_cosine":
        scores = searcher._calculate_dense_similarities(query_vector, documents)
        ranked = []
        for document in documents:
            item = document.copy()
            item["relevance_score"] = scores[str(item["id"])]
            ranked.append(item)
        ranked.sort(
            key=lambda item: (-float(item["relevance_score"]), str(item["id"]))
        )
        return ranked

    if method == "hybrid_rrf":
        ranked = searcher.fused_search(
            query=query_text,
            documents=documents,
            query_vector=query_vector,
            top_k=len(documents),
        )
        ranked.sort(
            key=lambda item: (-float(item.get("relevance_score", 0.0)), str(item["id"]))
        )
        return ranked

    raise ValueError("Unknown ranking method: {0}".format(method))


def _relevance_metrics(
    ranked_ids: Sequence[str], relevant_ids: Set[str], k: int
) -> Dict[str, float]:
    top = list(ranked_ids[:k])
    hits = len(set(top) & relevant_ids)
    reciprocal_rank = 0.0
    for position, memory_id in enumerate(ranked_ids, start=1):
        if memory_id in relevant_ids:
            reciprocal_rank = 1.0 / position
            break
    return {
        "recall_at_k": round(hits / len(relevant_ids), 4),
        "precision_at_k": round(hits / k, 4),
        "reciprocal_rank": round(reciprocal_rank, 4),
    }


def _mean_query_metrics(items: Sequence[Dict[str, float]]) -> Dict[str, float]:
    if not items:
        return {}
    keys = ("recall_at_k", "precision_at_k", "reciprocal_rank")
    return {
        "mean_{0}".format(key): round(
            statistics.fmean(item[key] for item in items), 4
        )
        for key in keys
    }


def _rank_order_fit(
    ranked: Sequence[Dict[str, Any]], token_budget: int
) -> List[Dict[str, Any]]:
    """Take ranked items that fit, skipping items that exceed the remaining budget."""
    selected: List[Dict[str, Any]] = []
    used = 0
    for item in ranked:
        cost = max(1, int(item.get("token_cost", 1)))
        if used + cost <= token_budget:
            selected.append(item)
            used += cost
    return selected


def _pack(
    method: str,
    ranked: List[Dict[str, Any]],
    top_k: int,
    token_budget: int,
    packer: KnapsackPacker,
) -> List[Dict[str, Any]]:
    if method == "top_k_unbudgeted":
        return ranked[:top_k]
    candidates = ranked
    if method == "ranked_fit":
        return _rank_order_fit(candidates, token_budget)
    if method == "greedy_ratio":
        return packer.greedy_pack(candidates, token_budget=token_budget)
    if method == "knapsack_dp":
        return packer.pack(candidates, token_budget=token_budget)
    raise ValueError("Unknown packing method: {0}".format(method))


def _git_revision() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def run_benchmark(
    dataset_path: Path,
    repeats: int = 5,
    warmups: int = 1,
    top_k: int = 5,
    candidate_k: int = 10,
    token_budget: int = 90,
) -> Dict[str, Any]:
    if repeats < 1:
        raise ValueError("--repeats must be at least 1.")
    if warmups < 0:
        raise ValueError("--warmups cannot be negative.")
    if top_k < 1:
        raise ValueError("--top-k must be at least 1.")
    if candidate_k < top_k:
        raise ValueError("--candidate-k must be greater than or equal to --top-k.")
    if token_budget < 1:
        raise ValueError("--token-budget must be at least 1.")

    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes.decode("utf-8"))
    _validate_dataset(dataset)
    document_specs = dataset["documents"]
    query_specs = dataset["queries"]
    ranking_methods = ("lexical_bm25", "dense_cosine", "hybrid_rrf")
    packing_methods = (
        "top_k_unbudgeted",
        "ranked_fit",
        "greedy_ratio",
        "knapsack_dp",
    )

    with tempfile.TemporaryDirectory(prefix="synapse-retrieval-bench-") as temp_dir:
        temp_path = Path(temp_dir)
        with _pinned_offline_environment(temp_path / ".synapse_key"):
            embedder = SynapseEmbedder(provider="deterministic")
            searcher = HybridSearch()
            packer = KnapsackPacker(token_budget=token_budget)
            store = SQLiteMemoryStore(db_path=str(temp_path / "corpus.sqlite3"))
            write_embed_ms: List[float] = []
            write_insert_ms: List[float] = []
            write_total_ms: List[float] = []
            started_at = time.time()

            try:
                # Measure embedding and encrypted SQLite insertion separately.
                for index, spec in enumerate(document_specs):
                    embed_start = time.perf_counter()
                    vector = embedder.embed_query(str(spec["content"]))
                    embed_ms = (time.perf_counter() - embed_start) * 1000.0

                    record = {
                        "id": str(spec["id"]),
                        "tenant_id": str(spec["tenant_id"]),
                        "content": str(spec["content"]),
                        "category": str(spec.get("category", "fact")),
                        "confidence": 0.95,
                        "created_at": started_at + index / 1000.0,
                        "last_accessed_at": started_at + index / 1000.0,
                        "access_count": 0,
                        "token_cost": _estimate_tokens(str(spec["content"])),
                        "embedding": vector,
                        "embedding_dimension": len(vector),
                        "feedback_multiplier": 1.0,
                    }
                    insert_start = time.perf_counter()
                    store.insert_memory(record)
                    insert_ms = (time.perf_counter() - insert_start) * 1000.0
                    write_embed_ms.append(embed_ms)
                    write_insert_ms.append(insert_ms)
                    write_total_ms.append(embed_ms + insert_ms)

                corpus = store.get_all_memories()
                if len(corpus) != len(document_specs):
                    raise RuntimeError(
                        "Corpus row count mismatch: expected {0}, got {1}".format(
                            len(document_specs), len(corpus)
                        )
                    )
                tenants = sorted({str(doc["tenant_id"]) for doc in corpus})
                tenant_corpus = {
                    tenant: store.get_all_memories(tenant_id=tenant)
                    for tenant in tenants
                }
                query_contexts: List[Dict[str, Any]] = []
                query_embed_ms: List[float] = []
                for spec in query_specs:
                    query_text = str(spec["query"])
                    query_started = time.perf_counter()
                    query_vector = embedder.embed_query(query_text)
                    query_embed_ms.append(
                        (time.perf_counter() - query_started) * 1000.0
                    )
                    query_contexts.append(
                        {
                            "spec": spec,
                            "query_vector": query_vector,
                            "tenant_documents": tenant_corpus[str(spec["tenant_id"])],
                        }
                    )

                rankings: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
                    method: {} for method in ranking_methods
                }
                rank_latencies: Dict[str, List[float]] = {
                    method: [] for method in ranking_methods
                }

                for _ in range(warmups):
                    for context in query_contexts:
                        spec = context["spec"]
                        tenant_docs = context["tenant_documents"]
                        for method in ranking_methods:
                            _rank_documents(
                                method,
                                str(spec["query"]),
                                context["query_vector"],
                                tenant_docs,
                                searcher,
                            )

                for repetition in range(repeats):
                    for context in query_contexts:
                        spec = context["spec"]
                        query_id = str(spec["id"])
                        tenant_docs = context["tenant_documents"]
                        for method in ranking_methods:
                            query_start = time.perf_counter()
                            ranked = _rank_documents(
                                method,
                                str(spec["query"]),
                                context["query_vector"],
                                tenant_docs,
                                searcher,
                            )
                            rank_latencies[method].append(
                                (time.perf_counter() - query_start) * 1000.0
                            )
                            if repetition == 0:
                                rankings[method][query_id] = ranked

                ranking_summary: Dict[str, Any] = {}
                per_query: Dict[str, Any] = {}
                for method in ranking_methods:
                    metric_rows: List[Dict[str, float]] = []
                    for spec in query_specs:
                        query_id = str(spec["id"])
                        relevant = set(str(x) for x in spec["relevant_memory_ids"])
                        ranked_ids = [
                            str(item["id"]) for item in rankings[method][query_id]
                        ]
                        metrics = _relevance_metrics(ranked_ids, relevant, top_k)
                        metric_rows.append(metrics)
                        per_query.setdefault(query_id, {})
                        per_query[query_id].update(
                            {
                                "tenant_id": str(spec["tenant_id"]),
                                "query": str(spec["query"]),
                                "relevant_memory_ids": sorted(relevant),
                            }
                        )
                        per_query[query_id].setdefault("ranking", {})[method] = {
                            "top_k_ids": ranked_ids[:top_k],
                            "metrics": metrics,
                        }
                    ranking_summary[method] = {
                        **_mean_query_metrics(metric_rows),
                        "latency_ms": _latency_summary(rank_latencies[method]),
                        "latency_samples_ms": [
                            round(value, 4) for value in rank_latencies[method]
                        ],
                    }

                # Compare packed context sets using the same hybrid-ranked candidate list.
                packing_latencies: Dict[str, List[float]] = {
                    method: [] for method in packing_methods
                }
                packed_results: Dict[str, Dict[str, Dict[str, Any]]] = {
                    method: {} for method in packing_methods
                }
                for _ in range(warmups):
                    for context in query_contexts:
                        spec = context["spec"]
                        query_id = str(spec["id"])
                        hybrid = rankings["hybrid_rrf"][query_id][:candidate_k]
                        for method in packing_methods:
                            _pack(method, hybrid, top_k, token_budget, packer)

                for repetition in range(repeats):
                    for context in query_contexts:
                        spec = context["spec"]
                        query_id = str(spec["id"])
                        relevant = set(str(x) for x in spec["relevant_memory_ids"])
                        candidates = rankings["hybrid_rrf"][query_id][:candidate_k]
                        for method in packing_methods:
                            pack_start = time.perf_counter()
                            selected = _pack(
                                method, candidates, top_k, token_budget, packer
                            )
                            packing_latencies[method].append(
                                (time.perf_counter() - pack_start) * 1000.0
                            )
                            if repetition == 0:
                                selected_ids = [str(item["id"]) for item in selected]
                                token_cost = sum(
                                    max(1, int(item.get("token_cost", 1)))
                                    for item in selected
                                )
                                metrics = _relevance_metrics(
                                    selected_ids, relevant, top_k
                                )
                                packed_results[method][query_id] = {
                                    "selected_ids": selected_ids,
                                    "tokens_used": token_cost,
                                    "budget_utilization_pct": round(
                                        100.0 * token_cost / token_budget, 2
                                    ),
                                    "within_budget": token_cost <= token_budget,
                                    "relevant_recall": round(
                                        len(set(selected_ids) & relevant) / len(relevant),
                                        4,
                                    ),
                                    "reciprocal_rank": metrics["reciprocal_rank"],
                                }

                packing_summary: Dict[str, Any] = {}
                for method in packing_methods:
                    rows = list(packed_results[method].values())
                    packing_summary[method] = {
                        "mean_relevant_recall": round(
                            statistics.fmean(row["relevant_recall"] for row in rows),
                            4,
                        ),
                        "mean_tokens_used": round(
                            statistics.fmean(row["tokens_used"] for row in rows), 2
                        ),
                        "mean_budget_utilization_pct": round(
                            statistics.fmean(
                                row["budget_utilization_pct"] for row in rows
                            ),
                            2,
                        ),
                        "over_budget_query_count": sum(
                            1 for row in rows if not row["within_budget"]
                        ),
                        "latency_ms": _latency_summary(packing_latencies[method]),
                        "latency_samples_ms": [
                            round(value, 4) for value in packing_latencies[method]
                        ],
                        "per_query": packed_results[method],
                    }
                    for query_id, values in packed_results[method].items():
                        per_query[query_id].setdefault("packing", {})[method] = values

                # Probe the real SQLite tenant-filtered APIs, not just the in-memory
                # filtered ranking view. The dataset includes multiple tenant IDs.
                isolation_probes = 0
                wrong_tenant_rows = 0
                for context in query_contexts:
                    spec = context["spec"]
                    tenant_id = str(spec["tenant_id"])
                    query_text = str(spec["query"])
                    query_vector = context["query_vector"]
                    probes = (
                        store.get_all_memories(tenant_id=tenant_id),
                        store.vector_search(
                            query_vector, top_k=candidate_k, tenant_id=tenant_id
                        ),
                        store.lexical_search(
                            query_text, top_k=candidate_k, tenant_id=tenant_id
                        ),
                    )
                    for rows in probes:
                        isolation_probes += 1
                        wrong_tenant_rows += sum(
                            1
                            for row in rows
                            if str(row.get("tenant_id")) != tenant_id
                        )

                # Measure a real encrypted store write/delete round-trip.
                sentinel_id = "benchmark_delete_sentinel"
                sentinel_content = (
                    "Synthetic benchmark deletion sentinel; it is not evaluation data."
                )
                sentinel_vector = embedder.embed_query(sentinel_content)
                delete_record = {
                    "id": sentinel_id,
                    "tenant_id": tenants[0],
                    "content": sentinel_content,
                    "category": "benchmark",
                    "confidence": 1.0,
                    "created_at": time.time(),
                    "last_accessed_at": time.time(),
                    "access_count": 0,
                    "token_cost": _estimate_tokens(sentinel_content),
                    "embedding": sentinel_vector,
                    "embedding_dimension": len(sentinel_vector),
                    "feedback_multiplier": 1.0,
                }
                store.insert_memory(delete_record)
                delete_start = time.perf_counter()
                store.delete_memory(sentinel_id, tenant_id=tenants[0])
                delete_ms = (time.perf_counter() - delete_start) * 1000.0
                deleted = store.get_memory_by_id(sentinel_id, tenant_id=tenants[0]) is None
                if store.count_memories(tenant_id=tenants[0]) != sum(
                    1 for item in document_specs if item["tenant_id"] == tenants[0]
                ):
                    deleted = False

                result = {
                    "schema_version": 1,
                    "benchmark": "synapse_retrieval_baselines",
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source_revision": _git_revision(),
                    "dataset": {
                        "name": dataset.get("name", dataset_path.stem),
                        "path": str(dataset_path),
                        "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                        "document_count": len(document_specs),
                        "query_count": len(query_specs),
                        "tenant_count": len(tenants),
                        "queries": [
                            {
                                "id": str(item["id"]),
                                "tenant_id": str(item["tenant_id"]),
                                "relevant_memory_ids": sorted(
                                    str(x) for x in item["relevant_memory_ids"]
                                ),
                            }
                            for item in query_specs
                        ],
                    },
                    "environment": {
                        "python": sys.version.split()[0],
                        "platform": platform.platform(),
                        "synapse_memory_engine": sdk_version,
                        "embedding_provider": embedder.provider_name,
                        "embedding_dimension": embedder.dimension,
                        "embedding_is_semantic": embedder.is_semantic,
                        "storage": "SQLite encrypted store; corpus preloaded before rank-only timings",
                    },
                    "configuration": {
                        "repeats": repeats,
                        "warmups": warmups,
                        "top_k": top_k,
                        "candidate_k": candidate_k,
                        "token_budget": token_budget,
                        "token_estimator": "max(15, int(word_count * 1.3) + 8)",
                        "tie_breaking": "descending score, then ascending document id",
                    },
                    "methodology": {
                        "ranking_methods": {
                            "lexical_bm25": "BM25 lexical scores only; zero-score documents remain in the ranked list.",
                            "dense_cosine": "Exact cosine ranking using deterministic 128-d hash vectors.",
                            "hybrid_rrf": "Synapse HybridSearch reciprocal-rank fusion on the same preloaded tenant corpus.",
                        },
                        "packing_methods": {
                            "top_k_unbudgeted": "First top_k hybrid results, without enforcing the token budget.",
                            "ranked_fit": "Greedy rank-order fit; skip any item that does not fit in remaining budget.",
                            "greedy_ratio": "KnapsackPacker greedy relevance-per-token fallback strategy.",
                            "knapsack_dp": "KnapsackPacker dynamic-programming strategy.",
                        },
                        "latency_scope": "Ranker latency excludes query embedding, disk reads, process startup, and network latency; query embedding, write/embed, SQLite insertion, and packing timings are reported separately.",
                        "write_scope": "Per document: deterministic embedding plus encrypted SQLite insert; deduplication is not included.",
                    },
                    "write_cost_ms": {
                        "embedding": _latency_summary(write_embed_ms),
                        "encrypted_sqlite_insert": _latency_summary(write_insert_ms),
                        "embedding_plus_insert": _latency_summary(write_total_ms),
                        "samples": [
                            {
                                "memory_id": str(document_specs[index]["id"]),
                                "embedding_ms": round(write_embed_ms[index], 4),
                                "insert_ms": round(write_insert_ms[index], 4),
                                "total_ms": round(write_total_ms[index], 4),
                            }
                            for index in range(len(document_specs))
                        ],
                    },
                    "query_embedding_latency_ms": {
                        "summary": _latency_summary(query_embed_ms),
                        "samples": [
                            {
                                "query_id": str(query_specs[index]["id"]),
                                "latency_ms": round(query_embed_ms[index], 4),
                            }
                            for index in range(len(query_specs))
                        ],
                    },
                    "ranking": ranking_summary,
                    "context_packing": packing_summary,
                    "tenant_isolation": {
                        "status": "passed" if wrong_tenant_rows == 0 else "failed",
                        "probes": isolation_probes,
                        "wrong_tenant_row_count": wrong_tenant_rows,
                        "scope": "SQLite get_all_memories, vector_search, and lexical_search tenant filters.",
                    },
                    "deletion": {
                        "status": "passed" if deleted else "failed",
                        "latency_ms": round(delete_ms, 4),
                        "sentinel_removed_from_tenant_store": deleted,
                    },
                    "per_query": per_query,
                    "limitations": [
                        "The fixture is small and synthetic; it does not estimate production workload quality.",
                        "The deterministic hash embedder is not semantic. Relevance scores do not demonstrate natural-language semantic understanding.",
                        "SQLite ranking uses an exact in-memory scan after one corpus load; results do not characterize pgvector HNSW or network/database latency.",
                        "The relevance labels are hand-authored judgments for this fixed dataset, not user-derived labels.",
                        "Latency values depend on the machine, Python build, filesystem, and process load. Compare only runs with recorded configuration and environment.",
                        "This benchmark compares retrieval and context-packing strategies; it does not benchmark LLM answer quality, contradiction resolution, or multi-turn memory extraction.",
                    ],
                }
            finally:
                store.close()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_SCRIPT_DIR / "retrieval_dataset.json",
        help="Path to the fixed JSON benchmark corpus.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("retrieval-baseline-results.json"),
        help="Output JSON file for complete measured results.",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=10)
    parser.add_argument("--token-budget", type=int, default=90)
    args = parser.parse_args()

    try:
        result = run_benchmark(
            dataset_path=args.dataset.resolve(),
            repeats=args.repeats,
            warmups=args.warmups,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            token_budget=args.token_budget,
        )
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print("Benchmark failed: {0}".format(exc), file=sys.stderr)
        return 2

    print("Measured retrieval baseline saved to {0}".format(output_path))
    print(
        "Dataset: {0} documents, {1} queries, {2} tenants; "
        "embedder={3} ({4}-d, semantic={5})".format(
            result["dataset"]["document_count"],
            result["dataset"]["query_count"],
            result["dataset"]["tenant_count"],
            result["environment"]["embedding_provider"],
            result["environment"]["embedding_dimension"],
            result["environment"]["embedding_is_semantic"],
        )
    )
    print("\nRanking metrics (mean per-query):")
    for method, values in result["ranking"].items():
        print(
            "  {0:<14} recall@k={1:.3f} precision@k={2:.3f} "
            "MRR={3:.3f} p50={4:.3f}ms p95={5:.3f}ms".format(
                method,
                values["mean_recall_at_k"],
                values["mean_precision_at_k"],
                values["mean_reciprocal_rank"],
                values["latency_ms"]["p50_ms"],
                values["latency_ms"]["p95_ms"],
            )
        )
    print("\nContext packing:")
    for method, values in result["context_packing"].items():
        print(
            "  {0:<18} relevant_recall={1:.3f} mean_tokens={2:.1f} "
            "over_budget={3}".format(
                method,
                values["mean_relevant_recall"],
                values["mean_tokens_used"],
                values["over_budget_query_count"],
            )
        )
    print(
        "\nTenant isolation: {0} ({1} cross-tenant rows); deletion: {2}".format(
            result["tenant_isolation"]["status"],
            result["tenant_isolation"]["wrong_tenant_row_count"],
            result["deletion"]["status"],
        )
    )
    if (
        result["tenant_isolation"]["status"] != "passed"
        or result["deletion"]["status"] != "passed"
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
