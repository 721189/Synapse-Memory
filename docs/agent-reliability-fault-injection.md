# Deterministic agent reliability fault-injection suite

Issue: [#32](https://github.com/721189/Synapse-Memory/issues/32)

## Purpose and evidence boundary

This suite checks deterministic runtime safeguards with synthetic local tools, fixtures, and tenant records. It is a regression harness, not a benchmark of real-world agent success, a security certification, or a reliability SLA.

Run the report locally from the repository root:

```bash
pip install -e "sdk/python[dev]"
PYTHONPATH=sdk/python python - <<'PY'
import json
from pathlib import Path
from synapse_memory.fault_injection_report import build_fault_injection_report

report = build_fault_injection_report()
Path("agent-reliability-fault-injection.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\\n",
    encoding="utf-8",
)
print(report["summary"])
if not report["summary"]["all_passed"]:
    raise SystemExit(1)
PY
```

CI runs the same report builder and uploads the fault-injection JSON as a 30-day artifact. The report is JSON with a versioned schema and per-scenario pass/fail evidence.

## Deterministic scenarios

1. **Repeated identical failures:** after the configured consecutive failure ceiling, an equivalent tool call is blocked before a further execution.
2. **Safe transient retry:** an explicitly idempotent read fails transiently and succeeds within the retry ceiling; every attempt consumes normal run step/tool budgets.
3. **Ambiguous non-idempotent write:** a write times out after a simulated possible commit; automatic replay is blocked unless the caller explicitly asserts idempotency/downstream idempotency-key protection.
4. **Long-run protected goal:** the actual /query API preserves mandatory caller context across 40 consecutive retrievals, reserves its budget before optional memories, and records only count/token-cost metadata in run traces.
5. **Correction/supersession:** a higher-confidence corrected fact supersedes the older record; the old evidence becomes ineligible at retrieval time.
6. **Untrusted memory trust escalation:** instruction-like user text is stored as unverified by default; elevated trust labels require the distinct memories:trust scope. This scope is not implied by legacy admin and may only be delegated by a platform administrator.
7. **Cross-tenant access:** the fixture checks tenant-scoped memory and run trace reads. The separate PostgreSQL integration suite remains the evidence for actual runtime-role RLS enforcement.
8. **Budget enforcement:** step, tool-call, cooperative deadline, protected token reservation, and optional-memory packing limits are exercised.
9. **Postcondition failure:** a tool's success response is not accepted as task success when an independent postcondition reports that the external result was not persisted.
10. **Cancellation/interruption:** cancellation and keyboard interruption propagate and leave a terminal aborted run state.

## Report metrics

The artifact records labeled guard outcomes (true positives, false positives, false negatives, and a safe-retry negative control); budget overshoot and protected-context reservation; corrected-evidence retention; verified, failed, and unknown postcondition outcomes; event delivery, redaction, serialized trace bytes, and instrumentation timing.

The instrumentation baseline is a tiny local no-op, not a production workload. Timing and trace size depend on the CI runner and do not establish a service SLO. The current fixture set is deliberately small; its precision/recall and false-positive rate describe only these labeled synthetic cases.

## Evidence that must remain separate

The report labels the following sources independently:

| Evidence source | Status in this report |
|---|---|
| Synthetic deterministic fault injection | Measured in this artifact |
| LongMemEval-S retrieval/evidence evaluation | Separate CI benchmark artifact |
| Developer-specific human relevance review | Awaiting independent human review |
| LLM-as-a-judge output | Not run by this suite |
| Actual end-to-end task success | Not measured by this fixture |

Do not combine these into a single reliability percentage. Keep raw LongMemEval-S benchmark output, the blinded human-review packet, judge outputs, and real task outcome records separate and preserve their dataset, model, and evaluation provenance.

## Runtime semantics and limits

- SynapseRun guards only calls explicitly routed through it. It is not a sandbox and does not intercept arbitrary framework/tool invocations.
- call_tool_with_retry(..., retry_safe=True) is an application assertion, not automatic proof of idempotency. Use it only for idempotent operations or tools protected by a downstream idempotency key.
- Retries are bounded by max_retry_attempts, repeated-failure limits, run step/tool budgets, a maximum 30-second backoff, and the cooperative run deadline. A synchronous tool already in progress cannot be forcibly stopped.
- call_tool_verified records a metadata-only verification result. The application supplies the postcondition; Synapse cannot independently prove that the condition reflects the user's real-world objective.
- Trust levels supplied directly to the in-process MemoryManager remain caller assertions. The HTTP API enforces memories:trust; applications integrating the manager directly must enforce their own trusted control plane.
- SQLite fixtures validate application tenant filtering. Production PostgreSQL isolation must still be validated using the least-privilege runtime role and row-level security, as covered by the dedicated integration workflow.
