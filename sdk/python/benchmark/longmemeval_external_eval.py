#!/usr/bin/env python3
"""Evaluate Synapse retrieval against corrected LongMemEval-S evidence labels.

The public dataset is downloaded at runtime and never committed or redistributed.
Its exact content is verified by SHA-256 because the hosting revision is mutable.
This script evaluates session retrieval, not answer-generation quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

DATASET_REPO = "agentscope-ai/ReMe_longmemeval_clean_s_v2"
DATASET_FILE = "longmemeval_s_reme_cleaned.json"
DATASET_SHA256 = "f8b308cc2d184d029dd2226e0c0a88eeac298e6318706d588e015beb1b83fc7d"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_question_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")


def session_text(session: Any) -> str:
    """Flatten a session's role/content turns without changing evidence IDs."""
    if not isinstance(session, list):
        return str(session)
    parts = []
    for turn in session:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "unknown")).strip()
        content = str(turn.get("content", "")).strip()
        if content:
            parts.append(role + ": " + content)
    return "\n".join(parts)[:4000]


def select_records(records: Sequence[Dict[str, Any]], max_per_type: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Deterministically stratify by question type and enforce temporal cutoffs."""
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        by_type.setdefault(str(row.get("question_type", "unknown")), []).append(row)

    selected = []
    counts: Dict[str, int] = {}
    for kind, rows in sorted(by_type.items()):
        ordered = sorted(
            rows,
            key=lambda row: hashlib.sha256(str(row["question_id"]).encode("utf-8")).hexdigest(),
        )
        chosen = ordered[:max_per_type]
        counts[kind] = len(chosen)
        selected.extend(chosen)
    selected.sort(key=lambda row: str(row["question_id"]))
    return selected, counts


def build_query_corpora(records: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build isolated per-question corpora using only sessions at/before question time."""
    docs: List[Dict[str, Any]] = []
    queries: List[Dict[str, Any]] = []
    excluded = Counter()
    for row in records:
        qid = str(row["question_id"])
        qtime = parse_question_date(str(row["question_date"]))
        ids = row.get("haystack_session_ids") or []
        dates = row.get("haystack_dates") or []
        sessions = row.get("haystack_sessions") or []
        if not (len(ids) == len(dates) == len(sessions)):
            raise ValueError("Mismatched session/date/id arrays for " + qid)

        relevant_source = set(str(x) for x in (row.get("answer_session_ids") or []))
        local_ids = set()
        relevant_ids = []
        for session_id, date_value, session in zip(ids, dates, sessions):
            session_id = str(session_id)
            session_time = parse_question_date(str(date_value))
            if session_time > qtime:
                continue
            document_id = qid + "::" + session_id
            if document_id in local_ids:
                continue
            local_ids.add(document_id)
            text = session_text(session)
            if not text:
                continue
            docs.append({
                "id": document_id,
                "tenant_id": qid,
                "category": "conversation_session",
                "content": text,
            })
            if session_id in relevant_source:
                relevant_ids.append(document_id)

        if not relevant_source:
            excluded["unanswerable_no_evidence"] += 1
            continue
        if not relevant_ids:
            excluded["evidence_missing_after_temporal_filter"] += 1
            continue
        queries.append({
            "id": qid,
            "tenant_id": qid,
            "query": str(row["question"]),
            "relevant_memory_ids": sorted(set(relevant_ids)),
            "question_type": str(row.get("question_type", "unknown")),
            "question_date": str(row["question_date"]),
            "answer": str(row.get("answer", "")),
        })
    return docs, {"queries": queries, "excluded": dict(excluded)}


def tokenize(text: str) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if len(token) > 1]


