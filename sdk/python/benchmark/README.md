# Reproducible Retrieval Benchmarks

## Current recommended baseline: strategy ablation

Run from the repository root after installing the project dependencies:

    python -m pip install -e sdk/python
    python sdk/python/benchmark/retrieval_baselines.py --output sdk/python/benchmark/retrieval-baseline-results.json --repeats 5 --warmups 1 --top-k 5 --candidate-k 10 --token-budget 90

The runner writes a complete JSON record containing the dataset SHA-256, source
revision when available, Python/platform/package version, fixed embedding
configuration, input parameters, individual latency samples, per-query ranked
IDs, relevance metrics, packing outcomes, write-cost samples, tenant-isolation
probes, and a real delete check. Preserve the resulting JSON as an experiment
artifact when reporting measurements.

### What is compared

| Dimension | Variants | Measurement |
|---|---|---|
| Retrieval ranking | BM25 lexical-only, exact cosine dense-only, Synapse hybrid RRF | recall@k, precision@k, MRR, p50/p95/p99 ranker latency |
| Context selection | unbudgeted top-k, rank-order fit, greedy relevance/token ratio, 0/1 knapsack DP | relevant-fact recall after selection, tokens used, budget utilization, latency |
| Write cost | deterministic embedding and encrypted SQLite insert | per-item embedding, insert, and combined latency samples |
| Isolation and deletion | tenant-scoped SQLite list/vector/lexical operations and sentinel deletion | cross-tenant rows and whether deletion is verified |

Example smoke run:

    python sdk/python/benchmark/retrieval_baselines.py --output /tmp/synapse-baseline.json --repeats 1 --warmups 0

### Dataset and fairness rules

- Dataset: retrieval_dataset.json, a small fixed synthetic corpus with 23
  memories, 14 judged queries, and three tenant IDs. The judgments are included
  in the dataset so reviewers can challenge them.
- All methods receive the same tenant-filtered corpus and query text. The
  document/query vectors are generated with the same fixed 128-dimensional
  deterministic hash provider.
- The deterministic provider is NOT semantic. This experiment validates
  ranking/packing mechanics over a controlled lexical-like fixture; it is not
  evidence of semantic retrieval quality.
- Ranker timings use a preloaded SQLite corpus and exclude network, process
  startup, database-read, and query-embedding time. Query embedding, encrypted
  insert, and packing costs are measured separately.
- SQLite uses exact scans. This benchmark says nothing about pgvector HNSW
  latency or recall. Compare production topology separately, with the same
  embedding model, dataset, candidate policy, and hardware.
- Run at least five measured repetitions after warm-up for informal local
  comparisons. For publishable comparisons, repeat on named hardware and retain
  every JSON output. Do not hand-pick the fastest run.
- Tenant-isolation probes check actual tenant filters exposed by the SQLite
  store. They are regression guards, not penetration tests or proof of
  PostgreSQL RLS behavior.

The CI smoke test runs one repetition to validate the output schema and safety
invariants, not to create performance claims.

---

## Historical Mem0 head-to-head (run_mem0_benchmark)

The result and numbers in this legacy section are a single historical run from
2026-10-17 using synapse-memory-engine==1.2.0, not a measurement of the current
v1.3.1 source. Do not use them as a current product comparison; rerun the suite
on the desired revision and environment before quoting results.

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

### Reference — measured only (historical single run, seed 42)

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
