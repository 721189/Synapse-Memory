#!/usr/bin/env python3
"""Build blinded human relevance-review packets from developer-query benchmark outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List


def build_packet(dataset_path: Path, result_paths: List[Path], max_queries: int = 12, seed: int = 20261027):
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    documents = {str(item["id"]): item for item in dataset["documents"]}
    queries = {str(item["id"]): item for item in dataset["queries"]}
    results = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]

    selected_ids = sorted(
        queries,
        key=lambda query_id: hashlib.sha256(query_id.encode("utf-8")).hexdigest(),
    )[:max_queries]
    rng = random.Random(seed)
    packet = []
    mapping = []
    for query_id in selected_ids:
        candidates = set()
        for result in results:
            per_query = result.get("per_query", {}).get(query_id, {})
            for method_data in per_query.get("ranking", {}).values():
                candidates.update(str(value) for value in method_data.get("top_k_ids", []))
            for method_data in per_query.get("context_packing", {}).values():
                candidates.update(str(value) for value in method_data.get("selected_ids", []))
        candidates = [candidate for candidate in candidates if candidate in documents]
        if not candidates:
            continue
        rng.shuffle(candidates)
        case_id = "CASE-" + hashlib.sha256(query_id.encode("utf-8")).hexdigest()[:10]
        candidate_rows = []
        for index, memory_id in enumerate(candidates, start=1):
            blind_id = "CAND-{0:03d}".format(index)
            candidate_rows.append({
                "candidate_id": blind_id,
                "content": str(documents[memory_id]["content"]),
                "reviewer_a": {"relevance": None, "notes": ""},
                "reviewer_b": {"relevance": None, "notes": ""},
            })
            mapping.append({
                "case_id": case_id,
                "candidate_id": blind_id,
                "query_id": query_id,
                "memory_id": memory_id,
            })
        packet.append({
            "case_id": case_id,
            "query": str(queries[query_id]["query"]),
            "instructions": "Rate each candidate for answering this query: 0=irrelevant, 1=related but insufficient, 2=directly useful evidence. Judge content, not ranking position.",
            "relevance_scale": {"0": "irrelevant", "1": "related but insufficient", "2": "directly useful evidence"},
            "candidates": candidate_rows,
        })
    return packet, mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--packet-output", type=Path, default=Path("developer-relevance-review-packet.json"))
    parser.add_argument("--mapping-output", type=Path, default=Path("developer-relevance-review-key.json"))
    parser.add_argument("--max-queries", type=int, default=12)
    args = parser.parse_args()
    if args.max_queries < 1:
        parser.error("--max-queries must be positive")
    packet, mapping = build_packet(args.dataset, args.results, args.max_queries)
    args.packet_output.write_text(json.dumps({
        "schema_version": 1,
        "title": "Blind developer-memory relevance review",
        "review_protocol": {
            "reviewers_required": 2,
            "blind": "Reviewers receive the packet without method names, model names, gold IDs, or the mapping key.",
            "scale": "0=irrelevant, 1=related but insufficient, 2=directly useful evidence",
            "adjudication": "Compare reviewer labels after both submit. Resolve disagreements before computing final metrics.",
        },
        "label_status": "awaiting_independent_human_review",
        "cases": packet,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.mapping_output.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Created {0} blind review cases; labels are still unreviewed.".format(len(packet)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
