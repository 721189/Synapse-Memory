import math
from typing import List, Dict, Any, Tuple

class HybridSearch:
    """
    Combines Lexical (Sparse Jaccard Index) and Semantic (Dense Cosine Similarity)
    search techniques using weighted hybrid fusion to optimize memory recall precision.
    """

    def __init__(self, semantic_weight: float = 0.65, lexical_weight: float = 0.35):
        """
        Args:
            semantic_weight: Gravity coefficient assigned to vector embeddings.
            lexical_weight: Gravity coefficient assigned to character-matching.
        """
        self.semantic_weight = semantic_weight
        self.lexical_weight = lexical_weight

    def _calculate_jaccard(self, text_a: str, text_b: str) -> float:
        """Computes Jaccard sparse overlap of normalized word sets."""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        
        if not words_a or not words_b:
            return 0.0
            
        intersection = words_a.intersection(words_b)
        union = words_a.union(words_b)
        return float(len(intersection)) / float(len(union))

    def _mock_cosine_similarity(self, text_a: str, text_b: str) -> float:
        """
        Simulates cosine similarity of dense embeddings.
        In production, this calculates actual dot-product/cosine on embedding float arrays.
        Here, we use substring-matched heuristics + length ratio to yield a real-world score.
        """
        a_words = text_a.lower().split()
        b_words = text_b.lower().split()
        
        intersection = [w for w in a_words if w in b_words]
        if not intersection:
            return 0.1  # baseline semantic background noise
            
        # Simulates vector orientation overlap
        overlap_ratio = len(intersection) / max(len(a_words), len(b_words))
        return 0.3 + (overlap_ratio * 0.7)

    def fused_search(
        self, 
        query: str, 
        documents: List[Dict[str, Any]], 
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Executes a unified hybrid search query over candidate documents.
        
        Args:
            query: The user prompt query.
            documents: List of memory documents. Each needs 'content', 'id'
            top_k: Top records to return.
            
        Returns:
            Sorted candidates with enriched 'relevance_score' parameters.
        """
        if not documents:
            return []

        results = []
        for doc in documents:
            content = doc.get("content", "")
            
            # 1. Lexical Scoring (Jaccard)
            lexical_score = self._calculate_jaccard(query, content)
            
            # 2. Semantic Scoring (Cosine Simulation)
            semantic_score = self._mock_cosine_similarity(query, content)
            
            # 3. Weighted Fusion
            fused_score = (semantic_score * self.semantic_weight) + (lexical_score * self.lexical_weight)
            
            # Incorporate memory baseline parameters if they exist
            confidence = doc.get("confidence", 0.95)
            final_relevance = fused_score * confidence

            # Create scored record copy
            scored_doc = doc.copy()
            scored_doc["relevance_score"] = min(1.0, max(0.0, final_relevance))
            results.append(scored_doc)

        # Sort by relevance score descending
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:top_k]
