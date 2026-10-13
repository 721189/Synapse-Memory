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

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to the gateway and unwrap the canonical response envelope.

        The engine wraps every JSON body as {success, data, request_id}; older
        gateways returned the payload flat. This transparently supports both so
        the SDK never breaks when the backend is upgraded.
        """
        res = requests.post(f"{self.endpoint}{path}", json=payload, headers=self.headers)
        res.raise_for_status()
        body = res.json()
        if isinstance(body, dict) and "success" in body and "data" in body:
            if body.get("success") is False:
                raise RuntimeError(f"Synapse error: {body.get('error') or body.get('detail')}")
            return body["data"]
        return body

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
        return self._post("/api/memories/create", payload)

    def retrieve_memories(self, query: str, limit: int = 3, tenant_id: str = "enterprise_core") -> List[Dict[str, Any]]:
        """
        Performs high-relevancy Hybrid Vector + Sparse BM25 Search.
        Eliminates LLM hallucinations and misinformation.
        """
        payload = {
            "prompt": query,
            "tenantId": tenant_id
        }
        data = self._post("/query", payload)
        # Canonical retrieval envelope returns fused context + injected nodes.
        nodes = data.get("injected_nodes", []) if isinstance(data, dict) else []
        if not nodes and isinstance(data, dict) and data.get("results"):
            nodes = data["results"]
        return nodes[:limit]

    def pack_context(self, query: str, max_tokens: int = 1500, tenant_id: str = "enterprise_core") -> Dict[str, Any]:
        """
        Solves the Knapsack optimization problem for target token budgets.
        Pack premium high-relevancy memory items cleanly into LLM prompts.
        """
        payload = {
            "prompt": query,
            "maxTokens": max_tokens,
            "tenantId": tenant_id
        }
        return self._post("/api/retrieval/knapsack-pack", payload)

    def scrub_text(self, text: str) -> str:
        """
        Queries the sovereign PII and security scrubber before sending to external LLM providers.
        """
        payload = {
            "text": text
        }
        data = self._post("/api/security/scrub", payload)
        if isinstance(data, dict):
            return data.get("cleanText") or data.get("scrubbedText") or text
        return text