def bm25_scores(query: str, documents: Sequence[str]) -> List[float]:
    """Small transparent BM25 implementation for session-level ranking."""
    tokenized = [tokenize(text) for text in documents]
    if not tokenized:
        return []
    lengths = [len(tokens) for tokens in tokenized]
    average_length = sum(lengths) / max(1, len(lengths))
    document_frequency: Counter[str] = Counter()
    term_frequencies = []
    for tokens in tokenized:
        frequencies = Counter(tokens)
        term_frequencies.append(frequencies)
        document_frequency.update(frequencies.keys())
    n = len(tokenized)
    scores = [0.0] * n
    for term in tokenize(query):
        df = document_frequency.get(term, 0)
        if not df:
            continue
        inverse = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for index, frequencies in enumerate(term_frequencies):
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            length_norm = 1.5 * (1.0 - 0.75 + 0.75 * lengths[index] / max(1.0, average_length))
            scores[index] += inverse * tf * (1.5 + 1.0) / (tf + length_norm)
    return scores


def metrics(ranked_ids: Sequence[str], relevant_ids: Sequence[str], k: int) -> Dict[str, float]:
    relevant = set(relevant_ids)
    top = list(ranked_ids[:k])
    hits = [1 if item in relevant else 0 for item in top]
    reciprocal_rank = next((1.0 / (i + 1) for i, hit in enumerate(hits) if hit), 0.0)
    dcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(hits))
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return {
        "recall_at_k": round(sum(hits) / len(relevant), 6),
        "precision_at_k": round(sum(hits) / k, 6),
        "mrr_at_k": round(reciprocal_rank, 6),
        "ndcg_at_k": round(dcg / idcg if idcg else 0.0, 6),
    }


