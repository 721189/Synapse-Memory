# Capacity and performance: what is actually supported

This page states what the code does today. It deliberately contains **no
invented numbers** — measure with the harness below before promising an SLO.

## Complexity per operation

| Operation | Cost today | Notes |
|---|---|---|
| `ingest_with_deduplication` | O(n) in the tenant/category set: loads candidates, compares each | Bounded by `max_record_limit` (default **50** records/tenant) |
| Capacity pruning | O(n log n) per ingest: loads the tenant set (incl. superseded lineage), scores by decay, sorts | Runs on CREATE, REJECT **and** MERGE |
| `PGVectorMemoryStore.query_candidates` | SQL-side, `LIMIT`-bounded, tenant-scoped | Preferred path for large tenants |
| Retrieval (`hybrid_search`) | O(n) over the filtered candidate set (BM25 + cosine) | pgvector HNSW is used by `vector_search`, not by the in-process RRF path |
| SQLite backend | Exact O(n) scans by design | Single-node dev/test backend |

### Practical implications

- The shipped default (`SYNAPSE_MAX_RECORD_LIMIT=50`) keeps ingest cheap.
- Raising the cap to thousands makes ingest and pruning linearly more
  expensive **per write**, and on PostgreSQL it decrypts `content` for every
  candidate row (the store decrypts on read).
- If you need large per-tenant corpora, do not simply raise the cap: pre-filter
  candidates with `query_candidates` / `vector_search` (SQL `LIMIT`), and prune
  from SQL rather than in Python. That work is **not implemented yet** — treat
  very large tenants as unsupported until it is.

## Benchmark before promising anything

`sdk/python/benchmark/bench_suite.py` contains an ingest/query harness with
deterministic embeddings and a committed reference run
(`sdk/python/benchmark/benchmark_results.json`). It measures a single small
corpus — it is **not** evidence of production-scale throughput.

To produce a claim you can publish:

1. Fix the corpus size, record count, embedding provider and hardware.
2. Run ingest, dedup, prune and retrieval separately and report p50/p95/p99.
3. Run at your intended concurrency, not single-threaded only.
4. Commit the raw result JSON next to the claim and cite it.

## Known limits (state them, don't hide them)

- Deduplication compares against in-process candidates; there is no ANN index
  for the dedup path.
- Pruning materialises and sorts the tenant set in Python.
- The retrieval-evaluation corpus is a small synthetic set; it validates
  plumbing, not retrieval quality at scale.
- The deterministic embedding provider is a keyword/vector projection, not a
  semantic model — production refuses it unless explicitly opted in.
