# Synapse Memory

**Synapse Memory** is a sovereign, token-aware cognitive memory substrate with database-level multi-tenancy, cryptographic encryption, and dynamic context budgeting for AI agents and LLM applications.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub Repository](https://img.shields.io/badge/GitHub-721189%2FSynapse--Memory-181717?logo=github)](https://github.com/721189/Synapse-Memory)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Node Version](https://img.shields.io/badge/node-18%2B-green.svg)](https://nodejs.org/)
[![PostgreSQL](https://img.shields.io/badge/pgvector-HNSW-336791.svg)](https://github.com/pgvector/pgvector)

---

## Technical Overview

Synapse Memory bridges stateless LLM interactions and stateful agent memory architectures. Rather than unbounded vector dumps that trigger context overflow and model hallucinations, Synapse provides a deterministic cognitive pipeline:

1. **Storage & Cryptographic Integrity**: Authenticated symmetric encryption at rest (Fernet AES-128-CBC with HMAC-SHA256) ensures sensitive context and vector representations are never stored unencrypted.
2. **Database-Level Multi-Tenancy**:
   - **PostgreSQL (`pgvector`)**: Native Row-Level Security (RLS) via `app.current_tenant` session settings and HNSW vector index acceleration.
   - **SQLite**: Automatic tenant indexing and cryptographic key scoping.
3. **Enterprise Identity & RBAC**: Cryptographically hashed API keys (`syn_live_*`), granular authorization scopes (`memories:read`, `memories:write`, `admin`), and immutable audit logging.
4. **Dynamic Context Budgeting**: Exact 0/1 Knapsack dynamic programming optimizer (`KnapsackPacker`) selecting the highest-relevance memories within fixed token envelopes.
5. **Temporal Decay & Reinforcement**: Ebbinghaus-derived exponential decay engine (`DecayEngine`) discounting aged records while boosting actively reinforced nodes.
6. **Semantic Deduplication**: Dual-stage verification (lexical token screening + dense vector cosine similarity) preventing index fragmentation.
7. **Hybrid Retrieval**: Dense semantic similarity search combined with sparse lexical BM25 ranking fused using Reciprocal Rank Fusion (RRF, $k=60$).

---

## Architecture & Operational Modes

```
+-------------------------------------------------------------------------+
|                          Agent Orchestrators                            |
|       (LangChain BaseMemory, LlamaIndex BaseMemory, CrewAI, REST API)   |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                      Unified Cognitive Gateway                          |
|  +--------------------+  +--------------------+  +--------------------+ |
|  |    HybridSearch    |  |   KnapsackPacker   |  |    DecayEngine     | |
|  |   (BM25 + Dense)   |  |   (0/1 DP Solver)  |  | (Ebbinghaus Decay) | |
|  +--------------------+  +--------------------+  +--------------------+ |
|  +--------------------+  +--------------------+  +--------------------+ |
|  |    Deduplicator    |  |   SecurityManager  |  |   MemoryManager    | |
|  |  (Lexical+Cosine)  |  | (Hashed RBAC/Audit)|  | (Tenant Isolation) | |
|  +--------------------+  +--------------------+  +--------------------+ |
+-------------------------------------------------------------------------+
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v                                       v
    [Embedded Storage Mode]                 [Enterprise Clustered Mode]
     - SQLite Storage Engine                 - PostgreSQL + pgvector (HNSW)
     - Fernet AES-128-CBC Encryption         - Row-Level Security (RLS)
     - Zero external dependencies            - FastAPI + Express API Gateway
     - Single-tenant / embedded use          - Multi-tenant cluster with Docker/Helm
```

---

## Quick Start (Python SDK)

### Installation
```bash
# Minimal embedded engine
pip install -e sdk/python

# Full enterprise suite (pgvector, sentence-transformers, LangChain, LlamaIndex)
pip install -e "sdk/python[all]"
```

### Multi-Tenant Ingestion & Retrieval Example
```python
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker

# 1. Initialize encrypted store and embedding engine
store = SQLiteMemoryStore(db_path="agent_memory.db")
embedder = SynapseEmbedder(provider="local")
manager = MemoryManager(store=store, embedder=embedder, max_record_limit=100)

# 2. Ingest memory scoped to a tenant with automatic semantic deduplication
mem_id, action, tokens = manager.ingest_with_deduplication(
    content="Production PostgreSQL connection timeout is configured to 3000ms.",
    category="infrastructure",
    confidence=0.95,
    tenant_id="tenant_finance_org"
)
print(f"Memory {mem_id}: Action={action} ({tokens} tokens)")

# 3. Hybrid search over tenant-isolated encrypted records
searcher = HybridSearch(alpha=0.6, k=60)
results = searcher.search(
    query="Postgres timeout settings",
    corpus=store.get_all_memories(tenant_id="tenant_finance_org"),
    query_vector=embedder.embed_query("Postgres timeout settings"),
    top_k=5
)

# 4. Knapsack context packing into LLM prompt budget
packer = KnapsackPacker(token_budget=1024)
packed_memories = packer.pack(results)
print(f"Packed {len(packed_memories)} memories into token envelope.")
```

---

## Turnkey Deployment

### Docker Compose
Run the unified multi-container stack (Node.js Gateway + FastAPI Engine + PostgreSQL pgvector + Redis Cache):

```bash
cd deploy
docker compose up --build
```

### Kubernetes (Helm Chart)
Deploy production clusters with horizontal pod autoscaling and automated TLS:

```bash
helm install synapse-memory deploy/helm-chart \
  --set secrets.existingSecret=my-k8s-credentials \
  --set ingress.hosts[0].host=synapse.yourdomain.com
```

---

## Drop-In Framework Integrations

### LangChain (`BaseMemory`)
```python
from synapse_memory.integrations.langchain import SynapseLangChainMemory
from synapse_memory.core import SQLiteMemoryStore, SynapseEmbedder

memory = SynapseLangChainMemory(
    memory_key="chat_history",
    max_token_budget=1500,
    store=SQLiteMemoryStore("agent_memory.db"),
    embedder=SynapseEmbedder(provider="local")
)
```

### LlamaIndex (`BaseMemory`)
```python
from synapse_memory.integrations.llamaindex import SynapseLlamaIndexMemory
from synapse_memory.core import SQLiteMemoryStore, SynapseEmbedder

memory = SynapseLlamaIndexMemory(
    max_token_limit=2048,
    store=SQLiteMemoryStore("agent_memory.db"),
    embedder=SynapseEmbedder(provider="local")
)
```

---

## Testing & Quality Verification

```bash
# Run unit & integration test suite
PYTHONPATH=sdk/python pytest sdk/python/tests -v

# Static type analysis
mypy sdk/python --config-file setup.cfg

# Security vulnerability audit
bandit -r sdk/python -s B101,B104,B311

# Code linting
flake8 sdk/python --config=setup.cfg
```

---

## Security Policy

Please refer to [SECURITY.md](SECURITY.md) for our vulnerability disclosure guidelines and designated security channels.

## License

Synapse Memory is open-source software licensed under the [MIT License](LICENSE).
