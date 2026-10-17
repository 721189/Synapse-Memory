# Performance Baseline — Synapse Memory 1.2.0

Measured locally (SQLite backend, `test_load_resilience.py`, 17 offline
tests) — NOT a production SLA. Re-measure on your hardware, dataset, and
topology before publishing targets. Topology for every number below:
single engine process, local SQLite file, default `SynapseEmbedder`, no
Redis, no network hop.

## What was measured

| Scenario | Result (local) |
|---|---|
| 8 threads × 25 parallel inserts | 200/200 rows, zero loss |
| 10 threads × atomic `access_count +1` | exactly +100, no lost update |
| Concurrent readers during writes | no torn/undecryptable rows |
| 1000-row corpus top-10 retrieval | sorted, planted doc found, exact lexical hit |
| 400-row 2-tenant × 2-category matrix | quality held, cross-tenant isolation held |
| HTTP read burst 50× | all 200 |
| HTTP write burst 30× | all 201 |
| 100-item `/api/memories/bulk` | `ingested == 100`, auto-prune bound ≤ 50/tenant |
| Full store outage | ingest 500s fail-closed, `_ingest_errors` counted, `/liveness` OK, instant recovery |
| DB interruption (patched connection) | `insert`/`get`/`vector_search` raise `OperationalError` — no fake success, no partial rows |

## Latency reporting

`/metrics` exposes live per-endpoint p50/p95
(`synapse_request_latency_seconds{path,quantile}` + sum/count, last-500
samples). There are no hardcoded latency figures anywhere in the codebase —
read the numbers off your own deployment's `/metrics` before setting SLOs.

## How to set production targets

1. Deploy the supported topology (split engine/gateway, external pgvector,
   Redis for multi-replica) on production-grade hardware.
2. Seed a representative corpus (your embedding dimension, tenant count,
   category mix) — not the 384-d test vectors.
3. Drive load at your expected concurrency (reads, writes, bulk, mixed).
4. Read p50/p95 per endpoint from `/metrics` over a sustained window.
5. Set SLOs from THOSE numbers with headroom — e.g. p95 + 50% as the alert
   threshold. Record hardware, dataset size, concurrency, and topology
   alongside every target.

## Do NOT do this

- Do not promise a universal p95/p99 — the plan explicitly forbids it
  before defining hardware, dataset, concurrency, and topology.
- Do not present `bench_suite.py` projections as measured results;
  `test_retrieval_eval.py` is the measured-only suite.
- Do not compare SQLite-local numbers with pgvector-cluster numbers.

## Recommended follow-ups

- Nightly load job in CI against the pgvector service (concurrency +
  large-batch + restart scenarios already written — they need a PG target).
- Grafana dashboard over `/metrics` (requests, p50/p95, auth/429, pool,
  packed tokens, `synapse_rate_limit_backend`).
- Alert thresholds derived per the 5-step process above.
