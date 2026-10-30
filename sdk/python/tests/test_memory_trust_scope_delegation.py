"""Trust-elevation scope delegation must remain platform-admin-only."""

from fastapi.testclient import TestClient

from synapse_memory.api.fastapi_server import app, security_manager


def test_workspace_admin_cannot_delegate_memories_trust_scope():
    token, _ = security_manager.generate_api_key(
        name="workspace-admin-trust-delegation-test",
        organization_id="org_trust_delegation",
        project_id="proj_trust_delegation",
        tenant_id="tenant_trust_delegation",
        scopes=[
            "auth:keys:create",
            "admin",
            "memories:read",
            "memories:write",
            "memories:delete",
        ],
        role="workspace_admin",
    )
    headers = {
        "X-Synapse-API-Key": token,
        "X-Tenant-ID": "tenant_trust_delegation",
    }
    client = TestClient(app)
    response = client.post(
        "/api/auth/keys/create",
        json={
            "name": "delegated-trust-writer",
            "organization_id": "org_trust_delegation",
            "project_id": "proj_trust_delegation",
            "tenant_id": "tenant_trust_delegation",
            "role": "developer",
            "scopes": ["memories:read", "memories:write", "memories:trust"],
        },
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert "memories:trust" in response.text
