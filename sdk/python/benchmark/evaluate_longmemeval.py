#!/usr/bin/env python3
"""Evaluate retrieval and context packing on third-party reviewed LongMemEval-S labels.

The source dataset is downloaded separately by CI and is never committed or
uploaded as a workflow artifact. Metrics/results retain IDs and scores, not the
raw conversation text.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_SDK_ROOT = _SCRIPT_DIR.parent
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from synapse_memory import __version__ as sdk_version
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker
from retrieval_baselines import (
    _SentenceTransformerBenchmarkEmbedder,
    _estimate_tokens,
    _latency_summary,
    _pack,
    _rank_documents,
)

SOURCE_DATASET = (
    "agentscope-ai/ReMe_longmemeval_clean_s_v2/"
    "longmemeval_s_reme_cleaned.json"
)
SOURCE_URL = (
    "https://huggingface.co/datasets/agentscope-ai/"
    "ReMe_longmemeval_clean_s_v2/resolve/"
    "f8bd08fd19ef175c99c7065c322191e16c69f0ea/"
    "longmemeval_s_reme_cleaned.json"
)
# The dataset card reports f8b308..., but the file downloaded from the exact
# Hugging Face snapshot below has SHA-256 57ed322... . Pin the observed snapshot
# and actual bytes; the evaluator then validates all evidence IDs and timestamps.
DATASET_REVISION = "f8bd08fd19ef175c99c7065c322191e16c69f0ea"
DATASET_CARD_REPORTED_SHA256 = (
    "f8b308cc2d184d029dd2226e0c0a88eeac298e6318706d588e015beb1b83fc7d"
)
EXPECTED_DATASET_SHA256 = (
    "57ed3225673015880c42eaf764406e656d9ac3fc4821337e3ec3c28ac8259823"
)
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _distribution_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _parse_timestamp(value: Any) -> datetime:
    """Parse LongMemEval timestamps and fail closed for unexpected formats."""
    text = re.sub(r"\s+\([^)]+\)", "", str(value)).strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("Unsupported LongMemEval timestamp format: {0!r}".format(value))


def _render_session(session: Any, session_date: str) -> str:
    """Render conversation turns without using hidden answer labels."""
    lines: List[str] = ["Session date: {0}".format(session_date)]
    if not isinstance(session, list):
        raise ValueError("Every haystack session must be a list of turns.")
    for turn in session:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "unknown")).strip() or "unknown"
        text = str(turn.get("content", "")).strip()
        if text:
            lines.append("{0}: {1}".format(role, text))
    return "\n".join(lines).strip()


def _ndcg_at_k(ranked_ids: Sequence[str], relevant_ids: Set[str], k: int) -> float:
    def dcg(ids: Sequence[str]) -> float:
        return sum(
            1.0 / math.log2(position + 2.0)
            for position, doc_id in enumerate(ids[:k])
            if doc_id in relevant_ids
        )

    ideal_count = min(k, len(relevant_ids))
    ideal = sum(1.0 / math.log2(position + 2.0) for position in range(ideal_count))
    return round(dcg(ranked_ids) / ideal, 4) if ideal else 0.0


def _metrics(ranked_ids: Sequence[str], relevant_ids: Set[str]) -> Dict[str, float]:
    found5 = set(ranked_ids[:5]) & relevant_ids
    found10 = set(ranked_ids[:10]) & relevant_ids
    reciprocal_rank = 0.0
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "recall_at_5": round(len(found5) / len(relevant_ids), 4),
        "recall_at_10": round(len(found10) / len(relevant_ids), 4),
        "precision_at_5": round(len(found5) / 5.0, 4),
        "mrr": round(reciprocal_rank, 4),
        "ndcg_at_10": _ndcg_at_k(ranked_ids, relevant_ids, 10),
    }


def _average_metrics(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    if not rows:
        return {}
    keys = ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")
    return {
        "mean_{0}".format(key): round(
            statistics.fmean(row[key] for row in rows), 4
        )
        for key in keys
    }


def _encode_many(embedder: Any, texts: Sequence[str]) -> List[List[float]]:
    if isinstance(embedder, _SentenceTransformerBenchmarkEmbedder):
        values = embedder._model.encode(
            list(texts),
            batch_size=64,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
        return values.tolist()
    return [embedder.embed_query(text) for text in texts]


def _validate_source_records(
    records: Any, expected_count: Optional[int] = 500
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not isinstance(records, list) or (
        expected_count is not None and len(records) != expected_count
    ):
        raise ValueError(
            "Expected {0} LongMemEval records; received {1}.".format(
                expected_count if expected_count is not None else "a list of",
                len(records) if isinstance(records, list) else type(records).__name__,
            )
        )

    ids = [str(item.get("question_id", "")) for item in records]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("LongMemEval question_id values must be unique and non-empty.")

    prepared: List[Dict[str, Any]] = []
    type_counts: Counter = Counter()
    no_evidence_count = 0
    future_sessions_excluded = 0
    evidence_session_count = 0
    candidate_session_count = 0

    for row in records:
        question_id = str(row["question_id"])
        question = str(row.get("question", "")).strip()
        if not question:
            raise ValueError("Question {0} has empty question text.".format(question_id))

        session_ids = [str(x) for x in row.get("haystack_session_ids", [])]
        session_dates = row.get("haystack_dates", [])
        sessions = row.get("haystack_sessions", [])
        if not (len(session_ids) == len(session_dates) == len(sessions)):
            raise ValueError(
                "Question {0}: session IDs, dates, and bodies have different lengths.".format(
                    question_id
                )
            )
        if len(session_ids) != len(set(session_ids)):
            raise ValueError(
                "Question {0} contains duplicate session IDs.".format(question_id)
            )

        query_time = _parse_timestamp(row["question_date"])
        documents: List[Dict[str, Any]] = []
        for session_id, raw_date, session in zip(session_ids, session_dates, sessions):
            session_time = _parse_timestamp(raw_date)
            if session_time > query_time:
                future_sessions_excluded += 1
                continue
            text = _render_session(session, str(raw_date))
            if not text:
                continue
            documents.append(
                {
                    "id": session_id,
                    "tenant_id": question_id,
                    "content": text,
                    "token_cost": _estimate_tokens(text),
                    "category": str(row.get("question_type", "unknown")),
                    "created_at": session_time.timestamp(),
                }
            )

        relevant_ids = set(str(x) for x in row.get("answer_session_ids", []))
        available_ids = {str(doc["id"]) for doc in documents}
        missing = relevant_ids - available_ids
        if missing:
            raise ValueError(
                "Reviewed evidence for question {0} is missing or occurs after the "
                "question timestamp: {1}".format(question_id, sorted(missing))
            )

        question_type = str(row.get("question_type", "unknown"))
        type_counts[question_type] += 1
        candidate_session_count += len(documents)
        evidence_session_count += len(relevant_ids)
        if not relevant_ids:
            no_evidence_count += 1

        prepared.append(
            {
                "id": question_id,
                "query": question,
                "question_type": question_type,
                "question_date": str(row["question_date"]),
                "documents": documents,
                "relevant_ids": relevant_ids,
            }
        )

    positive = sum(1 for item in prepared if item["relevant_ids"])
    metadata = {
        "question_count": len(prepared),
        "positive_evidence_question_count": positive,
        "no_evidence_question_count": no_evidence_count,
        "question_type_counts": dict(sorted(type_counts.items())),
        "candidate_session_instance_count": candidate_session_count,
        "mean_candidate_sessions_per_question": round(
            statistics.fmean(len(item["documents"]) for item in prepared), 2
        ),
        "reviewed_evidence_session_reference_count": evidence_session_count,
        "future_sessions_excluded_before_query_time": future_sessions_excluded,
        "label_validation": (
            "passed: every reviewed evidence session is present in the question's "
            "pre-question candidate history"
        ),
    }
    return prepared, metadata


def evaluate(
    dataset_path: Path,
    embedding_provider: str,
    expected_sha256: str = EXPECTED_DATASET_SHA256,
    top_k: int = 5,
    candidate_k: int = 10,
    token_budget: int = 512,
    model_name: str = MODEL_NAME,
    model_revision: str = MODEL_REVISION,
) -> Dict[str, Any]:
    if top_k < 1 or candidate_k < top_k or token_budget < 1:
        raise ValueError("Require top-k >= 1, candidate-k >= top-k, and token-budget >= 1.")
    if embedding_provider not in ("deterministic", "sentence-transformers"):
        raise ValueError("Unsupported embedding provider: {0}".format(embedding_provider))

    dataset_sha = _sha256_file(dataset_path)
    if expected_sha256 and dataset_sha != expected_sha256:
        raise ValueError(
            "Dataset checksum mismatch: expected {0}, got {1}. "
            "Review the source version before updating the pin.".format(
                expected_sha256, dataset_sha
            )
        )

    with dataset_path.open("r", encoding="utf-8") as handle:
        records = json.load(handle)
    prepared, dataset_stats = _validate_source_records(records)

    if embedding_provider == "sentence-transformers":
        embedder = _SentenceTransformerBenchmarkEmbedder(
            model_name=model_name, model_revision=model_revision
        )
    else:
        embedder = SynapseEmbedder(provider="deterministic")

    # Cache vectors by exact rendered session text; the same public history
    # session appears in multiple LongMemEval questions but is the same memory.
    content_by_digest: Dict[str, str] = {}
    for query in prepared:
        if not query["relevant_ids"]:
            continue
        for doc in query["documents"]:
            digest = hashlib.sha256(str(doc["content"]).encode("utf-8")).hexdigest()
            doc["content_sha256"] = digest
            content_by_digest.setdefault(digest, str(doc["content"]))

    content_hashes = list(content_by_digest)
    content_texts = [content_by_digest[key] for key in content_hashes]
    embedding_started = time.perf_counter()
    vectors = _encode_many(embedder, content_texts)
    vector_cache = {
        digest: vector for digest, vector in zip(content_hashes, vectors)
    }
    document_embedding_ms = (time.perf_counter() - embedding_started) * 1000.0

    positive_queries = [item for item in prepared if item["relevant_ids"]]
    query_texts = [str(item["query"]) for item in positive_queries]
    query_embedding_started = time.perf_counter()
    query_vectors = _encode_many(embedder, query_texts)
    query_embedding_ms = (time.perf_counter() - query_embedding_started) * 1000.0
    for item, vector in zip(positive_queries, query_vectors):
        item["query_vector"] = vector
        for doc in item["documents"]:
            doc["embedding"] = vector_cache[str(doc["content_sha256"])]

    ranking_methods = ("lexical_bm25", "dense_cosine", "hybrid_rrf")
    packing_methods = (
        "top_k_fit",
        "ranked_fit",
        "greedy_ratio",
        "knapsack_dp",
        "rank_discount_dp",
    )
    searcher = HybridSearch()
    packer = KnapsackPacker(token_budget=token_budget)
    ranking_latency: Dict[str, List[float]] = {m: [] for m in ranking_methods}
    packing_latency: Dict[str, List[float]] = {m: [] for m in packing_methods}
    rank_rows: Dict[str, List[Dict[str, float]]] = {m: [] for m in ranking_methods}
    type_rank_rows: Dict[str, Dict[str, List[Dict[str, float]]]] = {
        m: defaultdict(list) for m in ranking_methods
    }
    packing_rows: Dict[str, List[Dict[str, Any]]] = {m: [] for m in packing_methods}
    type_packing_rows: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        m: defaultdict(list) for m in packing_methods
    }
    per_query: Dict[str, Any] = {}

    for query in positive_queries:
        relevant = set(query["relevant_ids"])
        docs = query["documents"]
        query_vector = query["query_vector"]
        ranks: Dict[str, List[Dict[str, Any]]] = {}
        query_ranking: Dict[str, Any] = {}

        for method in ranking_methods:
            started = time.perf_counter()
            ranked = _rank_documents(
                method, str(query["query"]), query_vector, docs, searcher
            )
            ranking_latency[method].append((time.perf_counter() - started) * 1000.0)
            ranks[method] = ranked
            ranked_ids = [str(item["id"]) for item in ranked]
            measured = _metrics(ranked_ids, relevant)
            rank_rows[method].append(measured)
            type_rank_rows[method][query["question_type"]].append(measured)
            query_ranking[method] = {
                "top_5_session_ids": ranked_ids[:5],
                "top_10_session_ids": ranked_ids[:10],
                "metrics": measured,
            }

        hybrid_candidates = ranks["hybrid_rrf"][:candidate_k]
        candidates_by_method: Dict[str, List[Dict[str, Any]]] = {
            "top_k_fit": ranks["hybrid_rrf"][:top_k],
            "ranked_fit": hybrid_candidates,
            "greedy_ratio": hybrid_candidates,
            "knapsack_dp": hybrid_candidates,
            "rank_discount_dp": hybrid_candidates,
        }
        query_packing: Dict[str, Any] = {}
        for method in packing_methods:
            started = time.perf_counter()
            if method == "top_k_fit":
                selected = _pack(
                    "ranked_fit", candidates_by_method[method], top_k,
                    token_budget, packer
                )
            else:
                selected = _pack(
                    method, candidates_by_method[method], top_k,
                    token_budget, packer
                )
            packing_latency[method].append((time.perf_counter() - started) * 1000.0)
            selected_ids = [str(item["id"]) for item in selected]
            candidate_relevant = {str(x["id"]) for x in hybrid_candidates} & relevant
            selected_relevant = set(selected_ids) & relevant
            selected_token_cost = sum(max(1, int(x.get("token_cost", 1))) for x in selected)
            candidate_retention = (
                len(selected_relevant & candidate_relevant) / len(candidate_relevant)
                if candidate_relevant else None
            )
            row = {
                "evidence_recall_at_budget": round(
                    len(selected_relevant) / len(relevant), 4
                ),
                "candidate_recall": round(
                    len(candidate_relevant) / len(relevant), 4
                ),
                "retained_candidate_evidence_recall": (
                    round(candidate_retention, 4)
                    if candidate_retention is not None else None
                ),
                "tokens_used": selected_token_cost,
                "budget_utilization_pct": round(
                    selected_token_cost * 100.0 / token_budget, 2
                ),
                "within_budget": selected_token_cost <= token_budget,
                "selected_session_ids": selected_ids,
            }
            packing_rows[method].append(row)
            type_packing_rows[method][query["question_type"]].append(row)
            query_packing[method] = row

        per_query[query["id"]] = {
            "question_type": query["question_type"],
            "question_date": query["question_date"],
            "relevant_session_ids": sorted(relevant),
            "rankings": query_ranking,
            "packing": query_packing,
        }

    ranking_summary: Dict[str, Any] = {}
    for method in ranking_methods:
        ranking_summary[method] = {
            **_average_metrics(rank_rows[method]),
            "latency_ms": _latency_summary(ranking_latency[method]),
            "per_question_type": {
                kind: {
                    "question_count": len(rows),
                    **_average_metrics(rows),
                }
                for kind, rows in sorted(type_rank_rows[method].items())
            },
        }

    packing_summary: Dict[str, Any] = {}
    for method in packing_methods:
        rows = packing_rows[method]
        retained_values = [
            row["retained_candidate_evidence_recall"]
            for row in rows
            if row["retained_candidate_evidence_recall"] is not None
        ]
        packing_summary[method] = {
            "mean_evidence_recall_at_budget": round(
                statistics.fmean(row["evidence_recall_at_budget"] for row in rows), 4
            ),
            "mean_candidate_recall": round(
                statistics.fmean(row["candidate_recall"] for row in rows), 4
            ),
            "mean_retained_candidate_evidence_recall": (
                round(statistics.fmean(retained_values), 4) if retained_values else None
            ),
            "queries_with_relevant_candidate": len(retained_values),
            "queries_without_relevant_candidate": len(rows) - len(retained_values),
            "mean_tokens_used": round(
                statistics.fmean(row["tokens_used"] for row in rows), 2
            ),
            "mean_budget_utilization_pct": round(
                statistics.fmean(row["budget_utilization_pct"] for row in rows), 2
            ),
            "over_budget_query_count": sum(1 for row in rows if not row["within_budget"]),
            "latency_ms": _latency_summary(packing_latency[method]),
            "per_question_type": {
                kind: {
                    "question_count": len(type_rows),
                    "mean_evidence_recall_at_budget": round(
                        statistics.fmean(r["evidence_recall_at_budget"] for r in type_rows),
                        4,
                    ),
                    "mean_candidate_recall": round(
                        statistics.fmean(r["candidate_recall"] for r in type_rows), 4
                    ),
                    "mean_retained_candidate_evidence_recall": round(
                        statistics.fmean(
                            [
                                r["retained_candidate_evidence_recall"]
                                for r in type_rows
                                if r["retained_candidate_evidence_recall"] is not None
                            ]
                        ),
                        4,
                    ) if any(r["retained_candidate_evidence_recall"] is not None for r in type_rows) else None,
                }
                for kind, type_rows in sorted(type_packing_rows[method].items())
            },
            "per_query": {
                query_id: per_query[query_id]["packing"][method]
                for query_id in per_query
            },
        }

    return {
        "schema_version": 1,
        "benchmark": "longmemeval_reme_reviewed_session_retrieval_and_packing",
        "generated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source": {
            "dataset": SOURCE_DATASET,
            "dataset_url": SOURCE_URL,
            "dataset_sha256": dataset_sha,
            "expected_dataset_sha256": expected_sha256,
            "label_provenance": (
                "Third-party corrected LongMemEval-S release. Its dataset card says "
                "answers and evidence-session IDs were replaced with reviewed values; "
                "the Synapse team did not produce those labels. This is not a blind "
                "human annotation study performed specifically for Synapse."
            ),
            "upstream_dataset": "xiaowu0162/longmemeval-cleaned, revision 98d7416c24c778c2fee6e6f3006e7a073259d48f",
            "dataset_revision": DATASET_REVISION,
            "dataset_card_reported_sha256": DATASET_CARD_REPORTED_SHA256,
            "reviewed_ground_truth_sha256": "97dd2b0df872f12c682041c44fe8082f9fa55ab882f5c78c9237b7c77ba9bb00",
            "dataset_card_sha256_mismatch": dataset_sha != DATASET_CARD_REPORTED_SHA256,
            **dataset_stats,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "synapse_memory_engine": sdk_version,
            "embedding_provider": getattr(embedder, "provider_name", "deterministic"),
            "embedding_model": model_name if embedding_provider == "sentence-transformers" else None,
            "embedding_model_revision": model_revision if embedding_provider == "sentence-transformers" else None,
            "embedding_dimension": int(embedder.dimension),
            "embedding_is_semantic": bool(embedder.is_semantic),
            "embedding_runtime_versions": {
                name: _distribution_version(name)
                for name in ("sentence-transformers", "torch", "transformers", "huggingface-hub")
            },
        },
        "configuration": {
            "positive_evidence_questions_scored": len(positive_queries),
            "all_questions_in_dataset": len(prepared),
            "top_k": top_k,
            "candidate_k": candidate_k,
            "token_budget": token_budget,
            "session_text": "session date plus role/content turns; annotation-only has_answer flags are never passed to the embedder or ranker",
            "future_leakage_rule": "Exclude every session later than question_date; fail if any reviewed evidence session is missing after this filter.",
            "packing_input": "same hybrid-ranked candidate list per query; top_k_fit uses only the first top_k; other packers use the first candidate_k",
        },
        "ranking": ranking_summary,
        "context_packing": packing_summary,
        "per_query": per_query,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--embedding-provider",
        choices=("deterministic", "sentence-transformers"),
        default="sentence-transformers",
    )
    parser.add_argument("--model-name", default=MODEL_NAME)
    parser.add_argument("--model-revision", default=MODEL_REVISION)
    parser.add_argument("--expected-sha256", default=EXPECTED_DATASET_SHA256)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=10)
    parser.add_argument("--token-budget", type=int, default=512)
    args = parser.parse_args()
    try:
        result = evaluate(
            dataset_path=args.dataset.resolve(),
            embedding_provider=args.embedding_provider,
            expected_sha256=args.expected_sha256,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            token_budget=args.token_budget,
            model_name=args.model_name,
            model_revision=args.model_revision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        print("LongMemEval evaluation failed: {0}".format(exc), file=sys.stderr)
        return 2

    print("LongMemEval-S evaluated; full result written to {0}".format(args.output.resolve()))
    print(
        "Questions={0} positive-evidence={1} no-evidence={2}; sessions={3}; "
        "future sessions excluded={4}".format(
            result["source"]["question_count"],
            result["source"]["positive_evidence_question_count"],
            result["source"]["no_evidence_question_count"],
            result["source"]["candidate_session_instance_count"],
            result["source"]["future_sessions_excluded_before_query_time"],
        )
    )
    print(
        "Embedder={0}; model={1}; revision={2}; dimension={3}".format(
            result["environment"]["embedding_provider"],
            result["environment"]["embedding_model"],
            result["environment"]["embedding_model_revision"],
            result["environment"]["embedding_dimension"],
        )
    )
    print("\nRetrieval:")
    for method, values in result["ranking"].items():
        print(
            "  {0:<14} recall@5={1:.3f} recall@10={2:.3f} MRR={3:.3f} nDCG@10={4:.3f}".format(
                method,
                values["mean_recall_at_5"],
                values["mean_recall_at_10"],
                values["mean_mrr"],
                values["mean_ndcg_at_10"],
            )
        )
    print("\nPacking:")
    for method, values in result["context_packing"].items():
        retention = values["mean_retained_candidate_evidence_recall"]
        print(
            "  {0:<20} evidence_recall={1:.3f} candidate_recall={2:.3f} "
            "candidate_retention={3} tokens={4:.1f}".format(
                method,
                values["mean_evidence_recall_at_budget"],
                values["mean_candidate_recall"],
                "n/a" if retention is None else "{0:.3f}".format(retention),
                values["mean_tokens_used"],
            )
        )
    if any(v["over_budget_query_count"] for v in result["context_packing"].values()):
        print("One or more budgeted packers exceeded the configured token budget.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
