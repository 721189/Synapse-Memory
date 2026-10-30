# Synapse design-partner pilot runbook

**Status:** Internal pilot-preparation document. This is not a customer contract, a security certification, or evidence that Synapse is production-ready.  
**Launch gate:** [Issue #34](https://github.com/721189/Synapse-Memory/issues/34)  
**Product boundary:** Memory-aware execution controls and diagnosable runs for existing Python agents.

## 1. Pilot objective

Determine whether Synapse creates measurable value in a real, bounded AI-agent workflow without introducing unacceptable execution, privacy, security, or operational risk.

A pilot must answer three questions:

1. Does Synapse help the team detect or contain a failure mode it already experiences?
2. Does it improve diagnosis or outcome verification without materially degrading task success, latency, or developer workflow?
3. Is the improvement valuable enough that the team will keep using it and discuss a paid engagement?

Green CI, synthetic fault-injection results, and retrieval benchmark scores are engineering evidence. They are not substitutes for customer outcomes or an independent security review.

## 2. Who qualifies as a design partner

Prioritize small AI product teams or automation agencies that:

- operate a Python agent that calls tools or business APIs;
- can identify a recent, concrete agent failure from their own work;
- can name the current workaround and its cost (developer time, repeated calls, failed tasks, or diagnosis time);
- have an engineering owner who can instrument and observe the workflow;
- can begin in a staging environment or with a non-critical workload; and
- can agree on a baseline, evaluation window, data boundary, and review cadence.

Do not select a partner solely because they are interested in AI. The problem must be present in a workflow they actually operate.

### Exclude from the initial pilot

- safety-critical or high-impact decisions;
- workflows that can cause irreversible financial, legal, medical, physical, or customer-impacting actions;
- production workloads where an incorrect block or retry could cause material harm;
- data the partner is not authorized to share or process;
- any workflow whose secrets, personal data, or raw content cannot be kept within an explicitly reviewed boundary.

A later pilot may expand only after the threat model, controls, and reviewer evidence support that workload.

## 3. Qualification and discovery

Before offering technical access, conduct a problem interview. Ask for a recent incident, not a hypothetical product wish list.

### Interview prompts

1. Walk through the last agent run that failed, looped, or reported completion incorrectly.
2. What did the agent do, what should it have done, and how was the problem discovered?
3. Which logs, traces, or monitoring tools were available? What was missing?
4. How long did it take to locate the first failing or unsafe step?
5. Did the issue cause repeated tool calls, duplicate side effects, manual cleanup, missed work, or additional model spend?
6. How often has this happened in the last month or quarter? What evidence supports that estimate?
7. What would have prevented or shortened this incident?
8. Who owns the agent, the API credentials, and the operational response?
9. What data may be recorded, where may it be processed, and what retention/deletion rules apply?
10. What measurable improvement would justify continuing after a pilot?

Record direct observations separately from estimates and opinions.

### Qualification record

Complete one record per candidate:

| Field | Record |
|---|---|
| Organization / team | |
| Technical owner | |
| Agent framework and runtime | |
| Workflow under consideration | |
| Recent observed failure | |
| Existing telemetry and workaround | |
| Frequency / impact evidence | |
| Staging or low-risk test environment | |
| Data classification and permitted fields | |
| Success metric proposed by partner | |
| Pilot sponsor and review cadence | |
| Legal / procurement / security contact | |
| Decision | Proceed / Revisit / Decline |

Proceed only when the workflow, owner, problem, and data boundary are concrete.

## 4. Pilot operating stages

### Stage 0 — Written scope and authorization

Before installation or data exchange, agree in writing on:

- the exact workflow and environment in scope;
- the tasks and agent actions included and explicitly excluded;
- the partner's authority to use the data and credentials involved;
- the data fields Synapse will receive and persist;
- retention period, deletion process, and permitted support access;
- incident contact and escalation expectations;
- whether the partner permits use of anonymized results in any public material (default: no);
- applicable license, pilot-use terms, and any required legal or privacy review.

Do not treat an informal conversation or a repository license as a customer pilot agreement.

### Stage 1 — Baseline

Measure the existing workflow before enabling Synapse controls. Use a representative, agreed task set and keep its definition fixed across comparison periods.

Record:
- task attempts and task success using the partner's existing definition;
- tool calls per task and repeated equivalent calls;
- failures, unknown outcomes, and unverified completion claims;
- time to identify the first failed or unsafe step;
- runtime latency and current operational cost where reliably measured;
- existing false alarms or manual interventions.

Do not compare unrelated tasks or use a changed success definition between baseline and treatment.

### Stage 2 — Staging instrumentation, observe-only

1. Integrate the existing SDK/API and tenant-scoped run trace inspector.
2. Verify that the partner's API key has only the scopes needed for the pilot.
3. Confirm the trace contains metadata only and does not capture prompts, memory text, raw tool arguments, tool results, credentials, or unnecessary personal data.
4. Create known-good and known-bad test runs; verify the expected event sequence and redaction.
5. Run policies in observe-only mode where a false positive could interrupt work.
6. Verify export, retention, and deletion behavior using test data before any partner data is introduced.

Do not enable blocking controls merely because the dashboard displays events. First adjudicate whether the proposed block would have been correct.

### Stage 3 — Controlled treatment

Enable only the specific controls agreed for the workflow. Keep the first treatment non-critical and reversible.

For each relevant run, capture:
- run identifier and environment;
- policy configuration and version;
- task outcome under the partner's definition;
- detected repeated calls, retries, and guard decisions;
- independent postcondition result: verified, failed, unknown, or human review;
- diagnosis time and developer intervention;
- latency/storage overhead and any operational issue.

A model response saying an action succeeded is not an independent postcondition. Unknown outcomes must remain unknown; do not replay ambiguous writes.

### Stage 4 — Review and exit

Hold a review at the agreed cadence. Review false positives and missed failures with the partner's engineer, not only aggregate counts.

At the end, produce a short report with:
- workload and evaluation window;
- baseline and treatment definitions;
- sample counts and exclusions;
- metric definitions and results;
- representative redacted traces;
- failures, false positives, regressions, and limitations;
- partner feedback and operational burden;
- continue / revise / stop recommendation.

Obtain explicit written permission before publishing the partner's name, data, traces, quotes, or results.

## 5. Pilot measurement contract

Agree the primary metric and guardrails before the treatment period. Use the same task definition in baseline and treatment. Report denominators and sample counts; do not report a percentage without its count.

| Measure | Definition | Interpretation |
|---|---|---|
| Task success | Tasks meeting the partner's pre-agreed success definition / eligible task attempts | Must not materially regress |
| Repeated-call containment | Adjudicated harmful repeated-call episodes stopped before exceeding the agreed ceiling / adjudicated harmful repeated-call episodes | Detection alone is not proof of safe containment |
| False-positive block rate | Benign tool calls blocked / benign tool calls presented to the guard, after human adjudication | A high rate is a stop condition |
| Diagnosis time | Elapsed time from failure detection to identification of the first failing or unsafe step | Compare equivalent incidents and report median plus range |
| Verified completion coverage | Critical action attempts with a deterministic postcondition result / critical action attempts | Distinguish verified, failed, unknown, and human review |
| Unverified completion rate | Completion claims without a confirming postcondition / completion claims | A reduction is useful only if task success is preserved |
| Tool-call overhead | Tool calls per eligible task and repeated equivalent calls per task | Report both aggregate and per-task distributions |
| Runtime overhead | Added p50/p95 latency attributable to Synapse instrumentation/controls | State measurement method and environment |
| Storage overhead | Trace storage per run and delivery failures/dropped events | Verify retention and deletion, not just ingestion |
| Cost | Provider-reported usage/cost per eligible task, when available | Clearly label estimates; do not infer precise spend from local token estimates |

For detection precision/recall, construct an adjudicated labeled set of failure episodes and benign calls. Define the labeling rules before reviewing results. Synthetic scenarios may be reported separately from partner-observed events; never combine them without clearly separating the sources.

Set the required task count, observation window, and acceptable regression/false-positive thresholds with each partner before starting. Do not invent a universal sample-size threshold across unlike workflows.

## 6. Privacy and data-handling defaults

The pilot starts with metadata-only telemetry.

- Do not collect raw prompts, queries, memory content, tool arguments, tool results, access tokens, API keys, or secrets.
- Treat run IDs, memory IDs, tool names, fingerprints, timestamps, error labels, and tenant metadata as potentially sensitive metadata.
- Use the minimum scopes and minimum data needed for the selected workflow.
- Use a dedicated pilot tenant and credentials; do not share credentials between partners.
- Keep credentials in the partner's approved secret-management mechanism, not in source code, trace events, issue comments, or support messages.
- Agree region, processors, retention, backup behavior, and deletion expectations before data transfer.
- Do not upload partner traces to public issues, benchmark fixtures, or model prompts.
- Keep redacted example traces synthetic unless written permission covers real examples.
- Verify deletion from the active store and document the limits of deletion from backups and external systems.
- Do not add or enable raw-content capture as a convenience during a pilot. Any future content access needs a separate audited design, explicit permission, and security review.

If a data boundary cannot be demonstrated, stop the pilot before sending the data.

## 7. Independent security-review evidence pack

The reviewer must be independent of the implementation. This checklist prepares evidence; it does not mark controls as passed.

| Area | Evidence to provide | Reviewer questions / tests | Result |
|---|---|---|---|
| System/data-flow map | Current architecture and data-flow diagram | Where does data enter, transit, persist, export, and leave the system? | Not assessed |
| Authentication | API-key creation, storage, rotation, revocation design | Can unauthenticated callers access data-bearing routes? Are keys exposed in logs/errors? | Not assessed |
| Authorization | Scope matrix and endpoint inventory | Are read/write scopes least-privilege and consistently enforced? | Not assessed |
| Tenant isolation | PostgreSQL RLS policies, runtime role grants, tenant predicates, cross-tenant test output | Can tenant A read, mutate, enumerate, or infer tenant B data through any route or export? | Not assessed |
| Encryption and derivatives | Field-level encryption design and inventory of plaintext columns, indexes, embeddings, caches, and backups | Are plaintext search derivatives and telemetry included in the threat model? | Not assessed |
| Secrets and PII | Redaction rules, sample synthetic traces, logging configuration | Can credentials, raw tool payloads, prompts, or unnecessary PII reach logs or exports? | Not assessed |
| API and gateway | Route inventory, proxy code, CORS/rate-limit configuration | Does the gateway preserve caller identity and fail closed? Are path and header handling safe? | Not assessed |
| Database | Migration history, least-privilege role, backup configuration | Are runtime privileges minimal? Can backup/restore be completed and verified? | Not assessed |
| Retention/export/deletion | Retention policy, export/deletion tests, backup limitations | Do deletion and export work across all relevant data stores and derivatives? | Not assessed |
| Dependencies/build | Dependency lockfiles, security scan artifacts, image build provenance | Are known vulnerabilities reviewed and container images reproducible? | Not assessed |
| Operations | Monitoring, alerting, incident response, key-compromise and tenant-breach procedures | Can the operator detect, contain, investigate, and communicate an incident? | Not assessed |
| Availability/recovery | Load test, failure injection, backup/restore and migration/rollback evidence | Are failure modes bounded and recovery steps rehearsed? | Not assessed |
| Legal/licensing | License, pilot terms, data-processing terms as applicable | Are pilot use, redistribution, data handling, and public claims authorized? | Not assessed |

### Mandatory stop conditions

Do not move the pilot to a production-like or higher-risk workload while any of these remain unresolved:

- cross-tenant access or authorization bypass;
- credentials, secrets, or prohibited raw content appearing in persisted traces or exports;
- inability to revoke compromised credentials;
- an unverified or unrehearsed backup/restore or deletion path for the pilot data;
- a critical/high security finding without a documented mitigation, accountable owner, and explicit risk acceptance;
- blocking/retry behavior that can create unsafe external side effects or silently replay ambiguous writes.

A green scanner or CI job does not replace manual security review.

## 8. Pilot decision record

Complete this at the end of each pilot:

| Decision field | Record |
|---|---|
| Partner and workflow | |
| Evaluation dates | |
| Baseline / treatment task counts | |
| Primary metric and agreed target | |
| Task success change | |
| False-positive blocks | |
| Diagnosis-time change | |
| Verified / failed / unknown outcomes | |
| Latency and storage overhead | |
| Data-handling/deletion test result | |
| Security findings and owners | |
| Partner's recurring-use intention | |
| Willingness to discuss paid use | |
| Partner permission for any public use | |
| Decision | Continue / Revise / Stop |
| Follow-up owner and date | |

### Launch-gate exit criteria

Issue #34 remains open until all of the following are evidenced:

- at least two pilots have documented results;
- at least one team uses the integration repeatedly and is willing to discuss payment, or the product wedge is explicitly reconsidered;
- each public reliability claim links to reproducible evidence and clearly separates synthetic from partner-observed results;
- no critical/high security finding remains without documented mitigation and an accountable risk owner;
- export, deletion, retention, backup/restore, and migration/rollback have test and runbook evidence;
- pilot licensing, data terms, limitations, and support expectations are reviewed.

## 9. Next actions

1. Identify and qualify three candidate teams; do not start by sending them an install link.
2. Conduct discovery interviews and record concrete incidents and current workarounds.
3. Choose one narrow staging workflow with a technical owner and explicit data boundary.
4. Prepare a synthetic trace demonstration and a one-page architecture/data-flow explanation.
5. Find an independent security reviewer and agree on the scope and evidence format.
6. Start a pilot only after Stage 0 authorization and the data-handling checks are complete.

**Time protection:** keep this work inside the existing 4–6 hour weekly side-project limit while interview preparation remains the primary near-term priority. Prefer partner conversations and evidence collection over new product features.
