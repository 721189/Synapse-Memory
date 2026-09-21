# 🧬 SynapseMemory: Universal Active RAG & Cognitive Memory Layer

A high-performance, lightweight, and sovereign long-term memory engine designed to eliminate unbounded context inflation and LLM hallucinations in agentic workflows. Compatible with **LangChain**, **LlamaIndex**, and standalone LLM APIs.

---

## ✨ Features Included
- **0/1 Knapsack Context Packing:** Dynamic Programming algorithm optimized to maximize prompt semantic density within a strict, developer-defined token budget.
- **Ebbinghaus Forgetting Curve:** Mathematical temporal decay curve that reduces memory weights over time, stabilized dynamically by repeated accesses and RLAIF rewards.
- **Hybrid Search Fusion:** Unified retrieval fuser blending dense Cosine Semantic similarity with sparse Jaccard Lexical keyword matching.
- **Microservice API:** Self-hostable **FastAPI server** with pre-configured CORS and endpoint telemetry.
- **Drop-in Integrations:** Modular subclasses for seamless injection into active LangChain and LlamaIndex agent loops.

---

## 🚀 Quickstart Installation

1. Clone or download the `/sdk/python/` repository.
2. Install the lightweight dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the local simulation and integration benchmark suite:
   ```bash
   PYTHONPATH=. python3 synapse_memory/main.py
   ```

---

## ⚡ Hosting the REST API Microservice

Spin up the self-hosted RAG Proxy server on port `8000`:
```bash
PYTHONPATH=. python3 synapse_memory/api/fastapi_server.py
```

### Active REST Endpoints:
- **`GET /health`**: Returns engine telemetry and indexed node counts.
- **`POST /ingest`**: Registers new raw prompt segments and computes token footprints.
  ```json
  {
    "content": "User prefers PostgreSQL hosted on GCP Cloud SQL with pgvector.",
    "category": "infrastructure",
    "confidence": 0.95
  }
  ```
- **`POST /query`**: Performs hybrid search, applies temporal decay, and solves the Knapsack budget constraints.
  ```json
  {
    "prompt": "What database infrastructure should we use?",
    "max_token_budget": 1500
  }
  ```
- **`POST /feedback`**: Applies positive or negative reinforcement signals (RLAIF) to node parameters.

---

## 🧩 Framework Integrations

### LangChain Integration:
```python
from synapse_memory.examples.langchain_integration import SynapseLangChainMemory

# Drop-in replacement for any LangChain memory buffer
memory = SynapseLangChainMemory(max_token_budget=1500)

# Connects seamlessly to your agent
agent = initialize_agent(
    tools=tools,
    llm=llm,
    memory=memory,  # Done!
    agent="zero-shot-react-description"
)
```

### LlamaIndex Integration:
```python
from synapse_memory.examples.llamaindex_integration import SynapseLlamaIndexMemory

memory = SynapseLlamaIndexMemory(max_token_limit=2048)

# Seamlessly injects relevant memories during chat streams
context_history = memory.get("What database should we configure?")
```

---

## 📄 License
Licensed under the Apache License, Version 2.0. Clean, sovereign, and completely open-source.
