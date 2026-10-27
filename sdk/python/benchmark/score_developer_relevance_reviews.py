#!/usr/bin/env python3
"""Score completed, independently reviewed developer-memory relevance judgments.

Reviewers should score separate copies of the blind packet. Merge only their
labels into one packet after both are submitted. Keep the mapping key private.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Cannot read valid JSON from {0}: {1}".format(path, exc)) from exc


def _valid_grade(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
        raise ValueError("{0} must be an integer grade 0, 1, or 2.".format(where))
    return value


def _cohen_kappa(left: Sequence[int], right: Sequence[int], weighted: bool = False) -> Optional[float]:
    if not left or len(left) != len(right):
        return None
    categories = (0, 1, 2)
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    if not weighted:
        left_counts = {grade: left.count(grade) / len(left) for grade in categories}
        right_counts = {grade: right.count(grade) / len(right) for grade in categories}
        expected = sum(left_counts[grade] * right_counts[grade] for grade in categories)
        denominator = 1.0 - expected
        return None if abs(denominator) < 1e-12 else (observed - expected) / denominator

    def weight(a: int, b: int) -> float:
        return 1.0 - ((a - b) / 2.0) ** 2

    observed_weighted = sum(weight(a, b) for a, b in zip(left, right)) / len(left)
    left_counts = {grade: left.count(grade) / len(left) for grade in categories}
    right_counts = {grade: right.count(grade) / len(right) for grade in categories}
    expected_weighted = sum(
        left_counts[a] * right_counts[b] * weight(a, b)
        for a in categories
        for b in categories
    )
    denominator = 1.0 - expected_weighted
    return None if abs(denominator) < 1e-12 else (observed_weighted - expected_weighted) / denominator


def _dcg(grades: Sequence[int], k: int) -> float:
    return sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades[:k]))


def _ranking_metrics(ranked_ids: Sequence[str], grades_by_id: Dict[str, int], k: int) -> Dict[str, Any]:
    judged_grades = [grades_by_id[memory_id] for memory_id in ranked_ids[:k]]
    direct_ranks = [index for index, grade in enumerate(judged_grades) if grade == 2]
    reciprocal_rank = 1.0 / (direct_ranks[0] + 1) if direct_ranks else 0.0
    direct_count = sum(grade == 2 for grade in judged_grades)
    pool_grades = list(grades_by_id.values())
    ideal_grades = sorted(pool_grades, reverse=True)[:k]
    ideal_dcg = _dcg(ideal_grades, k)
    ndcg = _dcg(judged_grades, k) / ideal_dcg if ideal_dcg else 0.0
    pool_direct_count = sum(grade == 2 for grade in pool_grades)
    return {
        "ndcg_at_5": round(ndcg, 6),
        "mrr_at_5_direct_useful": round(reciprocal_rank, 6),
        "precision_at_5_direct_useful": round(direct_count / k, 6),
        # This is recall within the reviewed candidate pool, not full-corpus recall.
        "recall_at_5_within_reviewed_pool": (
            round(direct_count / pool_direct_count, 6) if pool_direct_count else None
        ),
        "direct_useful_items_in_reviewed_pool": pool_direct_count,
    }


def score_reviews(packet: Dict[str, Any], mapping: List[Dict[str, Any]],
                  results: List[Tuple[str, Dict[str, Any]]], k: int = 5) -> Dict[str, Any]:
    if k < 1:
        raise ValueError("k must be positive.")
    cases = packet.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Review packet must contain a non-empty cases list.")
    map_by_blind_id: Dict[Tuple[str, str], Dict[str, str]] = {}
    pair_to_blind: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for row in mapping:
        try:
            key = (str(row["case_id"]), str(row["candidate_id"]))
            value = {
                "query_id": str(row["query_id"]),
                "memory_id": str(row["memory_id"]),
            }
        except (KeyError, TypeError) as exc:
            raise ValueError("Mapping key has a malformed row.") from exc
        if key in map_by_blind_id:
            raise ValueError("Mapping key has duplicate case/candidate pair: {0}".format(key))
        map_by_blind_id[key] = value
        pair = (value["query_id"], value["memory_id"])
        if pair in pair_to_blind:
            raise ValueError("Mapping key repeats a query/memory pair: {0}".format(pair))
        pair_to_blind[pair] = key

    reviewer_a: List[int] = []
    reviewer_b: List[int] = []
    adjudicated_by_pair: Dict[Tuple[str, str], int] = {}
    query_pool: Dict[str, Dict[str, int]] = {}
    found_keys = set()
    disagreement_count = 0
    for case in cases:
        case_id = str(case.get("case_id", ""))
        candidates = case.get("candidates")
        if not case_id or not isinstance(candidates, list) or not candidates:
            raise ValueError("Every case needs a case_id and non-empty candidates.")
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id", ""))
            blind_key = (case_id, candidate_id)
            if not candidate_id or blind_key not in map_by_blind_id:
                raise ValueError("Packet candidate has no matching mapping-key row: {0}".format(blind_key))
            if blind_key in found_keys:
                raise ValueError("Packet repeats a blind candidate ID: {0}".format(blind_key))
            found_keys.add(blind_key)
            a = _valid_grade(candidate.get("reviewer_a", {}).get("relevance"), str(blind_key) + " reviewer_a")
            b = _valid_grade(candidate.get("reviewer_b", {}).get("relevance"), str(blind_key) + " reviewer_b")
            reviewer_a.append(a)
            reviewer_b.append(b)
            if a == b:
                grade = a
            else:
                disagreement_count += 1
                if "adjudicated_relevance" not in candidate:
                    raise ValueError(
                        "Unresolved reviewer disagreement for {0}; add adjudicated_relevance "
                        "after discussion.".format(blind_key)
                    )
                grade = _valid_grade(candidate["adjudicated_relevance"], str(blind_key) + " adjudicated_relevance")
                if not str(candidate.get("adjudication_note", "")).strip():
                    raise ValueError(
                        "Disagreement {0} needs an adjudication_note.".format(blind_key)
                    )
            mapped = map_by_blind_id[blind_key]
            query_id, memory_id = mapped["query_id"], mapped["memory_id"]
            query_pool.setdefault(query_id, {})[memory_id] = grade
            adjudicated_by_pair[(query_id, memory_id)] = grade

    if found_keys != set(map_by_blind_id):
        missing = sorted(set(map_by_blind_id) - found_keys)
        raise ValueError("Mapping key contains candidates absent from packet: {0}".format(missing[:5]))

    method_rows: Dict[str, List[Dict[str, Any]]] = {}
    for artifact_name, result in results:
        provider = str(result.get("environment", {}).get("embedding_provider", artifact_name))
        per_query = result.get("per_query")
        if not isinstance(per_query, dict):
            raise ValueError("Result {0} lacks a per_query object.".format(artifact_name))
        for method in ("lexical_bm25", "dense_cosine", "hybrid_rrf"):
            label = "{0} / {1}".format(provider, method)
            scored_queries = []
            for query_id, pool_grades in sorted(query_pool.items()):
                query_record = per_query.get(query_id)
                if not isinstance(query_record, dict):
                    raise ValueError(
                        "Result {0} is missing reviewed query {1}.".format(artifact_name, query_id)
                    )
                ranking = query_record.get("ranking", {}).get(method)
                if not isinstance(ranking, dict):
                    raise ValueError(
                        "Result {0} is missing ranking {1} for query {2}.".format(
                            artifact_name, method, query_id
                        )
                    )
                ranked_ids = [str(value) for value in ranking.get("top_k_ids", [])[:k]]
                unknown = [memory_id for memory_id in ranked_ids if memory_id not in pool_grades]
                if unknown:
                    raise ValueError(
                        "Reviewed query {0} has unjudged ranked candidates in {1}/{2}: {3}".format(
                            query_id, artifact_name, method, unknown
                        )
                    )
                scored_queries.append(_ranking_metrics(ranked_ids, pool_grades, k))
            if not scored_queries:
                raise ValueError("No reviewed queries were scored.")
            recall_values = [
                row["recall_at_5_within_reviewed_pool"]
                for row in scored_queries
                if row["recall_at_5_within_reviewed_pool"] is not None
            ]
            method_rows[label] = scored_queries
            method_rows[label + ".__summary__"] = [{
                "reviewed_query_count": len(scored_queries),
                "mean_ndcg_at_5": round(statistics.fmean(row["ndcg_at_5"] for row in scored_queries), 6),
                "mean_mrr_at_5_direct_useful": round(
                    statistics.fmean(row["mrr_at_5_direct_useful"] for row in scored_queries), 6
                ),
                "mean_precision_at_5_direct_useful": round(
                    statistics.fmean(row["precision_at_5_direct_useful"] for row in scored_queries), 6
                ),
                "mean_recall_at_5_within_reviewed_pool": (
                    round(statistics.fmean(recall_values), 6) if recall_values else None
                ),
                "queries_with_direct_useful_judgments": len(recall_values),
            }]

    summaries = {
        key[:-len(".__summary__")]: value[0]
        for key, value in method_rows.items()
        if key.endswith(".__summary__")
    }
    return {
        "schema_version": 1,
        "evaluation": "blinded_developer_query_human_relevance_review",
        "label_status": "adjudicated",
        "reviewed_case_count": len(cases),
        "reviewed_candidate_count": len(reviewer_a),
        "judgments": {
            "scale": {"0": "irrelevant", "1": "related but insufficient", "2": "directly useful evidence"},
            "reviewer_disagreement_count": disagreement_count,
            "reviewer_disagreement_rate": round(disagreement_count / len(reviewer_a), 6),
            "exact_agreement_rate": round(sum(a == b for a, b in zip(reviewer_a, reviewer_b)) / len(reviewer_a), 6),
            "cohen_kappa_nominal": (
                round(value, 6) if (value := _cohen_kappa(reviewer_a, reviewer_b)) is not None else None
            ),
            "cohen_kappa_quadratic_weighted": (
                round(value, 6) if (value := _cohen_kappa(reviewer_a, reviewer_b, weighted=True)) is not None else None
            ),
            "adjudication": "Equal labels accepted as final; every disagreement required an explicit adjudicated_relevance and non-empty adjudication_note.",
        },
        "ranking_metrics": summaries,
        "limitations": [
            "Metrics cover only queries and candidate documents in the reviewed blind packet, not the entire corpus.",
            "Recall@5 is recall within the reviewed candidate pool, not full-corpus recall.",
            "The packet is a small curated developer-query sample; results are exploratory and not a general production-quality claim.",
            "Dense results depend on the recorded embedding provider/model and benchmark run.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="Merged packet with both reviewers' labels.")
    parser.add_argument("--mapping-key", type=Path, required=True, help="Private mapping key; do not publish.")
    parser.add_argument("--results", type=Path, nargs="+", required=True, help="Raw retrieval benchmark JSON artifacts.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    try:
        packet = _read_json(args.packet)
        mapping = _read_json(args.mapping_key)
        if not isinstance(packet, dict) or not isinstance(mapping, list):
            raise ValueError("Packet must be a JSON object and mapping key must be a JSON list.")
        results = []
        for path in args.results:
            result = _read_json(path)
            if not isinstance(result, dict):
                raise ValueError("Benchmark result {0} must be a JSON object.".format(path))
            results.append((path.name, result))
        scored = score_reviews(packet, mapping, results, args.k)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(scored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("Developer relevance scoring failed: {0}".format(exc), file=sys.stderr)
        return 2
    print("Scored {0} reviewed cases / {1} candidates; output: {2}".format(
        scored["reviewed_case_count"], scored["reviewed_candidate_count"], args.output.resolve()
    ))
    for method, metrics in scored["ranking_metrics"].items():
        print("{0}: nDCG@5={1:.3f} MRR@5={2:.3f} P@5={3:.3f}".format(
            method, metrics["mean_ndcg_at_5"], metrics["mean_mrr_at_5_direct_useful"],
            metrics["mean_precision_at_5_direct_useful"],
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
