import requests
import time
from typing import Dict, List, Optional, Any

class SynapseMemoryClient:
    """
    Sovereign Enterprise Python SDK for SynapseMemory Gateway.
    Integrates directly with CrewAI, LangChain, and LlamaIndex agents.
    """
    def __init__(self, endpoint: str = "http://localhost:3000", license_key: Optional[str] = None):
        self.endpoint = endpoint.rstrip('/')
        self.license_key = license_key
        self.headers = {
            "Content-Type": "application/json"
        }
        if license_key:
            self.headers["Authorization"] = f"Bearer {license_key}"

    def upsert_memory(self, content: str, category: str = "general", confidence: float = 0.95, tenant_id: str = "enterprise_core") -> Dict[str, Any]:
        """
        Pushes a new memory fact into the cognitive graph.
        Triggers active semantic embedding generation & security scrubbers.
        """
        payload = {
            "content": content,
            "category": category,
            "confidence": confidence,
            "tenantId": tenant_id
        }
        res = requests.post(f"{self.endpoint}/api/memories/create", json=payload, headers=self.headers)
        res.raise_for_status()
        return res.json()

    def retrieve_memories(self, query: str, limit: int = 3, tenant_id: str = "enterprise_core") -> List[Dict[str, Any]]:
        """
        Performs high-relevancy Hybrid Vector + Sparse BM25 Search.
        Eliminates LLM hallucinations and misinformation.
        """
        payload = {
            "query": query,
            "tenantId": tenant_id
        }
        res = requests.post(f"{self.endpoint}/api/retrieval/hybrid-search", json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json()
        return data.get("results", [])[:limit]

    def pack_context(self, query: str, max_tokens: int = 1500) -> Dict[str, Any]:
        """
        Solves the Knapsack optimization problem for target token budgets.
        Pack premium high-relevancy memory items cleanly into LLM prompts.
        """
        payload = {
            "query": query,
            "maxTokens": max_tokens
        }
        res = requests.post(f"{self.endpoint}/api/retrieval/knapsack-pack", json=payload, headers=self.headers)
        res.raise_for_status()
        return res.json()

    def scrub_text(self, text: str) -> str:
        """
        Queries the sovereign PII and security scrubber before sending to external LLM providers.
        """
        payload = {
            "text": text
        }
        res = requests.post(f"{self.endpoint}/api/security/scrub", json=payload, headers=self.headers)
        res.raise_for_status()
        return res.json().get("cleanText", text)
