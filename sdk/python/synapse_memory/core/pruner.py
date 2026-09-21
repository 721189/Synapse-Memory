from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import time
import logging
from synapse_memory.core.decay_engine import DecayEngine

logger = logging.getLogger("Pruner")

class PruningPolicy(ABC):
    """Abstract base class for memory pruning policies."""
    
    @abstractmethod
    def identify_targets(self, memories: List[Dict[str, Any]]) -> List[str]:
        """Returns list of memory IDs to be pruned based on the policy."""
        pass

class TTLPruningPolicy(PruningPolicy):
    """Prunes memories older than a specified duration in seconds."""
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds

    def identify_targets(self, memories: List[Dict[str, Any]]) -> List[str]:
        now = time.time()
        return [m["id"] for m in memories if now - m["created_at"] > self.ttl_seconds]

class ConfidencePruningPolicy(PruningPolicy):
    """Prunes memories below a certain confidence threshold."""
    def __init__(self, confidence_threshold: float):
        self.confidence_threshold = confidence_threshold

    def identify_targets(self, memories: List[Dict[str, Any]]) -> List[str]:
        return [m["id"] for m in memories if m.get("confidence", 0.0) < self.confidence_threshold]

class CapacityPruningPolicy(PruningPolicy):
    """Prunes lowest-relevance memories to maintain capacity."""
    def __init__(self, max_records: int, decay_engine: DecayEngine):
        self.max_records = max_records
        self.decay_engine = decay_engine

    def identify_targets(self, memories: List[Dict[str, Any]]) -> List[str]:
        if len(memories) <= self.max_records:
            return []
        
        # Calculate relevance using robust DecayEngine
        scored = []
        for m in memories:
            relevance = self.decay_engine.calculate_relevance(m)
            scored.append((relevance, m["id"]))
            
        scored.sort(key=lambda x: x[0])
        excess = len(memories) - self.max_records
        return [m_id for _, m_id in scored[:excess]]

class PruningEngine:
    """Engine to orchestrate multiple pruning policies."""
    def __init__(self, policies: List[PruningPolicy]):
        self.policies = policies

    def run_pruning(self, memories: List[Dict[str, Any]]) -> List[str]:
        targets = set()
        for policy in self.policies:
            targets.update(policy.identify_targets(memories))
        return list(targets)
