# Synapse Memory Python SDK

Core Python SDK and algorithms for the Synapse Cognitive Memory substrate.

## Features

- **Encrypted SQLite Storage**: Fernet AES-128-CBC with HMAC-SHA256 authenticated encryption at rest.
- **Dynamic Programming Knapsack Packing**: Exact 0/1 Knapsack optimizer for packing high-relevance memories into strict token envelopes.
- **Ebbinghaus Decay Engine**: Continuous temporal decay with positive/negative reinforcement factors.
- **Semantic Deduplication**: Dual lexical Jaccard and dense cosine similarity detection to prevent node fragmentation.
- **Hybrid Retrieval**: BM25 sparse lexical ranking fused with dense vector embeddings via Reciprocal Rank Fusion (RRF).
- **Framework Integrations**: True drop-in adapters for LangChain (`BaseMemory`) and LlamaIndex (`BaseMemory`).

## Installation

```bash
pip install -e .
```

## Testing

```bash
python3 -m unittest discover -s tests
```
