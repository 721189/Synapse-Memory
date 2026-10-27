# Reproducible Retrieval and Packing Benchmarks

## What CI measures

The dedicated CI job runs both fixtures five times after one warm-up and uploads
the full raw JSON output as a workflow artifact. The fixture is synthetic and
the deterministic hash embedder is non-semantic; these runs are useful for
ranking and selection regression checks, not a semantic-retrieval claim.

| Fixture | Corpus | Queries | Evaluation design |
|---|---:|---:|---|
| retrieval_dataset.json | 25 memories | 14 queries | Small controlled baseline, mostly single-fact labels |
| packing_stress_dataset.json | 96 memories | 24 queries | Paraphrase-heavy queries; two relevant facts per query; three isolated tenants |

Run from the repository root after installing the SDK:

    python -m pip install -e sdk/python
    python sdk/python/benchmark/retrieval_baselines.py --dataset sdk/python/benchmark/retrieval_dataset.json --output retrieval-baseline-results.json --repeats 5 --warmups 1 --top-k 5 --candidate-k 10 --token-budget 90
    python sdk/python/benchmark/retrieval_baselines.py --dataset sdk/python/benchmark/packing_stress_dataset.json --output packing-stress-results.json --repeats 5 --warmups 1 --top-k 5 --candidate-k 10 --token-budget 90

The JSON output includes dataset SHA-256, source revision, environment and
configuration, raw latency samples, per-query results, token usage, tenant
isolation probes, and a verified deletion check.

## Real semantic embedding evaluation

The dedicated **Semantic Retrieval + Packing Evaluation** CI job compares the
deterministic baseline with a CPU neural embedding model on the same 96-memory,
24-query, three-tenant fixture. The model is
`sentence-transformers/all-MiniLM-L6-v2`, pinned to Hugging Face revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The workflow pins the inference
runtime in `requirements-semantic.txt`, caches model files by revision, records
the dataset SHA-256 and installed runtime versions, and uploads both raw JSON
results as an artifact. If the neural model cannot load, the run fails rather
than falling back to deterministic embeddings.

To reproduce the neural run from the repository root:

```bash
python -m pip install -e sdk/python
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu
python -m pip install -r sdk/python/benchmark/requirements-semantic.txt
python sdk/python/benchmark/retrieval_baselines.py \
  --dataset sdk/python/benchmark/semantic_packing_eval_dataset.json \
  --output semantic-eval-neural.json \
  --embedding-provider sentence-transformers \
  --model-name sentence-transformers/all-MiniLM-L6-v2 \
  --model-revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --repeats 5 --warmups 1 --top-k 5 --candidate-k 10 --token-budget 90
```

This is a **curated semantic evaluation**, not a standardized benchmark: its
96 documents, 24 paraphrased queries, and two relevant IDs per query are
hand-authored. They are not live user traffic and have not been independently
judged. The deterministic and neural runs use the exact same dataset SHA, so
the comparison isolates the effect of embedding choice on this fixture; it does
not prove general production quality or better LLM answers. Do not switch the
production packer objective based on this small set alone.

## External validation with reviewed LongMemEval evidence sessions

The dedicated semantic CI job also runs `longmemeval_external_eval.py` against
the corrected ReMe Cleaned-S release of LongMemEval. The adapter verifies the
downloaded JSON's SHA-256 before evaluating it, uses the published corrected
`answer_session_ids` evidence labels, and excludes every history session later
than the question timestamp. The subset is deterministic and stratified across
the six published question types (up to 20 per type); no dataset file is
committed to this repository.

The external evaluator compares BM25, pinned neural dense retrieval, and RRF
hybrid ranking at Recall@5, Precision@5, MRR@5, and nDCG@5. It measures retrieval
of evidence sessions, not answer-generation accuracy. LongMemEval is a general
long-term conversational-memory benchmark, not a developer-only benchmark.

Run it locally after installing the pinned semantic dependencies:

```bash
python -m pip install -e sdk/python
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu
python -m pip install -r sdk/python/benchmark/requirements-semantic.txt
python sdk/python/benchmark/longmemeval_external_eval.py \
  --output longmemeval-external-results.json \
  --max-per-type 20 --top-k 5
```

