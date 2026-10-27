# Performance Baseline — Synapse Memory

> This document does not define a universal performance SLA. Record source
> revision, hardware, dataset hash, corpus size, embedding provider/dimension,
> tenant count, concurrency, and topology with every measured result.

## Reproducible retrieval and packing fixtures

The dedicated CI job runs both fixtures five times after one warm-up and uploads
the raw JSON artifacts:

| Fixture | Size | Judgments | Purpose |
|---|---:|---:|---|
| retrieval_dataset.json | 25 memories / 14 queries | Mostly one relevant memory per query | Controlled baseline |
| packing_stress_dataset.json | 96 memories / 24 queries | Two relevant memories per query, three tenants | Paraphrase-heavy multi-fact selection stress |

Each artifact records its dataset hash, source revision, configuration, raw
latency samples, per-query retrieval metrics, token budget and packing results.
See sdk/python/benchmark/README.md for the exact invocation.

## Separating retrieval and packing

The benchmark reports candidate recall before packing, retention of relevant
items among those candidates, and retained raw relevance-score sum. This
separation matters: a memory absent from the candidate list cannot be recovered
by the packer, and optimizing summed ranking scores is not identical to
maximizing hand-judged fact recall.

The current DP packer had an avoidable precision issue: converting
relevance_score to int(score * 1000) rounded away small RRF score differences.
The implementation now preserves finite float scores and neutralizes non-finite
values. Regression tests cover a concrete counterexample and scale invariance.
Follow-up measurements should determine whether any remaining gap is explained
by the objective mismatch, candidate ordering, token costs, or the DP selection
itself before changing the production objective.

Interpretation constraints:

- The deterministic hash embeddings are non-semantic. The paraphrase-heavy
  fixture is a stress test of wording mismatch, not a semantic-quality benchmark.
- SQLite results are local exact-scan measurements, not pgvector HNSW performance.
- Relevance labels are hand-authored and the corpus is synthetic. Use representative
  application queries and independent judgments before drawing product conclusions.
- CI timings verify implementation and provide reproducible comparisons; they are
  not SLAs and should not be advertised as production latency.

## Benchmark-only rank-discount objective

In addition to raw-score DP, the benchmark now evaluates a rank-discount DP
ablation with utility `1 / log2(rank + 1)`. It uses the same candidate set and
token budget but values higher-ranked candidates more directly. This lets us
compare raw score utility retained versus human-judged fact retention without
changing production behavior.

Treat this as an experiment, not a recommendation: the rank-discount objective
still needs evaluation with real semantic embeddings and independent relevance
labels before it can be considered for the production packer.

## Historical operational stress-test observations

Measured locally (SQLite backend, test_load_resilience.py, 17 offline tests) —
not a production SLA and not necessarily a measurement of the current revision.
Topology: single engine process, local SQLite file, default SynapseEmbedder, no
Redis, no network hop.

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

1. Deploy the supported topology (split engine/gateway, external pgvector, Redis
   for multi-replica) on production-grade hardware.
2. Seed a representative corpus, embedding model and dimension, tenant count,
   category mix, and memory-length distribution.
3. Drive load at expected concurrency (reads, writes, bulk, mixed).
4. Read p50/p95 per endpoint from /metrics over a sustained window.
5. Set SLOs from measured numbers with headroom. Record hardware, dataset hash,
   concurrency, embedding model, and topology alongside every target.

## Do NOT do this

- Do not promise a universal p95/p99 before defining hardware, dataset, concurrency,
  and topology.
- Do not present bench_suite.py projections or root benchmark_results.json as
  measured production results.
- Do not compare SQLite-local numbers with pgvector-cluster numbers.
- Do not claim score-sum-optimized packing guarantees maximum judged-fact recall.

## Recommended follow-ups

- Run the larger fixture with a real semantic embedding provider and independent
  relevance labels, then compare packers using the same candidate lists.
- Add a pgvector/HNSW benchmark on pinned infrastructure with identical inputs.
- Measure LLM answer quality separately from retrieval/packing metrics.
- Build a Grafana dashboard over /metrics (requests, p50/p95, auth/429, pool,
  packed tokens, synapse_rate_limit_backend).
