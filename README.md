# Synapse Memory

**Synapse Memory** is a sovereign, token-aware cognitive memory substrate designed for autonomous AI agents and large language model workflows.

[![CI Status](https://github.com/synapse-memory/synapse_memory/actions/workflows/ci.yml/badge.svg)](https://github.com/synapse-memory/synapse_memory/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Node Version](https://img.shields.io/badge/node-18%2B-green.svg)](https://nodejs.org/)

---

## Technical Overview

Synapse Memory bridges the gap between stateless LLM interactions and stateful agent memory architectures. Rather than relying solely on unbounded vector dumps that trigger context overflow and model hallucinations, Synapse provides a layered cognitive pipeline:

1. **Storage & Cryptographic Integrity**: Authenticated symmetric encryption at rest (Fernet AES-128-CBC with HMAC-SHA256) ensures sensitive context and vector representations are never persisted unencrypted.
2. **Dynamic Context Budgeting**: An exact 0/1 Knapsack dynamic programming optimizer (`KnapsackPacker`) selects the highest cumulative relevance memory nodes to strictly satisfy fixed token budgets.
3. **Temporal Decay & Reinforcement**: An Ebbinghaus-derived exponential decay engine (`DecayEngine`) discounts aged memories over time while boosting frequently accessed or positively reinforced nodes.
4. **Semantic Deduplication**: Dual-stage verification (lexical Jaccard token screening + dense vector cosine similarity) identifies redundant knowledge and merges access counters without fragmenting the index.
5. **Hybrid Information Retrieval**: Dense semantic similarity search combined with sparse lexical BM25 ranking fused using Reciprocal Rank Fusion (RRF, $k=60$).

---

## Architectural Architecture & Operational Modes

```
+-------------------------------------------------------------------------+
|                          Agent Orchestrators                            |
|       (LangChain BaseMemory, LlamaIndex BaseMemory, CrewAI, Custom)     |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                         Cognitive Substrate                             |
|  +--------------------+  +--------------------+  +--------------------+ |
|  |    HybridSearch    |  |   KnapsackPacker   |  |    DecayEngine     | |
|  |   (BM25 + Dense)   |  |   (0/1 DP Solver)  |  | (Ebbinghaus Decay) | |
|  +--------------------+  +--------------------+  +--------------------+ |
|  +--------------------+  +--------------------+  +--------------------+ |
|  |    Deduplicator    |  |   PruningEngine    |  |   Observability    | |
|  |  (Lexical+Cosine)  |  |  (Eviction Policy) |  | (Latency/Counters) | |
|  +--------------------+  +--------------------+  +--------------------+ |
+-------------------------------------------------------------------------+
                                     |
                +--------------------+--------------------+
                |                                         |
                v                                         v
   [Embedded Storage Mode]                   [Gateway Enterprise Mode]
    - SQLite Storage Engine                   - Express / Node.js API Gateway
    - Fernet AES-128-CBC + HMAC-SHA256        - PostgreSQL + pgvector (HNSW)
    - Zero external network dependencies      - Distributed Celery/Redis workers
    - Local or API-backed embeddings          - Kubernetes / Helm Deployments
```

### 1. Embedded Mode (Local Python Runtime)
- Designed for single-agent CLI tools, desktop applications, and embedded pipelines.
- Data is persisted to a local encrypted SQLite database (`sqlite3` + `cryptography.fernet`).
- Direct Python API imports: `from synapse_memory.core import SQLiteMemoryStore, MemoryManager, HybridSearch`.

### 2. Clustered Gateway Mode (Production Services)
- Designed for multi-tenant enterprise applications with high concurrent query volumes.
- Node.js/TypeScript REST API gateway (`server.ts`) with Prometheus metrics, rate limiting, and PII quarantine scrubbers.
- Backed by PostgreSQL with `pgvector` HNSW indexes and Docker Compose / Helm chart deployment manifests.

---

## Security & Encryption Model

- **Encryption Standard**: Fernet symmetric authenticated encryption (128-bit AES in CBC mode with PKCS7 padding and HMAC-SHA256 signature).
- **Protected Fields**: Raw memory `content` and serialized vector `embedding` blobs.
- **Key Injection**:
  - **Production Recommended**: Inject a 32-byte URL-safe base64 key into the environment:
    ```bash
    export SYNAPSE_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    ```
  - **Local Development Fallback**: If no environment key is provided, the store creates a local key file (`.synapse_key`) with restricted read/write permissions.
  - **Plaintext Mode**: Only active if encryption is explicitly disabled (`encryption_key=False`).

---

## Drop-In Framework Integrations

### LangChain (`BaseMemory`)
Drop-in integration compliant with `langchain_core.memory.BaseMemory`:

```python
from synapse_memory.integrations.langchain import SynapseLangChainMemory
from synapse_memory.core import SQLiteMemoryStore, SynapseEmbedder

# Initialize memory connected to your persistent store
memory = SynapseLangChainMemory(
    memory_key="chat_history",
    max_token_budget=1500,
    store=SQLiteMemoryStore("agent_memory.db"),
    embedder=SynapseEmbedder(provider="local")
)

# Integrates into any standard LangChain chain or agent:
# inputs = {"input": "What database parameters did we choose yesterday?"}
# loaded = memory.load_memory_variables(inputs)
# memory.save_context({"input": "..."}, {"output": "..."})
```

### LlamaIndex (`BaseMemory`)
Drop-in integration for LlamaIndex chat engines:

```python
from synapse_memory.integrations.llamaindex import SynapseLlamaIndexMemory
from synapse_memory.core import SQLiteMemoryStore, SynapseEmbedder

memory = SynapseLlamaIndexMemory(
    max_token_limit=2048,
    store=SQLiteMemoryStore("agent_memory.db"),
    embedder=SynapseEmbedder(provider="local")
)

# Use directly in LlamaIndex chat workflows
# response = chat_engine.chat("Query", memory=memory)
```

---

## Quick Start (Python SDK)

### Installation
```bash
pip install -e sdk/python
```

### Core Ingestion & Retrieval Example
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

# 2. Ingest memory with automatic semantic deduplication
mem_id, action, tokens = manager.ingest_with_deduplication(
    content="Production PostgreSQL connection timeout is configured to 3000ms.",
    category="infrastructure",
    confidence=0.95
)
print(f"Memory {mem_id}: Action={action} ({tokens} tokens)")

# 3. Hybrid search over encrypted records
searcher = HybridSearch(alpha=0.6, k=60)
results = searcher.search(
    query="Postgres timeout settings",
    corpus=store.get_all_memories(),
    query_vector=embedder.embed_query("Postgres timeout settings"),
    top_k=5
)

# 4. Knapsack context packing into LLM prompt budget
packer = KnapsackPacker(token_budget=1024)
packed_memories = packer.pack(results)
print(f"Packed {len(packed_memories)} memories into token envelope.")
```

---

## Testing & Quality Verification

All core algorithms, storage adapters, and integrations are thoroughly tested across standard suites:

```bash
# Run complete test suite
PYTHONPATH=sdk/python pytest sdk/python/tests

# Static security audit
bandit -r sdk/python -s B101,B104,B311

# Code linting
flake8 sdk/python --config=setup.cfg

# Type checking
mypy sdk/python --config-file setup.cfg
```

### Verified Test Suites:
- `test_decay.py`: Temporal Ebbinghaus decay formulas, half-life parameters, and reinforcement multipliers.
- `test_deduplicator.py`: Lexical Jaccard and cosine similarity deduplication thresholds (merge, reject, create).
- `test_encryption.py`: Fernet AES-128-CBC authenticated encryption at rest and key rotation.
- `test_knapsack.py`: 0/1 Dynamic programming optimal item packing and budget safety.
- `test_pruner.py`: Capacity and decay-driven eviction policies.
- `test_synapse.py`: End-to-end integration across memory managers, hybrid retrieval, and SQLite operations.
- `test_langchain.py`: LangChain `BaseMemory` contract, `load_memory_variables`, and `save_context`.
- `test_llamaindex.py`: LlamaIndex chat memory integration and token packing.

---

## Security Policy

Please refer to [SECURITY.md](SECURITY.md) for our coordinated vulnerability disclosure guidelines and designated security channels.

---

## License

Synapse Memory is open-source software licensed under the [MIT License](LICENSE).
