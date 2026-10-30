"""API regressions for mandatory protected context and its trace metadata."""

import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import synapse_memory.api.fastapi_server as server


def _mint_key(tenant_id):
    token, _ = server.security_manager.generate_api_key(
        name="protected-context-test",
        organization_id="org_" + uuid.uuid4().hex[:8],
        project_id="proj_protected",
        tenant_id=tenant_id,
        scopes=["memories:read", "agent-runs:write", "agent-runs:read"],
        role="developer",
    )
    return {"X-Synapse-API-Key": token, "X-Tenant-ID": tenant_id}


class TestProtectedRunConstraints:
    def setup_method(self):
        self.client = TestClient(server.app)
        self.tenant_id = "protected_tenant_" + uuid.uuid4().hex[:8]
        self.headers = _mint_key(self.tenant_id)

    def test_protected_context_is_returned_even_for_empty_query(self):
        constraint_text = "Do not access or disclose another tenant's data."
        response = self.client.post(
            "/query",
            json={
                "prompt": " ",
                "maxTokens": 500,
                "protected_constraints": [
                    {"kind": "authorization", "text": constraint_text},
                ],
            },
            headers=self.headers,
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert constraint_text in data["fused_context"]
        assert data["injected_nodes_count"] == 0
        assert data["protected_constraints_count"] == 1
        assert data["protected_constraints_token_cost"] > 0

    def test_over_budget_protected_context_is_rejected_not_truncated(self):
        constraint_text = "Never drop this mandatory authorization rule. " * 20
        response = self.client.post(
            "/query",
            json={
                "prompt": " ",
                "maxTokens": 50,
                "protected_constraints": [
                    {"kind": "authorization", "text": constraint_text},
                ],
            },
            headers=self.headers,
        )

        assert response.status_code == 422, response.text
        assert "Protected context requires" in response.json()["error"]

    def test_empty_memory_result_still_records_protected_retrieval_metadata(self):
        run_id = "protected-empty-" + uuid.uuid4().hex[:12]
        created = self.client.post(
            "/api/agent-runs",
            json={"run_id": run_id},
            headers=self.headers,
        )
        assert created.status_code == 200, created.text
        started = self.client.post(
            f"/api/agent-runs/{run_id}/events",
            json={
                "schema_version": 1,
                "event_id": "evt-start-" + uuid.uuid4().hex,
                "run_id": run_id,
                "event_type": "run.started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_status": "running",
            },
            headers=self.headers,
        )
        assert started.status_code == 200, started.text

        constraint_text = "Do not perform writes outside this workspace."
        with (
            patch.object(server.embedder, "embed_query", return_value=[0.1] * server.embedding_dimension),
            patch.object(server.store, "vector_search", return_value=[]),
            patch.object(server.store, "lexical_search", return_value=[]),
            patch.object(server.store, "get_all_memories", return_value=[]),
            patch.object(server, "ACTIVE_BACKEND", "sqlite"),
        ):
            response = self.client.post(
                "/query",
                json={
                    "prompt": "find workspace context",
                    "maxTokens": 500,
                    "run_id": run_id,
                    "protected_constraints": [
                        {"kind": "authorization", "text": constraint_text},
                    ],
                },
                headers=self.headers,
            )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["retrieval_id"]
        assert data["injected_nodes_count"] == 0
        assert constraint_text in data["fused_context"]

        trace_response = self.client.get(
            f"/api/agent-runs/{run_id}/events",
            headers=self.headers,
        )
        assert trace_response.status_code == 200, trace_response.text
        events = trace_response.json()["data"]["events"]
        retrieval_events = [
            event for event in events if event["event_type"] == "memory.retrieval"
        ]
        assert len(retrieval_events) == 1
        event = retrieval_events[0]
        assert event["protected_constraints_count"] == 1
        assert event["protected_constraints_token_cost"] == data["protected_constraints_token_cost"]
        assert event["memory_evidence"] == []
        assert constraint_text not in json.dumps(event)

    def test_constraint_count_and_total_text_are_bounded(self):
        too_many = [
            {"kind": "goal", "text": "Required goal"}
            for _ in range(9)
        ]
        count_response = self.client.post(
            "/query",
            json={"prompt": " ", "protected_constraints": too_many},
            headers=self.headers,
        )
        assert count_response.status_code == 422

        too_much_text = [
            {"kind": "goal", "text": "x" * 1800}
            for _ in range(5)
        ]
        text_response = self.client.post(
            "/query",
            json={"prompt": " ", "protected_constraints": too_much_text},
            headers=self.headers,
        )
        assert text_response.status_code == 422

    def test_optional_memories_use_only_remaining_budget_and_trace_redacts_text(self):
        run_id = "protected-run-" + uuid.uuid4().hex[:12]
        created = self.client.post(
            "/api/agent-runs",
            json={"run_id": run_id},
            headers=self.headers,
        )
        assert created.status_code == 200, created.text

        # Retrieval correlation requires an active run, not merely a created summary.
        started = self.client.post(
            f"/api/agent-runs/{run_id}/events",
            json={
                "schema_version": 1,
                "event_id": "evt-start-" + uuid.uuid4().hex,
                "run_id": run_id,
                "event_type": "run.started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_status": "running",
            },
            headers=self.headers,
        )
        assert started.status_code == 200, started.text

        now = time.time()
        candidate = {
            "id": "mem_protected_context",
            "content": "Optional project context from memory.",
            "category": "fact",
            "confidence": 0.95,
            "created_at": now - 60,
            "last_accessed_at": now - 60,
            "access_count": 0,
            "token_cost": 80,
            "relevance_score": 0.9,
        }
        constraint_text = "Only operate inside the authenticated tenant."

        with (
            patch.object(server.embedder, "embed_query", return_value=[0.1] * server.embedding_dimension),
            patch.object(server.store, "vector_search", return_value=[candidate]),
            patch.object(server.store, "lexical_search", return_value=[]),
            patch.object(server.searcher, "fused_search", return_value=[candidate]),
            patch.object(server.store, "update_access_count"),
            patch.object(server, "ACTIVE_BACKEND", "sqlite"),
        ):
            response = self.client.post(
                "/query",
                json={
                    "prompt": "retrieve project context",
                    "maxTokens": 500,
                    "run_id": run_id,
                    "protected_constraints": [
                        {"kind": "authorization", "text": constraint_text},
                    ],
                },
                headers=self.headers,
            )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["protected_constraints_count"] == 1
        assert constraint_text in data["fused_context"]
        assert "Optional project context from memory." in data["fused_context"]
        memory_tokens = sum(int(item.get("token_cost", 0)) for item in data["injected_nodes"])
        assert data["protected_constraints_token_cost"] + memory_tokens <= 500

        trace_response = self.client.get(
            f"/api/agent-runs/{run_id}/events",
            headers=self.headers,
        )
        assert trace_response.status_code == 200, trace_response.text
        events = trace_response.json()["data"]["events"]
        retrieval_events = [event for event in events if event["event_type"] == "memory.retrieval"]
        assert len(retrieval_events) == 1
        event = retrieval_events[0]
        assert event["protected_constraints_count"] == 1
        assert event["protected_constraints_token_cost"] == data["protected_constraints_token_cost"]
        assert event["memory_evidence"][0]["packed"] is True
        assert constraint_text not in json.dumps(event)
        assert "Optional project context from memory." not in json.dumps(event)
