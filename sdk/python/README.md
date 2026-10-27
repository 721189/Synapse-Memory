# Synapse Memory Python SDK

Python distribution for the Synapse Memory engine. The distribution name is `synapse-memory-engine`; the import package is `synapse_memory`.

## Features

- **Encrypted SQLite Storage**: Fernet AES-128-CBC with HMAC-SHA256 authenticated encryption at rest.
- **Dynamic Programming Knapsack Packing**: Exact 0/1 Knapsack optimizer for packing high-relevance memories into strict token envelopes.
- **Ebbinghaus Decay Engine**: Continuous temporal decay with positive/negative reinforcement factors.
- **Semantic Deduplication**: Dual lexical Jaccard and dense cosine similarity detection to prevent node fragmentation.
- **Hybrid Retrieval**: BM25 sparse lexical ranking fused with dense vector embeddings via Reciprocal Rank Fusion (RRF).
- **Framework Integrations**: True drop-in adapters for LangChain (`BaseMemory`) and LlamaIndex (`BaseMemory`).

## Installation

```bash
pip install synapse-memory-engine          # from PyPI
# or, from a checkout of this repository:
pip install -e .
```

## REST client quick start

Use `SynapseClient` when connecting an application to a running Synapse API:

```python
from synapse_memory import SynapseClient, SynapseError

with SynapseClient(
    "http://127.0.0.1:8008",
    api_key="syn_live_your_key",
    tenant_id="your-tenant",
) as client:
    created = client.create_memory("Customer selected the annual plan.", category="fact")
    result = client.retrieve("Which plan did the customer select?")
    print(created.id, result.fused_context)
```

The client automatically retries idempotent `GET`/`HEAD`/`OPTIONS` requests and honours `Retry-After`. It does not automatically replay `POST` requests (including `/query`, which updates access metadata) after an ambiguous failure; callers should reconcile the operation before manually retrying, or provide their own idempotency mechanism.

## Testing

From the repository root:

```bash
pip install -e "sdk/python[pgvector,langchain,llamaindex,dev]"
PYTHONPATH=sdk/python pytest sdk/python/tests -v
```

Database-gated tests require a disposable PostgreSQL/pgvector database and explicitly documented environment flags. Passing the offline suite alone is not production verification.

**Licence:** proprietary. Installation does not grant unrestricted usage or deployment rights; obtain written permission under [`../../LICENSE`](../../LICENSE) before using this package outside the permissions granted by the copyright holder.
