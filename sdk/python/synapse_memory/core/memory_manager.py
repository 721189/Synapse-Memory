import os
import time
import logging
import asyncio
import uuid
import math
import re
from typing import Dict, Any, Tuple, Optional, Union
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.pgvector_db import PGVectorMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.deduplicator import Deduplicator, Action
from synapse_memory.core.pruner import PruningEngine, CapacityPruningPolicy
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.observability import log_event, instrument_operation

logger = logging.getLogger("SynapseMemoryManager")

# Explicit, operator-visible capacity ceiling for the in-memory store.
# Override with SYNAPSE_MAX_RECORD_LIMIT (must be a positive integer).
# The historical default was 50 records; change this only with capacity planning.
try:
    DEFAULT_MAX_RECORD_LIMIT = int(os.environ.get("SYNAPSE_MAX_RECORD_LIMIT", "50"))
    if DEFAULT_MAX_RECORD_LIMIT <= 0:
        raise ValueError
except (TypeError, ValueError):
    DEFAULT_MAX_RECORD_LIMIT = 50
    logger.warning(
        "Invalid SYNAPSE_MAX_RECORD_LIMIT; using default max_record_limit=50"
    )


def estimate_token_cost(content: str) -> int:
    """Production token estimator: prefers tiktoken when installed, else safe heuristic.
    Heuristic calibrated to ~4 chars/token (OpenAI/Gemini average for English).
    """
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return max(15, len(enc.encode(content)) + 8)
    except Exception:
        # Fallback: chars/4 + overhead. More accurate than words*1.35 for code/CJK.
        return max(15, int(len(content) / 4) + 10)


