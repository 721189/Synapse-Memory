# Synapse Agent Reliability — Product and Engineering Roadmap

**Status:** Proposed plan; validate the first customer wedge before committing to the full roadmap.  
**Repository baseline:** \`main\` at \`e0f0e8f4ebf2d5f49fd5b4569022e0491c4376b1\` (2026-10-27).  
**Product intent:** Evolve Synapse Memory into a memory-aware reliability layer for existing AI agents, not another general-purpose agent framework.

## 1. Executive decision

**Pursue the direction, but narrow the first release.** The initial product should help Python teams using tool-calling agents prevent repeated failed actions, preserve important context with provenance, and inspect why a run failed.

Proposed positioning:

> **Synapse helps teams make AI-agent runs bounded, context-aware, and diagnosable—with explicit controls instead of relying on prompts alone.**

Initial ideal customer profile (ICP): small AI SaaS teams and AI automation agencies with Python agents that call business APIs and need predictable run limits, useful failure traces, and durable task context.

Do not market Synapse as an autonomous self-healing system, a compliance-certified platform, or a replacement for existing observability products until evidence supports those claims.

### Why this direction is credible

The repo already includes a Python SDK, HTTP API, Node gateway/React dashboard, SQLite and PostgreSQL/pgvector storage, database row-level security, hybrid retrieval, memory lifecycle/deduplication, token-budgeted context packing, API-key scopes/rate limits/audit records, and retrieval evaluation. Those are useful foundations.

The current observability implementation in \`sdk/python/synapse_memory/core/observability.py\` provides structured operation logs and duration/success/failure instrumentation; it is not yet a complete agent trace, loop-prevention, execution budget, or outcome-verification subsystem. Build on it deliberately rather than duplicating it.

The current production-hardening documentation explicitly leaves workload-specific validation, independent security review, and real customer usage open. Treat commercial readiness as a separate gate from green CI.

## 2. Product boundary and differentiation

### Build

1. **Bounded agent runs:** explicit maximum steps/tool calls and wall-clock limits; use provider-reported usage for token/cost limits where available.
2. **Repeated-failure detection:** detect repeated calls to the same tool with equivalent arguments and repeated identical failures; stop or pause rather than retry indefinitely.
3. **Evidence-backed context:** track memory source, timestamp, trust/provenance, correction/supersession, and the reason a memory was selected.
4. **Outcome verification:** let application developers register deterministic postconditions for important actions; distinguish “the model says done” from “the system confirms done.”
5. **Useful traces:** link run, tool, retrieval, memory, budget, verification, and abort events under a stable run ID.
6. **Safe-by-default controls:** tenant isolation, payload redaction, retention/deletion, bounded ingestion, and auditable configuration.

### Do not build in the first MVP

- A new multi-agent orchestration framework.
- A general-purpose knowledge graph before customer evidence requires one.
- A custom tracing/analytics warehouse that competes feature-for-feature with Langfuse, LangSmith, Phoenix, or Helicone.
- Automatic retries or action replays without explicit idempotency semantics.
- “Self-healing” that rewinds database memory but ignores external side effects.
- Compliance claims (GDPR/HIPAA/PCI), full payload encryption claims, or precise cost claims without evidence and review.
- Multiple framework/provider integrations before one end-to-end flow works well.

### Defensible product wedge

Memory alone is crowded. Tracing alone is crowded. Start with **memory-aware execution controls** that link retrieved context to tool actions and deterministic outcome checks. Export standard telemetry rather than forcing customers to replace tools they already use.

## 3. Current-state audit: facts and gaps

| Area | Existing foundation | Required next check or gap |
|---|---|---|
| Memory/retrieval | BM25 + dense retrieval + RRF; decay; dedup/supersession; token-budget packing | Validate factual freshness, provenance, corrections, and selection explanations using realistic app queries. |
| Agent instrumentation | Basic structured logging and operation duration/failure logging | No demonstrated end-to-end agent-run ledger, tool-call normalization, run budgets, loop detector, or outcome-verification contract yet. |
| SDK/integrations | Published Python package; HTTP client; LangChain and LlamaIndex integrations | Design optional runtime APIs without breaking current SDK contracts. |
| Storage/security | Postgres RLS and scoped auth; SQLite dev support; encryption of the authoritative \`content\` field | README/security docs acknowledge that \`content_plain\` and search derivatives remain plaintext. Threat-model telemetry and review deletion/retention paths before piloting sensitive data. |
| Evaluation | CI tests; curated semantic fixture; full LongMemEval evidence-session evaluation | Existing benchmark measures evidence retrieval, not answer correctness or overall agent-task success. Developer-specific blinded labels still need independent human review. Add agent-failure replay tests separately. |
| UI/operations | Existing React dashboard, health/readiness/metrics, deployment docs | Confirm the existing UI and API seams before choosing new screens/endpoints; avoid building a second observability system. |
| Commercial readiness | Package publish and automated checks | No independent production sign-off or confirmed customer demand is documented. Need pilots, support/runbooks, license review, and explicit security limitations. |

## 4. Workstreams and engineering ownership

A small team can run these as roles, even if one person is doing several roles:

- **Product/founder:** customer interviews, wedge selection, pilot success criteria, licensing and pricing hypothesis.
- **Runtime/SDK engineer:** run context, guard policies, tool wrappers, typed failure modes, backwards compatibility.
- **Data/retrieval engineer:** provenance and temporal semantics, event schema, migrations, retrieval instrumentation.
- **Security/reliability engineer:** threat model, tenant isolation, redaction, retention/deletion, failure-injection tests.
- **Dashboard/integration engineer:** trace/event API, run inspector, examples, optional OpenTelemetry export.
- **Evaluation engineer:** deterministic regression scenarios, failure metrics, baseline comparisons, pilot review.

For solo development, implement in dependency order; do not parallelize by building separate systems before agreeing on the event and run contracts.

## 5. Phased execution plan

The schedule below assumes a part-time solo builder with study/interview preparation taking priority. Treat the weeks as sequencing guidance, not commitments. Each phase has a go/no-go gate.

### Phase 0 — Verify the customer problem and repo seams

**Planning window:** Week 1–2.  
**Owner:** Product/founder + tech lead.

Tasks:
- Interview 10–15 developers/founders who operate Python agents or AI automation workflows. Ask about their most recent actual failure, how it was detected, recovery time, existing tools, and current workaround.
- Find 3–5 concrete failure traces or create clearly labeled synthetic cases; distinguish observed incidents from hypothetical examples.
- Test whether budget/loop containment plus memory-context diagnostics is more urgent than memory quality alone.
- Inventory the existing React UI, API schemas, auth/tenant middleware, migration mechanism, and test fixtures before proposing endpoints or database tables.
- Review package license and pilot permissions. The current repo license is proprietary; clarify what pilot users may install, run, modify, and deploy.
- Establish the current security boundary and data-flow map: authoritative encrypted content vs plaintext search projections/embeddings/telemetry.

**Deliverables:** 1-page ICP/problem statement, competitor/tool substitution map, 3 candidate pilots, schema/architecture note, privacy threat model, and baseline metrics.

**Go/no-go:** Continue the selected wedge only if at least 3 interviewees describe the problem from recent experience and at least 2 agree to a concrete pilot or technical evaluation. Otherwise narrow or revise the wedge before substantial feature coding.

### Phase 1 — Safe run envelope and event contract

**Planning window:** Weeks 2–4.  
**Owners:** Runtime/SDK + security/reliability.

Design an optional Python runtime layer; do not silently change how existing memory APIs behave.

Initial conceptual API (names are illustrative until API review):

\`\`\`python
policy = RunPolicy(
    max_steps=30,
    max_tool_calls=20,
    timeout_seconds=120,
    max_repeated_tool_calls=2,
)

with synapse.run(policy=policy, tenant_id=tenant_id) as run:
    result = run.call_tool("lookup_customer", arguments, lookup_customer)
    run.record_memory_retrieval(query=query, memory_ids=ids)
    run.verify("customer_lookup_succeeded", verify_customer_lookup(result))
\`\`\`

Implementation tasks:
- Define typed \`RunPolicy\`, \`RunContext\`, \`RunEvent\`, stable IDs, UTC timestamps, and explicit terminal states.
- Provide an explicit tool invocation wrapper/decorator. Avoid pretending the runtime can intercept arbitrary framework calls without instrumentation.
- Enforce wall-clock, step, and tool-call limits in code, not prompts. If token/cost data is unavailable from the provider, say so; allow deterministic step/time caps regardless.
- Canonicalize tool name/arguments for loop detection. Use a stable digest for comparison; do not store raw arguments by default.
- Stop or pause repeated equivalent calls after a configurable threshold, with typed exceptions and a reason visible to callers.
- Retry only when a tool's retry policy is explicitly configured. Honor bounded backoff for safe operations; do not replay ambiguous writes by default.
- Define event types for run start/finish/abort, step start/finish, model call metadata, memory retrieval, tool call start/success/failure, budget exceeded, repeated-call stop, approval, and postcondition outcome.
- Avoid leaking prompts, secrets, PII, raw tool arguments, exception secrets, and full memory content into logs. Use metadata-only recording by default and explicit opt-in for content.
- Keep tenancy/auth checks on every persisted event and detail lookup. Event APIs must not trust a caller-supplied tenant ID as authority.

**Acceptance criteria:**
- Unit tests prove each hard limit stops execution deterministically and reports the reason.
- An identical tool+argument call that fails repeatedly is blocked before the configured ceiling is exceeded.
- Different arguments do not collide in the loop detector under the documented canonicalization rules.
- Cancellation, timeout, exceptions, and failed cleanup leave a terminal, inspectable run state.
- Cross-tenant run/event reads are rejected by API checks and proven with PostgreSQL runtime-role tests.
- Default logs/events contain no raw secrets or full user content.
- Existing SDK tests and public behavior remain compatible.

### Phase 2 — Context evidence, freshness, and safe outcome checks

**Planning window:** Weeks 4–6.  
**Owners:** Data/retrieval + runtime.

Tasks:
- Audit the existing memory record/supersession model first; extend it only where fields are missing.
- Define provenance fields: source type/ID, observed/created time, optional valid-from/valid-until, trust classification, and supersession/retraction reason.
- Make temporal conflict rules explicit: a correction can invalidate prior fact use; recency alone must not override a stronger authoritative source; uncertainty/conflict should be visible instead of silently merged.
- Give every retrieval trace the memory IDs, rank/score components available, token cost, and packing decision needed to explain why that context was used.
- Support protected run goals and constraints separately from optional memories; the packer must never drop authorization/safety requirements as if they were ordinary low-value candidates.
- Define explicit postcondition/verifier callbacks for critical actions. The callback must inspect authoritative state or evidence where possible; a model's claim that a task completed is not verification.
- Separate “verified complete,” “failed,” “unknown outcome,” and “requires human review.” Never mark an ambiguous external write as safe to retry.

**Acceptance criteria:**
- Tests cover a changed fact, conflicting facts, future-dated facts, retraction/deletion, and source provenance.
- Trace output explains which memory IDs were selected and why, without exposing content by default.
- Packer tests prove required constraints stay in a reserved budget or return a clear budget/configuration error; they are not silently dropped.
- An action without a verifier cannot be presented as verified success.
- Unknown outcome is represented explicitly and does not trigger automatic replay.

### Phase 3 — Failure replay/evaluation harness and trace inspection

**Planning window:** Weeks 6–8.  
**Owners:** Evaluation + dashboard/integration.

Build a small deterministic harness before adding LLM-as-a-judge. Start with reproducible events and assertions; use judges only as a supplementary signal.

Required scenarios:
1. Same tool and arguments fail repeatedly.
2. A harmless tool returns a transient error and succeeds under an explicit bounded retry policy.
3. A non-idempotent write has an ambiguous timeout and is not replayed.
4. A goal/constraint is important early in a long run.
5. A newer correction supersedes an older fact.
6. Untrusted text contains instructions attempting to pollute trusted memory.
7. One tenant attempts to access another tenant's runs or memory IDs.
8. A run exceeds time, steps, tool calls, or known token/cost budgets.
9. A tool reports success but the registered postcondition fails.
10. An instrumented run is cancelled or interrupted and leaves a comprehensible state.

Metrics:
- repeated-call detection precision/recall on the labeled synthetic suite;
- budget enforcement success and overshoot;
- false-positive stop rate;
- postcondition verification pass/fail/unknown counts;
- retrieval evidence recall and context token count;
- instrumentation latency and storage overhead;
- dropped/undelivered events and redaction regressions.

UI: the first metadata-only run inspector is now implemented in the existing React dashboard. It lists tenant-scoped runs, shows chronological events and tool status/fingerprints, exposes retrieval memory IDs/provenance/packing decisions, reports recorded token estimates and protected-context reservation, displays guard/abort reasons and postcondition outcomes, handles loading/empty/error/paginated partial traces, and exports an explicit allowlist of redacted metadata. The current event schema does not persist a total run token/cost ceiling, so the UI explicitly marks remaining budget as unavailable rather than inferring it. Raw content access is not implemented; any future opt-in content view requires a separate audited, permissioned API design. Prefer integrating/exporting telemetry to an existing observability backend over rebuilding general-purpose dashboards.

**Acceptance criteria:** every scenario has a test that fails before the relevant guard is added; artifacts capture a reproducible trace; no benchmark is advertised as proof of production reliability; telemetry overhead and false alarms are measured.

### Phase 4 — Design-partner pilots and production hardening

**Planning window:** Weeks 8–12+, only after earlier gates pass.  
**Owners:** Product/founder + all workstreams.

Tasks:
- Onboard 2–3 design partners on non-critical or staging workloads first.
- Agree on a baseline period and a fixed success measure with each pilot (for example, fewer repeated tool invocations, shorter time-to-diagnosis, fewer unverified completions, or lower spend for the same task success rate).
- Run in observe-only mode first for policies that could block actions; enable enforcement only after reviewing false positives.
- Load-test event ingestion, exercise backups/restore, retention and tenant deletion, and verify access boundaries with the actual production runtime role.
- Review auth, tenant isolation, key handling, plain-text search derivatives, PII controls, secrets in telemetry, dependency risk, retention/export/deletion, and incident response with an independent reviewer.
- Make self-healing a future feature only after checkpoint consistency and external-side-effect/idempotency behavior are verified under injected failures.
- Obtain pilot-user permission and legal review before including their data or results in public examples.

**Acceptance criteria:**
- At least 2 pilots complete a documented evaluation; at least 1 team uses the integration in recurring work and agrees to discuss payment, or the positioning is revisited.
- Every customer-facing reliability claim is backed by an artifact and an explicit test setup.
- No unresolved critical/high security finding is accepted without documented mitigation and risk owner.
- Run data can be exported/deleted; retention is configurable and tested; tenant boundaries are tested.
- Release notes, migration/rollback instructions, limitations, support path, and usage/licensing terms are clear.

## 6. GitHub backlog: proposed ticket order

Create tickets in this dependency order. A ticket is not “done” because code exists: it needs tests, docs, and evidence.

| Order | Ticket | Priority | Depends on |
|---|---|---|---|
| 1 | Customer discovery and narrow the first paid use case | P0 | None |
| 2 | Map current API/data schemas and threat model for run/event telemetry | P0 | None; can proceed in parallel with discovery |
| 3 | Define run lifecycle, event schema, and redaction defaults | P0 | 2 |
| 4 | Implement Python run budgets and repeated-call guard | P0 | 3 |
| 5 | Persist and retrieve tenant-isolated run/event traces | P0 | 3 |
| 6 | Instrument memory retrieval and expose evidence/provenance | P1 | 3, 5 |
| 7 | Add deterministic postcondition verification contracts | P1 | 3, 4 |
| 8 | Add agent-failure replay/evaluation fixtures | P1 | 4–7 |
| 9 | Add minimal trace inspector to existing UI | P1 | 5–6 |
| 10 | Pilot onboarding, security review, and reliability report | P0 launch gate | 1, 8, 9 |

## 7. Architecture principles

- **Keep memory and control plane separable:** existing memory clients continue working without opting into agent runtime features.
- **Fail closed for security and budgets:** if an enforcement decision cannot be made, do not silently bypass the configured safety control. Expose a typed, understandable error.
- **Do not pretend estimates are facts:** provider-reported tokens/costs can drive hard provider-specific budget checks; local estimates must be labeled estimates.
- **No hidden retry/replay:** a retry must be explicitly permitted, bounded, observable, and safe for that operation.
- **Use existing standards and integrations:** emit OpenTelemetry-compatible spans/events or a documented adapter; keep Langfuse/Phoenix/other destinations optional.
- **Data minimization by default:** trace metadata first; explicit content capture opt-in; redaction before persistence; per-tenant access control, retention, and deletion.
- **Test with controlled fixtures and real pilot traces:** never conflate synthetic benchmark scores, LongMemEval evidence retrieval, LLM-judge outputs, and verified production task success.
- **Security review is a gate, not a marketing badge:** tenant RLS and field encryption are meaningful controls, but searchable plaintext derivatives still need explicit threat-modeling and should not be called “fully encrypted at rest.”
- **Backward compatibility:** version public SDK/API contracts; require migration and rollback tests for schema changes.
- **Human approval for irreversible actions:** do not promise the model or Synapse alone can safely authorize destructive writes.

## 8. Success metrics and go/no-go decisions

### MVP outcome metrics

The product must demonstrate measurable change on a reproducible suite and pilot workload. Track:
- loops halted before crossing configured execution ceiling;
- budget-limit enforcement and any overshoot;
- time to locate the first failing/unsafe step;
- proportion of critical outcomes checked against an independent postcondition;
- retrieval evidence recall and tokens packed;
- false positive blocking rate;
- additional latency, storage, and operational cost.

A higher detection rate is not automatically good if it blocks safe calls. Tune thresholds with labeled traces and report precision/recall, not a single accuracy percentage.

### Commercial gate

Proceed to recurring product development only if customers use the MVP against a real workload, can describe a measurable benefit over their current stack, and at least one is willing to discuss a paid pilot. Otherwise revisit the customer segment or wedge.

### Claims discipline

The source reports' large incident-cost and failure-rate figures are not yet independently verified by this repository. Do not put claims such as “prevents $47k incidents,” “cuts failures by X%,” or “enterprise compliant” on the site until original sources are validated and Synapse-specific evaluation supports the wording.

## 9. Competitor and standards notes

The existing categories are active and competitive:
- [Langfuse observability](https://langfuse.com/docs/observability/overview) documents tracing of prompts, model responses, token use, latency, tools, and retrieval, plus evaluation and dashboards.
- [Microsoft Research AgentRx](https://www.microsoft.com/en-us/research/blog/systematic-debugging-for-ai-agents-introducing-the-agentrx-framework/) (announced 2026-03-12) is an open-source direction for locating critical failure steps with evidence-backed constraint checks. Review its code/benchmark before claiming novel failure diagnosis.
- [OWASP LLM10: Unbounded Consumption](https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/) recommends controls such as rate limiting, quotas, resource management, timeouts, sandboxing, monitoring, and queued-action limits.
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) offer a useful standard vocabulary for model calls, tools, and retrieval telemetry.

The competitive opening is not “nobody has agent tracing.” It is a tighter workflow connecting evidence-backed memory, bounded execution, loop prevention, and explicit outcome verification in a developer-friendly self-hostable layer.

## 10. Study/career protection rule

The founder's primary near-term goal remains interview preparation and technical study. Treat Synapse as a controlled side project:
- allocate about **4–6 hours per week** at first, with a hard weekly cap;
- prioritize customer conversations and the MVP's smallest measurable outcome;
- avoid broad refactors and speculative feature branches;
- only increase product time after a pilot or repeat usage supports it;
- do not delay applications/interviews to complete a non-validated roadmap.

## 11. Definition of Done for every implementation PR

- Tests cover success, failure, boundary, tenant isolation, and privacy/redaction behavior where relevant.
- CI passes on supported Python/Node versions and DB-gated integration checks are explicitly identified.
- Public API/SDK changes and migrations are documented; migration and rollback paths are tested.
- Logs contain no secrets; trace content capture defaults are documented and covered by tests.
- Benchmark setup, raw outputs, limitations, and reproducible commands are included where claims depend on measurement.
- No fallback silently bypasses a safety control.
- A reviewer can tell exactly what evidence proves the feature works.

---

**Recommended first implementation:** after the Phase 0 discovery and architecture review, implement the event contract plus a Python run guard for bounded steps/time and repeated equivalent tool-call failures. That is the smallest path from the existing memory substrate toward a differentiated, testable reliability product.
