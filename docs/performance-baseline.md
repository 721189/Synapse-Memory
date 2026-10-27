# Performance Baseline — Synapse Memory

> Benchmark discipline: this document does not define a universal performance
> SLA. Record source revision, hardware, dataset hash, corpus size, embedding
> provider/dimension, tenant count, concurrency, and topology with every measured
> result. Old runs below are historical evidence, not current-release claims.

## Reproducible retrieval strategy baseline

Use the offline, machine-readable benchmark for a repeatable strategy comparison:

    python -m pip install -e sdk/python
    python sdk/python/benchmark/retrieval_baselines.py --output sdk/python/benchmark/retrieval-baseline-results.json --repeats 5 --warmups 1 --top-k 5 --candidate-k 10 --token-budget 90

The harness stores dataset SHA-256 and source revision alongside raw latency
samples and per-query relevance judgments. Its 23-document / 14-query synthetic
fixture compares BM25 lexical-only, exact dense cosine, hybrid RRF, and four
context-selection strategies. It also records embedding/encrypted-insert cost,
tenant-filter leakage probes on SQLite, and a verified deletion check.

Interpretation constraints:

- The deterministic 128-dimensional hash embedding is not semantic; this fixture
  validates ranking and packing behavior, not natural-language understanding.
- Ranker latency is measured over a preloaded in-memory corpus and excludes
  network, process startup, database reads, and query-embedding time. These are
  measured separately where relevant.
- SQLite is an exact local scan. It is not a proxy for pgvector HNSW performance.
- Ground-truth relevance labels are hand-authored and the fixture is small. Use
  representative production queries and a PostgreSQL target before drawing
  product-level conclusions.
- CI executes a one-repeat smoke run for output correctness and tenant/deletion
  invariants; CI timings are not performance results.

Keep each resulting JSON file as an artifact for review. Do not copy benchmark
numbers into product pages until multiple runs and the full configuration are
reviewed.

## Historical operational stress-test observations

Measured locally (SQLite backend, test_load_resilience.py, 17 offline tests) —
not a production SLA and not necessarily a measurement of the current revision.
Topology for every observation below: single engine process, local SQLite file,
default SynapseEmbedder, no Redis, no network hop.

| Scenario | Result (historical local run) |
|---|---|
| 8 threads × 25 parallel inserts | 200/200 rows, zero loss |
| 10 threads × atomic access_count +1 | exactly +100, no lost update |
| Concurrent readers during writes | no torn/undecryptable rows |
| 1000-row corpus top-10 retrieval | sorted, planted doc found, exact lexical hit |
| 400-row 2-tenant × 2-category matrix | quality held, cross-tenant isolation held |
| HTTP read burst 50× | all 200 |
| HTTP write burst 30× | all 201 |
| 100-item /api/memories/bulk | ingested == 100, auto-prune bound ≤ 50/tenant |
| Full store outage | ingest 500s fail-closed, _ingest_errors counted, /liveness OK, instant recovery |
| DB interruption (patched connection) | insert/get/vector_search raise OperationalError — no fake success, no partial rows |

## Latency reporting

The /metrics endpoint exposes live per-endpoint p50/p95
(synapse_request_latency_seconds path/quantile plus sum/count, last-500
samples). Read measurements from the target deployment before setting SLOs.

## How to set production targets

1. Deploy the supported topology (split engine/gateway, external pgvector,
   Redis for multi-replica) on production-grade hardware.
2. Seed a representative corpus (embedding model and dimension, tenant count,
   category mix, and memory-length distribution).
3. Drive load at expected concurrency (reads, writes, bulk, mixed).
4. Read p50/p95 per endpoint from /metrics over a sustained window.
5. Set SLOs from measured numbers with headroom. Record hardware, dataset hash,
   concurrency, embedding model, and topology alongside every target.

## Do NOT do this

- Do not promise a universal p95/p99 before defining hardware, dataset,
  concurrency, and topology.
- Do not present projections from bench_suite.py or root benchmark_results.json
  as measured results; they are not production performance evidence.
- Do not compare SQLite-local numbers with pgvector-cluster numbers.
- Do not quote the legacy Mem0 result in sdk/python/benchmark/README.md as a
  current v1.3.1 comparison; it records a historical 1.2.0 run.

## Recommended follow-ups

- Add a nightly benchmark job against a specified pgvector service and pinned
  hardware; report recall/latency across corpus sizes and tenant counts.
- Evaluate query quality using representative user-derived judgments and a
  real semantic embedding provider before making semantic retrieval claims.
- Add LLM answer-quality evaluation separately from retrieval-only scores.
- Build a Grafana dashboard over /metrics (requests, p50/p95, auth/429, pool,
  packed tokens, synapse_rate_limit_backend).
