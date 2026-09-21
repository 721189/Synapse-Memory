from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Any, Dict, List, Optional


logger = logging.getLogger("Deduplicator")


class Action(Enum):
    CREATE = "CREATE"
    MERGE = "MERGE"
    REJECT = "REJECT"


@dataclass
class MergeResult:
    action: Action
    target_id: Optional[str]
    reason: str
    provenance: Dict[str, Any]


# Alias for backwards compatibility
DeduplicationResult = MergeResult


class Deduplicator:
    def __init__(self, category_thresholds: Dict[str, float], default_threshold: float = 0.85):
        self.category_thresholds = category_thresholds
        self.default_threshold = default_threshold

    def _get_threshold(self, category: str) -> float:
        return self.category_thresholds.get(category, self.default_threshold)

    def _jaccard_similarity(self, content_a: str, content_b: str) -> float:
        set_a = set(content_a.lower().split())
        set_b = set(content_b.lower().split())
        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))
        return intersection / union if union > 0 else 0.0

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def check_duplicate(self, new_mem: Dict[str, Any], existing_mems: List[Dict[str, Any]]) -> MergeResult:
        threshold = self._get_threshold(new_mem.get("category", "interaction"))

        for existing in existing_mems:
            # 1. Exact Match Check (e.g., hash of content or specific ID)
            if new_mem.get("id") == existing.get("id"):
                return MergeResult(Action.REJECT, existing["id"], "Exact ID match", {"type": "exact"})

            # 2. Lexical Similarity (Jaccard)
            lexical_sim = self._jaccard_similarity(new_mem["content"], existing["content"])

            # 3. Semantic Similarity (Cosine)
            semantic_sim = self._cosine_similarity(new_mem.get("embedding", []), existing.get("embedding", []))

            # 4. Conflict & Supersession Detection (Production-Grade Heuristic)
            if semantic_sim > threshold:
                # If high semantic similarity, compare metadata to decide supersession
                new_conf = new_mem.get("confidence", 0.5)
                old_conf = existing.get("confidence", 0.5)

                # If new memory has significantly higher confidence, suggest supersession (merge)
                if new_conf > old_conf + 0.1:
                    return MergeResult(
                        Action.MERGE,
                        existing["id"],
                        "Higher confidence supersession",
                        {"new_conf": new_conf, "old_conf": old_conf}
                    )

                # Check for explicit contradiction in content
                content_new = new_mem["content"].lower()
                content_old = existing["content"].lower()
                if ("true" in content_new and "false" in content_old) or ("false" in content_new and "true" in content_old):
                    return MergeResult(Action.REJECT, None, "Direct contradiction detected", {"type": "conflict"})

            # 5. Threshold Decision
            if semantic_sim >= threshold or (lexical_sim > 0.9 and semantic_sim > 0.7):
                return MergeResult(
                    Action.MERGE,
                    existing["id"],
                    "Semantic/Lexical similarity threshold exceeded",
                    {"semantic_sim": semantic_sim, "lexical_sim": lexical_sim}
                )

        return MergeResult(Action.CREATE, None, "No duplicates found", {})

