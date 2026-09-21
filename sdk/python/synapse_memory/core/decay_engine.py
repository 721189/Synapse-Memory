import math
import time
from typing import Any, Dict, Optional


class DecayEngine:
    """
    Robust Ebbinghaus-style decay and reinforcement engine.
    
    Formula:
        Score = Confidence * exp(-(now - last_accessed) / Strength)
        Strength = BaseStrength * (1 + (access_count * factor) * multiplier)
    """
    def __init__(
        self,
        category_configs: Optional[Dict[str, Dict[str, float]]] = None
    ):
        self.category_configs = category_configs or {}
        self.default_config = {
            'base_strength': 86400.0,
            'reinforcement_factor': 0.45
        }
        self.decay_floor = 0.10

    def _get_config(self, category: str) -> Dict[str, float]:
        return self.category_configs.get(category, self.default_config)

    def calculate_relevance(
        self,
        memory: Dict[str, Any],
        now: Optional[float] = None
    ) -> float:
        """Calculates current relevance score for a memory node."""
        if now is None:
            now = time.time()
            
        last_accessed = memory.get("last_accessed_at", memory["created_at"])
        elapsed = max(0.0, now - last_accessed)
        
        config = self._get_config(memory.get("category", "interaction"))
        
        access_count = memory.get("access_count", 0)
        feedback_multiplier = memory.get("feedback_multiplier", 1.0)
        
        strength = config['base_strength'] * (
            1 + (access_count * config['reinforcement_factor']) * feedback_multiplier
        )
        
        confidence = float(memory.get("confidence", 0.5))
        return max(self.decay_floor, confidence * math.exp(-elapsed / max(1.0, strength)))

    def calculate_retention(
        self,
        base_relevance: float,
        created_epoch: float,
        access_count: int = 0,
        now: Optional[float] = None
    ) -> float:
        """
        Calculates Ebbinghaus retention for a memory based on age and access count.
        """
        if now is None:
            now = time.time()
        elapsed = max(0.0, now - created_epoch)
        effective_half_life = 86400.0 * 2.0 * (1.0 + 0.5 * access_count)
        decay = math.exp(-elapsed / effective_half_life)
        return max(self.decay_floor, base_relevance * decay)

    def compute_feedback_boost(
        self,
        current_confidence: float,
        feedback_type: str
    ) -> float:
        """Helper to adjust confidence based on feedback."""
        boost = 0.05 if feedback_type == "positive" else -0.1
        return max(0.1, min(1.0, current_confidence + boost))

    def reinforce(
        self,
        memory: Dict[str, Any],
        feedback_value: float = 1.0
    ) -> Dict[str, Any]:
        """
        Applies reinforcement to a memory node.
        
        feedback_value > 0: Positive reinforcement
        feedback_value < 0: Negative reinforcement (penalty)
        """
        memory["access_count"] = memory.get("access_count", 0) + 1
        memory["last_accessed_at"] = time.time()
        
        current_multiplier = memory.get("feedback_multiplier", 1.0)
        memory["feedback_multiplier"] = max(
            0.1,
            current_multiplier + (feedback_value * 0.1)
        )
        return memory

