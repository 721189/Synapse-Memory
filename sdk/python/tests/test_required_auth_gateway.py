import os
import time
import unittest
from fastapi.testclient import TestClient
from synapse_memory.api.fastapi_server import app, security_manager


class TestRequiredAuthAndGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db_path = "synapse_memory.db"

    def setUp(self):
        # Clear existing keys or set up test keys
        pass

    def test_missing_key_auth(self):
        # Missing key on protected write endpoint should return 401/403
        response = self.client.post("/api/memories/add", json={"content": "test"})
        self.assertIn(response.status_code, [401, 403])

    def test_anonymous_gateway_protection(self):
        # Anonymous -> /api/memories/add -> 401/403
        r1 = self.client.post("/api/memories/add", json={"content": "anon add"})
        self.assertIn(r1.status_code, [401, 403])

        # Anonymous -> /api/memories/clear -> 401/403
        r2 = self.client.post("/api/memories/clear", json={})
        self.assertIn(r2.status_code, [401, 403])

        # Anonymous -> /api/security/gdpr-delete -> 401/403
        r3 = self.client.post("/api/security/gdpr-delete", json={"tenant_id": "tenant_alpha"})
        self.assertIn(r3.status_code, [401, 403])

    def test_read_only_key_permissions(self):
        # Create an admin key first to generate test keys
        admin_token, _ = security_manager.generate_api_key(
            name="Admin Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["admin", "memories:read", "memories:write", "memories:delete"],
            role="admin"
        )

        # Create read-only key
        ro_token, _ = security_manager.generate_api_key(
            name="Read Only Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["memories:read"],
            role="developer"
        )

        headers = {"X-Synapse-API-Key": ro_token, "X-Tenant-ID": "tenant_alpha"}

        # Read-only key -> GET works
        get_resp = self.client.get("/api/memories", headers=headers)
        self.assertEqual(get_resp.status_code, 200)

        # Read-only key -> Query works
        query_resp = self.client.post("/query", json={"prompt": "test query"}, headers=headers)
        self.assertEqual(query_resp.status_code, 200)

        # Read-only key -> Write rejected (403)
        write_resp = self.client.post("/api/memories/add", json={"content": "should fail"}, headers=headers)
        self.assertEqual(write_resp.status_code, 403)

    def test_write_key_permissions(self):
        write_token, _ = security_manager.generate_api_key(
            name="Write Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["memories:read", "memories:write"],
            role="developer"
        )

        headers = {"X-Synapse-API-Key": write_token, "X-Tenant-ID": "tenant_alpha"}

        # Write key -> write works
        write_resp = self.client.post("/api/memories/add", json={"content": "allowed write"}, headers=headers)
        self.assertEqual(write_resp.status_code, 201)

        # Write key without delete scope -> delete rejected (403)
        del_resp = self.client.delete("/api/memories/some_id", headers=headers)
        self.assertEqual(del_resp.status_code, 403)

    def test_tenant_isolation_and_selection_bug(self):
        alpha_token, _ = security_manager.generate_api_key(
            name="Alpha Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["memories:read", "memories:write"],
            role="developer"
        )

        # tenant_alpha key + tenant_beta request parameter = 403 (catches tenant-selection bug)
        headers = {"X-Synapse-API-Key": alpha_token, "X-Tenant-ID": "tenant_beta"}

        resp = self.client.get("/api/memories", headers=headers)
        self.assertEqual(resp.status_code, 403)

        ingest_resp = self.client.post("/api/memories/add", json={"content": "cross-tenant injection attempt", "tenantId": "tenant_beta"}, headers=headers)
        self.assertEqual(ingest_resp.status_code, 403)

    def test_expired_and_revoked_keys(self):
        # Expired key
        exp_token, exp_rec = security_manager.generate_api_key(
            name="Expired Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["memories:read"],
            ttl_days=-1  # Already expired
        )

        headers = {"X-Synapse-API-Key": exp_token}
        resp = self.client.get("/api/memories", headers=headers)
        self.assertEqual(resp.status_code, 403)

        # Revoked key
        rev_token, rev_rec = security_manager.generate_api_key(
            name="Revoked Key",
            organization_id="org_test",
            project_id="proj_test",
            tenant_id="tenant_alpha",
            scopes=["memories:read"]
        )
        security_manager.revoke_api_key(rev_rec.key_id)

        headers_rev = {"X-Synapse-API-Key": rev_token}
        resp_rev = self.client.get("/api/memories", headers=headers_rev)
        self.assertEqual(resp_rev.status_code, 403)


if __name__ == "__main__":
    unittest.main()
