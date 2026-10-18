# Mem0 Head-to-Head Benchmark (`run_mem0_benchmark`)

Honest, measured-only head-to-head comparison of a Synapse-Memory installation
against [mem0](https://github.com/mem0/mem0) (v2.2.1) on **identical corpora,
identical queries and identical (deterministic) embeddings** — the only thing
that differs is the storage/retrieval engine.

## Why this is honest

- **No LLM API keys, no LLM calls.** The embedding provider is a shared
  deterministic (non-semantic, hash-based) provider registered with mem0's
  `EmbedderFactory` under the synthetic key `synapse_shared`. Both engines see
  the exact same vectors, so latency/quality differences are attributable to the
  engine, not to embedding quality.
- **No projections.** Results below are measured on this machine in one run. They
  are not tuned, not iterated until they look good, and not extrapolated beyond
  this machine.
- **Skips gracefully** when `mem0` is not installed, so the suite is
  dependency-optional.
- **Disclosed limitations** (as designed, not hidden): the deterministic embedder
  is lexical (hash of the text), not semantic; the corpus/needle design measures
  lexical overlap; SQLite is an exact O(N) scan — the production path is
  pgvector HNSW; mem0 ingest statistics include lazy collection/model warm-up;
  the token-budget knapsack fields are reported as `packing: none` for mem0.

## Run

```bat
cd sdk\python\benchmark
python bench_suite.py --mem0-only   # Mem0 side only (fast)
python bench_suite.py               # full suite (Synapse requires the SDK)
```

`benchmark_results.json` is written here with a `mem0_head_to_head` key holding
the complete measured record: corpus size, needle count, `top_k`, query count,
seed, per-engine versions and timestamps, and per-engine p50/p95/p99 ingest +
query (ms), recall@k, MRR, cross-tenant leak counts, token-budget packer stats.

## Reference — measured only (single run, seed 42)

| Metric | Synapse (SQLite) | mem0 (qdrant :memory:) |
|---|---|---|
| Ingest p50 | 115.2 ms (1,005 rows) | 12.4 ms (1,005 adds) |
| Ingest p95 | 438.7 ms | 38.4 ms |
| Ingest p99 | 1,196.1 ms | 237.5 ms |
| Query p50 | 360.4 ms | 34.8 ms |
| Recall@10 | 78 % | 78 % |
| MRR | 0.744 | 0.745 |
| Cross-tenant leaks | 0 | 0 |

Synapse details: `ingest_with_dedup` p50 **309.3** ms (200 dedupe samples),
knapsack packed 165.9 / saved 71.1 mean tokens (top-k 10). mem0 packs nothing —
it returns the raw ranked list (`packing: none`); the README's implementation is
engine code, not benchmark noise.

## Guardrails

- The suite is flake8 + mypy clean and reuses the shared benchmark embedder
  (`synapse_memory.core.embedder.SynapseEmbedder(provider="deterministic")`);
  no semantic model loads anywhere in the harness.
- Live PostgreSQL tables (`test_pgvector_integration.py`, 20/20 passing) are
  gated behind a running PG and skipped when absent — no daemon required to run
  the harness itself.