def mean_metrics(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    if not rows:
        return {}
    return {
        key: round(sum(row[key] for row in rows) / len(rows), 6)
        for key in ("recall_at_k", "precision_at_k", "mrr_at_k", "ndcg_at_k")
    }


def evaluate(path: Path, max_per_type: int = 20, k: int = 5) -> Dict[str, Any]:
    actual_sha = sha256_file(path)
    if actual_sha != DATASET_SHA256:
        raise ValueError(
            "Dataset SHA-256 mismatch: expected {0}, got {1}".format(DATASET_SHA256, actual_sha)
        )
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != 500:
        raise ValueError("Expected exactly 500 LongMemEval-S records.")
    selected, strata = select_records(records, max_per_type)
    docs, query_data = build_query_corpora(selected)
    queries = query_data["queries"]
    if not docs or not queries:
        raise ValueError("No answerable, temporally valid queries were selected.")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Install sdk/python/benchmark/requirements-semantic.txt before running."
        ) from exc
    import numpy as np

    model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device="cpu")
    texts = [doc["content"] for doc in docs]
    doc_vectors = model.encode(
        texts, batch_size=64, normalize_embeddings=True,
        convert_to_numpy=True, show_progress_bar=True,
    )
    id_to_index = {doc["id"]: index for index, doc in enumerate(docs)}
    docs_by_tenant: Dict[str, List[Dict[str, Any]]] = {}
    for doc in docs:
        docs_by_tenant.setdefault(doc["tenant_id"], []).append(doc)

    per_query = []
    method_rows: Dict[str, List[Dict[str, float]]] = {
        "lexical_bm25": [], "dense_cosine": [], "hybrid_rrf": []
    }
    by_type: Dict[str, Dict[str, List[Dict[str, float]]]] = {}
    for query in queries:
        candidates = docs_by_tenant[query["tenant_id"]]
        candidate_texts = [doc["content"] for doc in candidates]
        bm25 = bm25_scores(query["query"], candidate_texts)
        query_vector = model.encode(
            [query["query"]], normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        )[0]
        dense = [
            float(np.dot(query_vector, doc_vectors[id_to_index[doc["id"]]]))
            for doc in candidates
        ]
        lexical_order = sorted(
            range(len(candidates)),
            key=lambda i: (-bm25[i], candidates[i]["id"]),
        )
        dense_order = sorted(
            range(len(candidates)),
            key=lambda i: (-dense[i], candidates[i]["id"]),
        )
        lexical_rank = {idx: rank for rank, idx in enumerate(lexical_order, 1)}
        dense_rank = {idx: rank for rank, idx in enumerate(dense_order, 1)}
        hybrid_order = sorted(
            range(len(candidates)),
            key=lambda i: (
                -(1.0 / (60 + lexical_rank[i]) + 1.0 / (60 + dense_rank[i])),
                candidates[i]["id"],
            ),
        )
        orders = {
            "lexical_bm25": lexical_order,
            "dense_cosine": dense_order,
            "hybrid_rrf": hybrid_order,
        }
        row = {
            "question_id": query["id"],
            "question_type": query["question_type"],
            "query": query["query"],
            "candidate_session_count": len(candidates),
            "relevant_session_count": len(query["relevant_memory_ids"]),
            "methods": {},
        }
        for method, order in orders.items():
            ranked_ids = [candidates[index]["id"] for index in order]
            values = metrics(ranked_ids, query["relevant_memory_ids"], k)
            row["methods"][method] = {
                "top_k_session_ids": ranked_ids[:k],
                "metrics": values,
            }
            method_rows[method].append(values)
            by_type.setdefault(query["question_type"], {}).setdefault(method, []).append(values)
        per_query.append(row)

    return {
        "schema_version": 1,
        "benchmark": "LongMemEval-S ReMe Cleaned-S (reviewed evidence-session labels)",
        "source": {
            "repo_id": DATASET_REPO,
            "filename": DATASET_FILE,
            "dataset_sha256": actual_sha,
            "upstream_revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f",
            "reviewed_ground_truth_sha256": "97dd2b0df872f12c682041c44fe8082f9fa55ab882f5c78c9237b7c77ba9bb00",
            "generated_file_sha256": DATASET_SHA256,
            "url": "https://huggingface.co/datasets/agentscope-ai/ReMe_longmemeval_clean_s_v2",
        },
        "model": {"name": MODEL_NAME, "revision": MODEL_REVISION, "dimension": int(doc_vectors.shape[1])},
        "config": {"max_questions_per_type": max_per_type, "top_k": k, "stratified_sample_counts": strata, "temporal_policy": "exclude every session later than question_date", "fusion": "RRF k=60"},
        "dataset_counts": {
            "source_questions": len(records),
            "sampled_questions": len(selected),
            "evaluated_answerable_questions": len(queries),
            "session_documents": len(docs),
            "excluded_questions": query_data["excluded"],
        },
        "overall": {method: mean_metrics(rows) for method, rows in method_rows.items()},
        "by_question_type": {
            kind: {method: mean_metrics(rows) for method, rows in methods.items()}
            for kind, methods in sorted(by_type.items())
        },
        "per_query": per_query,
        "limitations": [
            "Uses reviewed evidence-session labels; it does not evaluate answer-generation correctness.",
            "This is a deterministic stratified subset of LongMemEval-S, not the full 500-question leaderboard run.",
            "Evidence session relevance is inherited from the published corrected dataset; no claim is made that these labels are independent of the benchmark authors.",
            "Sessions are ranked within each question's own haystack, with all sessions after question_date excluded to prevent future leakage.",
            "LongMemEval is general long-term conversational memory, not a developer-only benchmark.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-path", type=Path, help="Use a local copy; its SHA-256 must match.")
    parser.add_argument("--output", type=Path, default=Path("longmemeval-external-results.json"))
    parser.add_argument("--max-per-type", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.max_per_type < 1 or args.top_k < 1:
        parser.error("--max-per-type and --top-k must be positive")
    try:
        if args.dataset_path:
            dataset_path = args.dataset_path
        else:
            from huggingface_hub import hf_hub_download
            dataset_path = Path(hf_hub_download(
                repo_id=DATASET_REPO,
                filename=DATASET_FILE,
                repo_type="dataset",
                revision="main",
            ))
        result = evaluate(dataset_path, max_per_type=args.max_per_type, k=args.top_k)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        print("LongMemEval evaluation failed: {0}".format(exc), file=sys.stderr)
        return 2
    print("LongMemEval evaluation saved: {0}".format(args.output.resolve()))
    print("Evaluated answerable queries: {0}".format(result["dataset_counts"]["evaluated_answerable_questions"]))
    for method, values in result["overall"].items():
        print("{0}: Recall@{1}={2:.3f}, MRR={3:.3f}, nDCG={4:.3f}".format(
            method, args.top_k, values["recall_at_k"], values["mrr_at_k"], values["ndcg_at_k"]
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
