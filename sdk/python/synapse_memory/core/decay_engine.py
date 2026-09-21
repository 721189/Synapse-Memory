import math
import time
from typing import Dict, Any

class DecayEngine:
    """
    Implements the Ebbinghaus Forgetting Curve for temporal memory decay.
    
    Mathematical Formulation:
        Retention_t = Base_Relevance * exp(-t / Strength)
        Strength = Base_Strength * (1 + Access_Count * Boost_Coeff)
    """

    def __init__(
        self, 
        base_strength_seconds: float = 86400.0,  # 1 day default half-life base
        boost_coefficient: float = 0.45,         # Accesses compound memory stabilization by 45%
        decay_floor: float = 0.15                # Memories never decay below a floor of 15% relevance
    ):
        self.base_strength = base_strength_seconds
        self.boost_coefficient = boost_coefficient
        self.decay_floor = decay_floor

    def calculate_retention(
        self, 
        base_relevance: float, 
        created_epoch: float, 
        access_count: int,
        last_accessed_epoch: float = 0.0
    ) -> float:
        """
        Computes the current relevance of a memory item based on Ebbinghaus forgetting logic.
        
        Args:
            base_relevance: Initial score generated during vector indexing.
            created_epoch: Epoch timestamp when the memory was registered.
            access_count: Number of times this memory was retrieved.
            last_accessed_epoch: Optional timestamp of the last access.
            
        Returns:
            The optimized, decayed relevance score (between decay_floor and base_relevance).
        """
        now = time.time()
        elapsed_seconds = max(0.0, now - created_epoch)

        # Strength increases with repetitions (access counts)
        stabilized_strength = self.base_strength * (1.0 + (access_count * self.boost_coefficient))
        
        # Calculate exponential retention
        retention = math.exp(-elapsed_seconds / max(1.0, stabilized_strength))
        
        # Compute final relevance and clip to floor boundary
        final_relevance = base_relevance * retention
        return max(self.decay_floor, min(base_relevance, final_relevance))

    def compute_feedback_boost(
        self, 
        current_confidence: float, 
        feedback_type: str
    ) -> float:
        """
        Adjusts node confidence dynamically based on RLAIF (Reinforcement Learning from AI Feedback).
        
        Args:
            current_confidence: Active confidence float.
            feedback_type: 'positive' (reinforce) or 'negative' (hallucination warning).
        """
        if feedback_type == "positive":
            # Asymptotically approach 1.0 confidence
            return current_confidence + (1.0 - current_confidence) * 0.20
        elif feedback_type == "negative":
            # Punish confidence by 35% for hallucination reports
            return max(0.05, current_confidence * 0.65)
        return current_confidence