The dataset is fetched from
[the corrected ReMe Cleaned-S dataset card](https://huggingface.co/datasets/agentscope-ai/ReMe_longmemeval_clean_s_v2)
and must match SHA-256
`f8b308cc2d184d029dd2226e0c0a88eeac298e6318706d588e015beb1b83fc7d`.
The dataset card documents corrections to answer and evidence-session labels,
including future-evidence leakage. Do not redistribute the downloaded data
without checking the dataset's applicable license and terms.

## Developer-specific relevance review (human step required)

The same CI job creates a blinded packet from the top-ranked developer-memory
candidates across deterministic and neural runs. Reviewers score candidate
evidence from 0 (irrelevant), 1 (related but insufficient), to 2 (directly
useful evidence). The packet and its mapping key are uploaded as separate
artifacts; send only the packet to two reviewers and keep the key private until
both reviews are complete. The packet is explicitly marked
`awaiting_independent_human_review`: CI cannot manufacture independent labels.
Resolve disagreements and then calculate nDCG/MRR from adjudicated labels
before using this developer-specific evaluation to support product claims.

## How to interpret packing results

The packer optimizes the sum of each candidate's numeric relevance_score under
the token budget. The benchmark's judged relevant memory IDs are a separate
quality signal; they are not guaranteed to be proportional to those scores.
The benchmark therefore records three distinct stages:

- **Candidate recall:** what proportion of the query's judged relevant memories
  reached the hybrid top-candidate list before packing.
- **Retained candidate-relevant recall:** what proportion of those relevant
  candidate memories survived packing. Queries with no relevant candidate are
  counted separately rather than blaming the packer for an unretrieved fact.
- **Score utility retained:** how much of the raw hybrid score sum the packing
  strategy retained, which helps inspect the optimizer's stated objective.

Also compare overall relevant-fact recall after packing, mean tokens used,
budget utilization, over-budget count, and packing latency. A high score sum
does not automatically mean high judged-fact recall; conversely, high judged-fact
recall on a hand-authored fixture is not proof of better LLM answers.

## Packing objective ablation

The harness now compares a benchmark-only `rank_discount_dp` method alongside
raw-score DP. It assigns candidate rank *r* utility `1 / log2(r + 1)` and runs
the same DP under the same token budget. This tests whether rank position is a
better selection signal than the raw RRF score sum on these fixtures. Results
are mapped back to the original retrieval records before evaluating score
utility and judged-fact recall.

`rank_discount_dp` is an experiment only; production continues using the
existing raw-score DP. Do not promote the rank-discount objective until it is
tested with a real semantic embedder, representative user queries, and
independent relevance judgments.

## Knapsack precision finding

The old dynamic-programming implementation converted scores with
int(relevance_score * 1000). RRF differences are often below 0.001, so this
collapsed distinct values into integer ties. For example, a 10-token item with
score 0.0060 could win a tie against two 5-token items with score 0.00349 each,
even though the pair has the higher total score (0.00698 vs 0.0060). The DP now
preserves finite floating-point relevance values and treats non-finite scores
as zero. Regression tests assert the optimal choice remains stable when scores
are multiplied by a positive constant and that NaN/Infinity cannot poison DP.

This correction fixes the quantization bug; it does not change the objective
from score-sum maximization to ground-truth recall maximization. The larger
stress fixture is intended to reveal whether score maximization and judged
relevant-fact retention diverge when queries require multiple facts.

## Methodology and limitations

- The deterministic 128-dimensional hash embedder is not semantic. The
  paraphrase-heavy fixture stresses wording mismatch, but does not replace a
  real neural embedding model or user-derived relevance judgments.
- Every ranking method receives the same tenant-filtered corpus and query.
  BM25, exact cosine, and hybrid RRF rankings are compared over the same
  deterministic vectors. SQLite is a local exact scan, not a proxy for pgvector
  HNSW latency or recall.
- Ranker timings use a preloaded corpus and exclude process startup, database
  reads, query embedding, network, and LLM answer generation. Write and packing
  timings are reported separately.
- The dataset labels are hand-authored. Review the JSON per-query results, not
  only aggregate metrics; keep all repetitions and do not select only the best
  run.
- The CI run validates reproducible mechanics and stores measurements. Its
  timings are machine-specific and must not be published as a product SLA.

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
