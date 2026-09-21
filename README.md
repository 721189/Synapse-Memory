# Synapse Memory

**Synapse Memory** is a sovereign, secure, and performant cognitive memory engine designed to provide AI agents with persistent, long-term, and encrypted memory storage.

[![CI Status](https://github.com/example/synapse_memory/actions/workflows/ci.yml/badge.svg)](https://github.com/example/synapse_memory/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

Synapse Memory bridges the gap between ephemeral LLM interactions and persistent cognitive context. It provides a structured, secure, and efficient backend for storing and retrieving vectorized memories, utilizing industry-standard encryption and optimized SQL indexing.

## Features

*   **Security-First**: AES-256 encryption at rest (Fernet) ensures sensitive data is never persisted in plain text.
*   **Highly Performant**: Optimized category-based SQL indexing and LRU-cached embedding operations.
*   **Observability**: Integrated instrumentation for monitoring cost, latency, and cache hits.
*   **Extensible**: Modular architecture for embedding providers (Gemini, OpenAI, Local).
*   **Production-Ready**: CI/CD pipeline integrated with linting and type-checking.

## Quick Start

### Installation

```bash
pip install synapse-memory
```

### Basic Usage

```python
from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedding_provider import LocalEmbeddingProvider, EmbeddingManager
from synapse_memory.core.memory_manager import MemoryManager

# 1. Initialize with persistence
store = SQLiteMemoryStore(db_path="my_agent.db")
embedder = EmbeddingManager(LocalEmbeddingProvider(dim=128))
manager = MemoryManager(store=store, embedder=embedder)

# 2. Ingest
manager.ingest_with_deduplication("Agent context memory node", category="knowledge")

# 3. Retrieve
all_memories = store.get_all_memories()
```

## Documentation

For full API reference and advanced configuration, see the [generated documentation](docs/).

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on development, testing, and submitting pull requests.

## License

This project is licensed under the MIT License.
