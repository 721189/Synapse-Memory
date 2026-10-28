# Synapse Memory Python SDK

Python distribution for the Synapse Memory engine. The distribution name is `synapse-memory-engine`; the import package is `synapse_memory`.

## Features

- **Encrypted SQLite Storage**: Fernet AES-128-CBC with HMAC-SHA256 authenticated encryption at rest.
- **Dynamic Programming Knapsack Packing**: Exact 0/1 Knapsack optimizer for packing high-relevance memories into strict token envelopes.
- **Ebbinghaus Decay Engine**: Continuous temporal decay with positive/negative reinforcement factors.
- **Semantic Deduplication**: Dual lexical Jaccard and dense cosine similarity detection to prevent node fragmentation.
- **Hybrid Retrieval**: BM25 sparse lexical ranking fused with dense vector embeddings via Reciprocal Rank Fusion (RRF).
- **Framework Integrations**: True drop-in adapters for LangChain (`BaseMemory`) and LlamaIndex (`BaseMemory`).
- **Agent reliability runtime**: Opt-in run budgets, repeated failed-call blocking, versioned event serialization, and persistent tenant-scoped run traces.
- **Opt-in agent run guard**: Bounded steps/tool calls, cooperative deadlines, repeated-failure blocking, and privacy-minimized in-memory events.

## Installation

```bash
pip install synapse-memory-engine          # from PyPI
# or, from a checkout of this repository:
pip install -e .
```

## REST client quick start

Use `SynapseClient` when connecting an application to a running Synapse API:

```python
from synapse_memory import SynapseClient, SynapseError

with SynapseClient(
    "http://127.0.0.1:8008",
    api_key="syn_live_your_key",
    tenant_id="your-tenant",
) as client:
    created = client.create_memory("Customer selected the annual plan.", category="fact")
    result = client.retrieve("Which plan did the customer select?")
    print(created.id, result.fused_context)
```

The client automatically retries idempotent `GET`/`HEAD`/`OPTIONS` requests and honours `Retry-After`. It does not automatically replay `POST` requests (including `/query`, which updates access metadata) after an ambiguous failure; callers should reconcile the operation before manually retrying, or provide their own idempotency mechanism.

## Opt-in Python run guard

The runtime only guards tool calls explicitly routed through `SynapseRun`; it does not intercept arbitrary framework calls. Events stay in memory until the application exports or persists them. Event records contain tool names and keyed argument fingerprints, not raw arguments, prompts, tool results, or exception messages.

```python
from synapse_memory import RunPolicy, SynapseRun

policy = RunPolicy(
    max_steps=12,
    max_tool_calls=8,
    timeout_seconds=30,
    max_repeated_failures=2,
)

with SynapseRun(policy=policy) as run:
    customer = run.call_tool(
        "lookup_customer",
        {"customer_id": "cust_123"},
        lookup_customer,  # callable accepts arguments as keyword arguments
    )
    run.record_step()  # account for non-tool work
    print(customer)

# Inspect metadata-only events after the context exits.
for event in run.events:
    print(event.event_type.value, event.reason, event.tool_fingerprint)
```

Limits are checked before admitting each step/tool call. The deadline is cooperative: a synchronous tool already in progress cannot be forcibly interrupted. A run guard is not a sandbox, does not authorize tools, does not intercept unwrapped calls, or enforce provider token/cost budgets. Repeated-call blocking applies after the configured number of consecutive failures for the same tool and equivalent JSON arguments; a successful call resets that failure count. No automatic retries are performed.

## Persistent run traces and retrieval provenance

Trace persistence is an explicit opt-in operation. The event contract is versioned (schema_version=1) and records only run lifecycle metadata, tool name/status, a per-run keyed argument fingerprint, typed failure reasons, and retrieval evidence. It does not record prompts, raw tool arguments, tool results, memory content, or the retrieval query. Retrieval evidence must be supplied by the application from its own retrieval/packing pipeline; Synapse does not silently instrument arbitrary framework calls or automatically attach every /query response to a run.

To persist a completed SynapseRun, use a key with agent-runs:read and agent-runs:write scopes, plus agent-runs:delete only when deletion is needed:

~~~python
from synapse_memory import RunPolicy, SynapseClient, SynapseRun

with SynapseRun(policy=RunPolicy(max_steps=12, max_tool_calls=8)) as run:
    # The app can query Synapse or another retriever here.
    candidates = [
        {"id": "memory_01", "relevance_score": 0.91, "token_cost": 18},
        {"id": "memory_02", "relevance_score": 0.43, "token_cost": 16},
    ]
    packed_ids = ["memory_01"]
    run.record_memory_retrieval(candidates, packed_memory_ids=packed_ids)
    answer = run.call_tool("lookup_customer", {"customer_id": "cust_123"}, lookup_customer)

with SynapseClient(
    "https://your-synapse-api",
    api_key="syn_live_your_key",
    tenant_id="your-tenant",
) as client:
    receipt = client.upload_agent_run(run)
    print(receipt["events_inserted"], receipt["events_deduplicated"])
~~~

The client uploads the run's ordered events after the run ends. Stable event IDs make retrying a partially completed upload idempotent. Event records are tenant-bound by the authenticated API key; a caller-supplied tenant field in an event is rejected. Available endpoints are documented in the API contract (../../docs/api-reference.md). Each tenant can list and delete only its own trace records; event pages are bounded and ordered by sequence number.

This first persistence layer is not yet a live streaming sink, a full observability dashboard, an outcome verifier, or a retention scheduler. Deployments must apply schema migration v6 before starting a PostgreSQL least-privilege runtime role. Use the API's deletion endpoint or a separately configured retention job to remove stored runs.

## Testing

From the repository root:

```bash
pip install -e "sdk/python[pgvector,langchain,llamaindex,dev]"
PYTHONPATH=sdk/python pytest sdk/python/tests -v
```

Database-gated tests require a disposable PostgreSQL/pgvector database and explicitly documented environment flags. Passing the offline suite alone is not production verification.

**Licence:** proprietary. Installation does not grant unrestricted usage or deployment rights; obtain written permission under [`../../LICENSE`](../../LICENSE) before using this package outside the permissions granted by the copyright holder.
