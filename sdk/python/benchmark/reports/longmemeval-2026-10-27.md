# Synapse Memory retrieval evaluation report — 2026-10-27

## Scope

This report records one reproducible CI run on PR #25 at commit
`592b171133de69d77d9a1d2f921d153da41598b1`. It evaluates retrieval of
evidence sessions from a corrected LongMemEval-S release. It does not measure
LLM answer correctness, user satisfaction, or developer-specific relevance.

Run: https://github.com/721189/Synapse-Memory/actions/runs/40542907118  
PR: https://github.com/721189/Synapse-Memory/pull/25  
Machine-readable artifact: [semantic-packing-evaluation artifact](https://github.com/721189/Synapse-Memory/actions/runs/40542907118/artifacts/12752493391)

## Configuration and provenance

- Dataset: `agentscope-ai/ReMe_longmemeval_clean_s_v2`, file
  `longmemeval_s_reme_cleaned.json`.
- Dataset snapshot: `f8bd08fd19ef175c99c7065c322191e16c69f0ea`.
- Observed dataset SHA-256:
  `57ed3225673015880c42eaf764406e656d9ac3fc4821337e3ec3c28ac8259823`.
- The dataset card reports a different SHA-256:
  `f8b308cc2d184d029dd2226e0c0a88eeac298e6318706d588e015beb1b83fc7d`.
  The mismatch is explicitly detected and documented by the evaluator; it must
  be resolved with the dataset publisher before treating the provenance as ideal.
- Embedding model: `sentence-transformers/all-MiniLM-L6-v2`, revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
- Questions: 500; positive-evidence questions: 441; no-evidence questions: 59.
- Session candidates: 22,392; future sessions excluded before query time: 1,475.
- Retrieval: BM25, dense cosine, and reciprocal-rank-fusion (RRF) hybrid.
- Configuration: top-k 5, candidate-k 10, context token budget 512.
- Label source: third-party corrected evidence-session labels, not labels authored
  independently for Synapse.

## Retrieval results

| Method | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|
| BM25 lexical | 0.909 | 0.946 | 0.901 | 0.891 |
| Dense neural | 0.856 | 0.928 | 0.830 | 0.828 |
| Hybrid RRF | 0.925 | 0.962 | 0.887 | 0.889 |

## Interpretation

Hybrid RRF has the best recall in this run. BM25 has the best MRR and nDCG@10,
so there is no single method that wins every ranking objective. The result is
specific to this dataset, model, implementation, and configuration. It is not
proof of superiority to all memory systems and says nothing directly about
generated-answer accuracy.

Context packing is measured separately in the linked raw artifact. Production
packing behavior should not be changed solely on this evaluation without a
separate reviewed comparison.

## Developer-specific validation status

The separate blind packet contains a small sample of developer-memory queries.
At the time of this report, it is marked
`awaiting_independent_human_review`. Two independent reviewers must grade
candidate evidence 0/1/2, disagreements must be adjudicated, and the scorer
`score_developer_relevance_reviews.py` can then calculate pool-limited metrics.

The developer packet artifact is temporary and expires according to GitHub's
artifact-retention policy:
https://github.com/721189/Synapse-Memory/actions/runs/40542907118/artifacts/12751973430

Do not claim developer-specific independent validation until those judgments
exist. Do not describe recall within the reviewed candidate pool as full-corpus
recall. Do not publish the private mapping key or raw conversation dataset.

## Reproduction

See [benchmark README](../README.md) for the pinned environment, dataset
verification, local commands, and limitations. The exact benchmark scripts,
tests, workflow configuration, and model revision are version-controlled in
this repository.
