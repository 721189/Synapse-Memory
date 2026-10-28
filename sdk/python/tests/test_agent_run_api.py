"""Authenticated API contract tests for persistent agent run traces."""
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from synapse_memory.api.fastapi_server import app, security_manager


def _mint_trace_key(tenant_id, scopes):
    token, _ = security_manager.generate_api_key(
        name="agent-trace-test",
        organization_id="org_" + uuid.uuid4().hex[:8],
        project_id="proj_trace",
        tenant_id=tenant_id,
        scopes=scopes,
        role="developer",
    )
    return {"X-Synapse-API-Key": token, "X-Tenant-ID": tenant_id}


def _event(run_id, event_type, run_status, **extra):
    body = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "run_id": run_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_status": run_status,
    }
    body.update(extra)
    return body


def test_agent_run_endpoints_persist_metadata_and_are_idempotent():
    client = TestClient(app)
    tenant_id = "trace_tenant_" + uuid.uuid4().hex[:10]
    headers = _mint_trace_key(
        tenant_id, ["agent-runs:read", "agent-runs:write", "agent-runs:delete"]
    )
    run_id = "api-run-" + uuid.uuid4().hex
    created = client.post("/api/agent-runs", json={"run_id": run_id}, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["data"]["created"] is True

    started = _event(run_id, "run.started", "running")
    first = client.post(f"/api/agent-runs/{run_id}/events", json=started, headers=headers)
    assert first.status_code == 200, first.text
    replay = client.post(f"/api/agent-runs/{run_id}/events", json=started, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["sequence_number"] == first.json()["data"]["sequence_number"]

    retrieval = _event(
        run_id,
        "memory.retrieval",
        "running",
        retrieval_id="retrieval-" + uuid.uuid4().hex[:8],
        memory_evidence=[
            {
                "memory_id": "memory-01",
                "rank": 1,
                "score_components": {"relevance_score": 0.9, "rrf_score": 0.04},
                "token_cost": 15,
                "packed": True,
                "created_at": 1_760_000_000.0,
                "supersedes": "memory-old",
            }
        ],
    )
    retrieval_response = client.post(
        f"/api/agent-runs/{run_id}/events", json=retrieval, headers=headers
    )
    assert retrieval_response.status_code == 200, retrieval_response.text

    finished = _event(
        run_id, "run.finished", "succeeded", reason="succeeded",
    )
    final_response = client.post(
        f"/api/agent-runs/{run_id}/events", json=finished, headers=headers
    )
    assert final_response.status_code == 200, final_response.text

    summary = client.get(f"/api/agent-runs/{run_id}", headers=headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["data"]["status"] == "succeeded"
    assert summary.json()["data"]["event_count"] == 3

    page = client.get(f"/api/agent-runs/{run_id}/events?limit=10", headers=headers)
    assert page.status_code == 200, page.text
    events = page.json()["data"]["events"]
    assert [event["sequence_number"] for event in events] == [1, 2, 3]
    retrieval_result = events[1]
    assert retrieval_result["event_type"] == "memory.retrieval"
    assert retrieval_result["memory_evidence"][0]["memory_id"] == "memory-01"
    assert retrieval_result["memory_evidence"][0]["packed"] is True
    assert "content" not in retrieval_result["memory_evidence"][0]
    assert "query" not in retrieval_result["memory_evidence"][0]

    listing = client.get("/api/agent-runs?limit=10", headers=headers)
    assert listing.status_code == 200, listing.text
    assert any(row["run_id"] == run_id for row in listing.json()["data"]["runs"])

    deleted = client.delete(f"/api/agent-runs/{run_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["status"] == "DELETED"
    missing = client.get(f"/api/agent-runs/{run_id}", headers=headers)
    assert missing.status_code == 404


def test_agent_run_data_cannot_select_tenant_or_use_unknown_schema():
    client = TestClient(app)
    tenant_id = "trace_tenant_" + uuid.uuid4().hex[:10]
    headers = _mint_trace_key(tenant_id, ["agent-runs:write", "agent-runs:read"])
    run_id = "api-run-" + uuid.uuid4().hex

    bad_version = client.post(
        "/api/agent-runs",
        json={"run_id": run_id, "schema_version": 2, "tenant_id": "other_tenant"},
        headers=headers,
    )
    assert bad_version.status_code == 422

    created = client.post("/api/agent-runs", json={"run_id": run_id}, headers=headers)
    assert created.status_code == 200

    forged_tenant = _event(
        run_id,
        "run.started",
        "running",
        tenant_id="somebody-elses-tenant",
    )
    response = client.post(
        f"/api/agent-runs/{run_id}/events", json=forged_tenant, headers=headers
    )
    assert response.status_code == 422


def test_agent_run_access_is_tenant_scoped_and_requires_explicit_trace_scope():
    client = TestClient(app)
    tenant_a = "trace_tenant_a_" + uuid.uuid4().hex[:8]
    tenant_b = "trace_tenant_b_" + uuid.uuid4().hex[:8]
    write_read_a = _mint_trace_key(tenant_a, ["agent-runs:read", "agent-runs:write"])
    read_b = _mint_trace_key(tenant_b, ["agent-runs:read"])
    memory_only = _mint_trace_key(tenant_a, ["memories:read"])
    admin_only = _mint_trace_key(tenant_a, ["admin"])
    run_id = "api-run-" + uuid.uuid4().hex

    created = client.post("/api/agent-runs", json={"run_id": run_id}, headers=write_read_a)
    assert created.status_code == 200
    started = _event(run_id, "run.started", "running")
    assert client.post(
        f"/api/agent-runs/{run_id}/events", json=started, headers=write_read_a
    ).status_code == 200

    other_tenant = client.get(f"/api/agent-runs/{run_id}", headers=read_b)
    assert other_tenant.status_code == 404
    assert client.get("/api/agent-runs", headers=read_b).json()["data"]["runs"] == []
    assert client.get("/api/agent-runs", headers=memory_only).status_code == 403
    assert client.get("/api/agent-runs", headers=admin_only).status_code == 403
