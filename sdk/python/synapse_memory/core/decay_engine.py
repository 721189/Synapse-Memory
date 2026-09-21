import time
import math
from typing import Dict, Any

class DecayEngine:
    """
    Robust Ebbinghaus-style decay and reinforcement engine.
    
    Formula:
        Score = Confidence * exp(-(now - last_accessed) / Strength)
        Strength = BaseStrength * (1 + (access_count * reinforcement_factor) * feedback_multiplier)
    """
    def __init__(self, category_configs: Dict[str, Dict[str, float]]):
        # Config structure: { 'category': { 'base_strength': 86400, 'reinforcement_factor': 0.45 } }
        self.category_configs = category_configs
        self.default_config = {'base_strength': 86400.0, 'reinforcement_factor': 0.45}

    def _get_config(self, category: str) -> Dict[str, float]:
        return self.category_configs.get(category, self.default_config)

    def calculate_relevance(self, memory: Dict[str, Any], now: float = None) -> float:
        """Calculates current relevance score for a memory node."""
        if now is None:
            now = time.time()
            
        last_accessed = memory.get("last_accessed_at", memory["created_at"])
        elapsed = max(0.0, now - last_accessed)
        
        config = self._get_config(memory.get("category", "interaction"))
        
        # Calculate strength (durability)
        access_count = memory.get("access_count", 0)
        feedback_multiplier = memory.get("feedback_multiplier", 1.0)
        
        strength = config['base_strength'] * (1 + (access_count * config['reinforcement_factor']) * feedback_multiplier)
        
        # Exponential decay
        return memory.get("confidence", 0.5) * math.exp(-elapsed / strength)

    def reinforce(self, memory: Dict[str, Any], feedback_value: float = 1.0) -> Dict[str, Any]:
        """
        Applies reinforcement to a memory node.
        
        feedback_value > 0: Positive reinforcement
        feedback_value < 0: Negative reinforcement (penalty)
        """
        memory["access_count"] = memory.get("access_count", 0) + 1
        memory["last_accessed_at"] = time.time()
        
        current_multiplier = memory.get("feedback_multiplier", 1.0)
        # Update feedback multiplier with a smoothing factor
        memory["feedback_multiplier"] = max(0.1, current_multiplier + (feedback_value * 0.1))
        
        return memory