class MemoryManager:
    """
    Manages high-level memory operations: Semantic Deduplication,
    Multi-tenant Ingestion, Auto-Eviction, and Cognitive Memory Pruning.
    Compatible with both SQLiteMemoryStore and PGVectorMemoryStore backends.

    Capacity: ``max_record_limit`` bounds how many memories a tenant may hold.
    After every ingest, :meth:`auto_prune_store` evicts the lowest-relevance
    memories until the count is within the limit. The default comes from the
    ``SYNAPSE_MAX_RECORD_LIMIT`` environment variable and falls back to 50
    records (the historical default). Expose this setting explicitly -- never
    rely on the silent default in production, or older memories are pruned
    without the operator realizing a cap is active.
    """

    def __init__(
        self,
        store: Union[SQLiteMemoryStore, PGVectorMemoryStore, Any],
        embedder: SynapseEmbedder,
        category_thresholds: Optional[Dict[str, float]] = None,
        max_record_limit: int = DEFAULT_MAX_RECORD_LIMIT
    ):
        self.store = store
        self.embedder = embedder
        self.deduplicator = Deduplicator(category_thresholds or {})
        self.max_record_limit = int(max_record_limit)
        self.decay_engine = DecayEngine({})
        self.pruner = PruningEngine([CapacityPruningPolicy(max_record_limit, self.decay_engine)])

    @instrument_operation("ingest_memory")
    def ingest_with_deduplication(
        self,
        content: str,
        category: str = "interaction",
        confidence: float = 0.95,
        tenant_id: str = "default",
        source_type: str = "unspecified",
        source_id: Optional[str] = None,
        observed_at: Optional[float] = None,
        valid_from: Optional[float] = None,
        valid_until: Optional[float] = None,
        trust_level: str = "unverified",
    ) -> Tuple[str, str, int]:
        """
        Ingests a new memory node.
        Runs semantic similarity comparison against existing indexed nodes within the same tenant.

        When a near-duplicate is found (Action.MERGE), the incoming content is preserved:
        a new memory row is created and linked to the old one via a supersession chain
        (``superseded_by``). The old row is marked superseded but retained for provenance.
        Confidence is merged conservatively: the higher of the two confidences is kept,
        and a small reinforcement is applied only when the new content is at least as
        confident as the existing one. This ensures updated/corrected facts are never
        silently discarded.

        When Action.REJECT is returned (direct contradiction), the incoming memory is
        still recorded as a separate row so no information is lost; the caller can
        decide how to resolve the conflict.
        """
        # Validate provenance metadata before embedding or writing anything.
        allowed_sources = {"unspecified", "user", "tool", "document", "system", "import"}
        allowed_trust = {"unverified", "low", "medium", "high", "authoritative"}
        if source_type not in allowed_sources:
            raise ValueError("source_type must be one of: " + ", ".join(sorted(allowed_sources)))
        if trust_level not in allowed_trust:
            raise ValueError("trust_level must be one of: " + ", ".join(sorted(allowed_trust)))
        if source_id is not None and (
            not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", source_id)
        ):
            raise ValueError("source_id must be a safe 1-256 character identifier")
        for field_name, value in (("observed_at", observed_at), ("valid_from", valid_from), ("valid_until", valid_until)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(field_name + " must be a finite non-negative timestamp")
        if valid_from is not None and valid_until is not None and valid_until <= valid_from:
            raise ValueError("valid_until must be later than valid_from")
        provenance = {
            "source_type": source_type,
            "source_id": source_id,
            "observed_at": float(observed_at) if observed_at is not None else time.time(),
            "valid_from": float(valid_from) if valid_from is not None else None,
            "valid_until": float(valid_until) if valid_until is not None else None,
            "trust_level": trust_level,
            "supersession_reason": None,
            "retracted_at": None,
            "retraction_reason": None,
        }

        # 1. Generate dense vector representation
        new_vector = self.embedder.embed_query(content)
        existing_memories = self.store.get_memories_by_category(category, tenant_id=tenant_id)

        # 2. Check for duplicate nodes
        new_node_base = {
            "content": content,
            "category": category,
            "tenant_id": tenant_id,
            "embedding": new_vector,
            "confidence": confidence,
        }
        result = self.deduplicator.check_duplicate(new_node_base, existing_memories)

        # 3. Handle Deduplication Result
        if result.action == Action.MERGE and result.target_id:
            match_id = result.target_id
            match_mem = next((m for m in existing_memories if m["id"] == match_id), None)
            old_confidence = float(match_mem.get("confidence", 0.5)) if match_mem else 0.5

            merged_confidence = max(old_confidence, confidence)
            if confidence >= old_confidence:
                merged_confidence = min(1.0, merged_confidence + 0.02)

            new_memory_id = f"mem_{uuid.uuid4().hex[:16]}"
            token_cost = estimate_token_cost(content)
            new_node = {
                "id": new_memory_id,
                "tenant_id": tenant_id,
                "content": content,
                "category": category,
                "confidence": merged_confidence,
                "created_at": time.time(),
                "access_count": 0,
                "token_cost": token_cost,
                "embedding": new_vector,
                "embedding_dimension": len(new_vector),
                "model_version": getattr(self.embedder, "model_name", "unknown"),
                "supersedes": match_id,
                "superseded_by": None,
                **{**provenance, "supersession_reason": result.reason},
            }
            self.store.insert_memory(new_node)

            try:
                self.store.mark_superseded(match_id, new_memory_id, tenant_id=tenant_id)
            except Exception:
                log_event("memory_deduplication", "MERGE_MARK_FAILED", {"target_id": match_id, "tenant_id": tenant_id})

            # The supersession row above is a real row: it counts toward
            # max_record_limit like any other write, so enforce the capacity
            # ceiling on this path too. Without this, repeated merges of the same
            # fact grow the table without bound (the superseded lineage rows are
            # never evicted) even though CREATE/REJECT do prune.
            self.auto_prune_store(tenant_id=tenant_id)

            log_event("memory_deduplication", "MERGED_PRESERVED", {
                "old_id": match_id, "new_id": new_memory_id, "tenant_id": tenant_id,
                "old_confidence": old_confidence, "new_confidence": merged_confidence,
            })
            return new_memory_id, "MERGED", token_cost

        elif result.action == Action.REJECT:
            log_event("memory_deduplication", "REJECTED_RECORDED", {"tenant_id": tenant_id, "reason": result.reason})
            new_memory_id = f"mem_{uuid.uuid4().hex[:16]}"
            token_cost = estimate_token_cost(content)
            new_node = {
                "id": new_memory_id,
                "tenant_id": tenant_id,
                "content": content,
                "category": category,
                "confidence": confidence,
                "created_at": time.time(),
                "access_count": 0,
                "token_cost": token_cost,
                "embedding": new_vector,
                "embedding_dimension": len(new_vector),
                "model_version": getattr(self.embedder, "model_name", "unknown"),
                "supersedes": None,
                "superseded_by": None,
                **provenance,
            }
            self.store.insert_memory(new_node)
            self.auto_prune_store(tenant_id=tenant_id)
            return new_memory_id, "REJECTED", token_cost

        # 4. Otherwise, save as a clean memory
        log_event("memory_deduplication", "CREATED", {"tenant_id": tenant_id})
        memory_id = f"mem_{uuid.uuid4().hex[:16]}"
        token_cost = estimate_token_cost(content)
        new_node = {
            "id": memory_id,
            "tenant_id": tenant_id,
            "content": content,
            "category": category,
            "confidence": confidence,
            "created_at": time.time(),
            "access_count": 0,
            "token_cost": token_cost,
            "embedding": new_vector,
            # Embedding provenance: stamps which dimension + model wrote this row,
            # so schema/drift checks can detect stale index entries after an
            # embedding-model upgrade (and trigger a re-embed pass).
            "embedding_dimension": len(new_vector),
            "model_version": getattr(self.embedder, "model_name", "unknown"),
            "supersedes": None,
            "superseded_by": None,
            **provenance,
        }
        self.store.insert_memory(new_node)

        # 5. Execute Auto-Pruning if limit is breached
        self.auto_prune_store(tenant_id=tenant_id)

        return memory_id, "CREATED", token_cost

    async def ingest_async(self, *args, **kwargs) -> Tuple[str, str, int]:
        return await asyncio.to_thread(self.ingest_with_deduplication, *args, **kwargs)

    def auto_prune_store(self, tenant_id: Optional[str] = None) -> int:
        """
        Evicts memories based on the configured PruningEngine within a tenant.

        Returns:
            The number of successfully evicted memory nodes.
        """
        memories = self.store.get_all_memories(tenant_id=tenant_id, include_superseded=True)
        targets = self.pruner.run_pruning(memories)

        evicted = 0
        for target_id in targets:
            self.store.delete_memory(target_id, tenant_id=tenant_id)
            evicted += 1
            log_event("memory_pruning", "EVICTED", {"target_id": target_id, "tenant_id": tenant_id})

        return evicted

    async def prune_async(self, tenant_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.auto_prune_store, tenant_id=tenant_id)
