import math
from typing import List, Dict, Any, Tuple, Optional

class HybridSearch:
    """
    State-of-the-art Hybrid Information Retrieval Engine.
    Combines Lexical Sparse Search (real BM25 algorithm) with Dense Cosine 
    vector similarity, fused via Reciprocal Rank Fusion (RRF).
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, rrf_k: int = 60):
        """
        Args:
            k1: BM25 term frequency saturation parameter.
            b: BM25 document length normalization parameter.
            rrf_k: Constant used to smooth Reciprocal Rank Fusion ranks.
        """
        self.k1 = k1
        self.b = b
        self.rrf_k = rrf_k

    def _calculate_bm25_scores(self, query_terms: List[str], corpus: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculates exact Okapi BM25 scores for all documents in the corpus.
        """
        scores = {doc["id"]: 0.0 for doc in corpus}
        if not query_terms or not corpus:
            return scores

        N = len(corpus)
        doc_lengths = {doc["id"]: len(doc.get("content", "").lower().split()) for doc in corpus}
        avgdl = sum(doc_lengths.values()) / max(1, N)

        # 1. Compute Document Frequency (df) for each query term
        df = {}
        for term in query_terms:
            df[term] = sum(1 for doc in corpus if term in doc.get("content", "").lower().split())

        # 2. Compute BM25 scores for each document
        for term in query_terms:
            n_q = df.get(term, 0)
            # Standard IDF with floor to avoid negative values
            idf = math.log((N - n_q + 0.5) / (n_q + 0.5) + 1.0)
            idf = max(0.0001, idf)

            for doc in corpus:
                doc_id = doc["id"]
                content_words = doc.get("content", "").lower().split()
                tf = content_words.count(term)
                
                if tf > 0:
                    dl = doc_lengths[doc_id]
                    numerator = tf * (self.k1 + 1.0)
                    denominator = tf + self.k1 * (1.0 - self.b + self.b * (dl / max(1.0, avgdl)))
                    scores[doc_id] += idf * (numerator / denominator)

        return scores

    def _calculate_dense_similarities(
        self, 
        query_vector: List[float], 
        corpus: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Calculates true Cosine similarity between query vector and document embeddings."""
        similarities = {doc["id"]: 0.0 for doc in corpus}
        if not query_vector:
            return similarities

        norm_q = math.sqrt(sum(q * q for q in query_vector))
        if norm_q == 0.0:
            return similarities

        for doc in corpus:
            doc_id = doc["id"]
            doc_vec = doc.get("embedding", [])
            
            if not doc_vec or len(doc_vec) != len(query_vector):
                continue

            dot_product = sum(q * d for q, d in zip(query_vector, doc_vec))
            norm_d = math.sqrt(sum(d * d for d in doc_vec))
            
            if norm_d > 0.0:
                similarities[doc_id] = dot_product / (norm_q * norm_d)

        return similarities

    def fused_search(
        self, 
        query: str, 
        documents: List[Dict[str, Any]], 
        query_vector: Optional[List[float]] = None,
        top_k: int = 10,
        category_filter: Optional[str] = None,
        tenant_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes unified Reciprocal Rank Fusion (RRF) combining Sparse BM25 and Dense Cosine ranks.
        Provides retrieval explainability via detailed score breakdown.
        
        Formula:
            RRF_Score(d) = 1 / (rrf_k + rank_bm25(d)) + 1 / (rrf_k + rank_dense(d))
            
        Args:
            query: Raw query string.
            documents: List of memory documents.
            query_vector: Dense vector array representing the query.
            top_k: Maximum elements to return.
            category_filter: Filter by memory category.
            tenant_filter: Filter by tenant_id.
        """
        # 1. Apply Filtering
        filtered_docs = documents
        if category_filter:
            filtered_docs = [d for d in filtered_docs if d.get("category") == category_filter]
        if tenant_filter:
            filtered_docs = [d for d in filtered_docs if d.get("tenant_id") == tenant_filter]
            
        if not filtered_docs:
            return []

        query_terms = [t for t in query.lower().split() if len(t) > 1]
        
        # 2. Compute pure Lexical BM25 scores and sort to get Sparse ranks
        bm25_scores = self._calculate_bm25_scores(query_terms, filtered_docs)
        sparse_ranking = sorted(filtered_docs, key=lambda x: bm25_scores[x["id"]], reverse=True)
        sparse_ranks = {doc["id"]: idx + 1 for idx, doc in enumerate(sparse_ranking)}

        # 3. Compute pure Cosine Vector similarities and sort to get Dense ranks
        dense_scores = self._calculate_dense_similarities(query_vector or [], filtered_docs)
        dense_ranking = sorted(filtered_docs, key=lambda x: dense_scores[x["id"]], reverse=True)
        dense_ranks = {doc["id"]: idx + 1 for idx, doc in enumerate(dense_ranking)}

        # 4. Apply Reciprocal Rank Fusion (RRF)
        results = []
        for doc in filtered_docs:
            doc_id = doc["id"]
            
            # Reciprocal sparse rank score
            rank_s = sparse_ranks.get(doc_id, len(filtered_docs))
            rrf_sparse = 1.0 / (self.rrf_k + rank_s)
            
            # Reciprocal dense rank score
            rank_d = dense_ranks.get(doc_id, len(filtered_docs))
            rrf_dense = 1.0 / (self.rrf_k + rank_d)

            fused_score = rrf_sparse + rrf_dense
            
            # Structure Explainability Data
            explanation = {
                "lexical_score": bm25_scores[doc_id],
                "semantic_score": dense_scores[doc_id],
                "fused_score": fused_score,
                "sparse_rank": rank_s,
                "dense_rank": rank_d
            }

            scored_doc = doc.copy()
            scored_doc["relevance_score"] = fused_score
            scored_doc["explanation"] = explanation
            results.append(scored_doc)

        # 5. Final Ranking
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:top_k]
