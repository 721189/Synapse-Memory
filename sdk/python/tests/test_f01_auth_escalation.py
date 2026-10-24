"""F-01 — privilege-escalation regressions for API-key role/scope delegation.

These tests go through the real FastAPI routes (not just the helpers), because
the escalation in F-01 was only reachable end-to-end: the helper's scope guard
existed but the route never passed ``requested_scopes``, and the tenant
boundary compared the raw ``role`` string.

Covered:
  * a workspace admin cannot mint role="admin" (the legacy ambiguous string)
  * ... nor scopes=["admin"] or ["security:audit"]
  * ... nor escalate to platform_admin
  * a legitimate delegation still works
  * a key whose stored role IS "admin" (minted before the fix) is not
    cross-tenant, on both the cache-miss and cache-hit validation paths
"""
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from synapse_memory.api.fastapi_server import app, security_manager
from synapse_memory.core.auth import (
    APIKeyRecord,
    scope_satisfied,
    tenant_selection_allowed,
)


def _mint(**kwargs):
    return security_manager.generate_api_key(**kwargs)


class TestWorkspaceAdminCannotEscalate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        token, _ = _mint(
            name="workspace admin",
            organization_id="org_ws",
            project_id="proj_ws",
            tenant_id="tenant_ws1",
            scopes=[
                "auth:keys:create",
                "admin",
                "memories:read",
                "memories:write",
                "memories:delete",
            ],
            role="workspace_admin",
        )
        cls.token = token
        cls.headers = {"X-Synapse-API-Key": token, "X-Tenant-ID": "tenant_ws1"}

    def _create(self, **payload):
        body = {
            "name": "child-key",
            "organization_id": "org_ws",
            "project_id": "proj_ws",
            "tenant_id": "tenant_ws1",
        }
        body.update(payload)
        return self.client.post("/api/auth/keys/create", json=body, headers=self.headers)

    def test_cannot_mint_legacy_admin_role(self):
        resp = self._create(role="admin")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("not assignable", resp.text)

    def test_cannot_mint_admin_scope(self):
        resp = self._create(scopes=["admin"])
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_cannot_mint_security_audit_scope(self):
        resp = self._create(scopes=["security:audit"])
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_cannot_escalate_to_platform_admin(self):
        resp = self._create(role="platform_admin")
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_legitimate_delegation_still_works(self):
        resp = self._create(role="developer", scopes=["memories:read"])
        self.assertIn(resp.status_code, (200, 201), resp.text)
        minted = resp.json()["data"]["api_key"]

        # The delegated key is confined to its own tenant.
        other = {"X-Synapse-API-Key": minted, "X-Tenant-ID": "tenant_ws2"}
        self.assertEqual(self.client.get("/api/memories", headers=other).status_code, 403)


class TestLegacyRawAdminRoleIsNotPrivileged(unittest.TestCase):
    """A stored role of "admin" must not imply platform-wide access (F-01)."""

    def test_helpers_treat_raw_admin_as_workspace_scoped(self):
        legacy = APIKeyRecord(
            key_id="key_legacy_admin",
            key_hash="",
            name="legacy admin",
            organization_id="org_ws",
            project_id="proj_ws",
            tenant_id="tenant_ws1",
            scopes=["admin", "memories:read"],
            role="admin",
            is_active=True,
            expires_at=None,
            created_at=time.time(),
            last_used_at=None,
        )
        self.assertFalse(tenant_selection_allowed(legacy, "tenant_ws2"))
        self.assertTrue(tenant_selection_allowed(legacy, "tenant_ws1"))
        # The admin scope is a memory-domain superuser, not a platform one.
        self.assertTrue(scope_satisfied(legacy, "memories:read"))
        self.assertFalse(scope_satisfied(legacy, "auth:keys:create"))
        self.assertFalse(scope_satisfied(legacy, "security:audit"))

    def test_raw_admin_role_is_denied_cross_tenant_on_cache_miss_and_hit(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        from synapse_memory.core.auth import SecurityManager

        sm = SecurityManager(db_path=db_path)
        try:
            token, record = sm.generate_api_key(
                name="legacy-row",
                organization_id="org_ws",
                project_id="proj_ws",
                tenant_id="tenant_ws1",
                scopes=["admin", "memories:read"],
                role="workspace_admin",
            )
            # Simulate a key minted before the fix: the raw column says "admin".
            conn = sm._get_connection()
            try:
                conn.execute(
                    "UPDATE api_keys SET role = ? WHERE key_id = ?", ("admin", record.key_id)
                )
                conn.commit()
            finally:
                sm._return_connection(conn)

            # First call = cache miss (reads the row), second = cache hit.
            for attempt in ("cache-miss", "cache-hit"):
                ok, _rec, reason = sm.validate_api_key(
                    token=token, requested_tenant_id="tenant_ws2"
                )
                self.assertFalse(ok, f"{attempt} must deny cross-tenant access")
                self.assertIn("Tenant authorization boundary", reason)

                ok_own, _r, _ = sm.validate_api_key(
                    token=token, requested_tenant_id="tenant_ws1"
                )
                self.assertTrue(ok_own, f"{attempt} must still allow its own tenant")
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(db_path + suffix)
                except OSError:
                    pass


class TestWildcardTenantIsExplicitOnly(unittest.TestCase):
    """A "*" binding is the ONLY way to span tenants without platform_admin."""

    def _record(self, tenant, role="developer"):
        return APIKeyRecord(
            key_id="key_wild",
            key_hash="",
            name="wild",
            organization_id="org_a",
            project_id="proj_a",
            tenant_id=tenant,
            scopes=["memories:read"],
            role=role,
            is_active=True,
            expires_at=None,
            created_at=time.time(),
            last_used_at=None,
        )

    def test_wildcard_binding_may_select_any_tenant(self):
        wildcard = self._record("*")
        self.assertTrue(tenant_selection_allowed(wildcard, "tenant_b"))
        self.assertTrue(tenant_selection_allowed(wildcard, "tenant_c"))

    def test_ordinary_binding_stays_inside_its_tenant(self):
        scoped = self._record("tenant_a")
        self.assertTrue(tenant_selection_allowed(scoped, "tenant_a"))
        self.assertFalse(tenant_selection_allowed(scoped, "tenant_b"))

    def test_wildcard_binding_does_not_grant_admin_scopes(self):
        wildcard = self._record("*")
        self.assertFalse(scope_satisfied(wildcard, "auth:keys:create"))
        self.assertFalse(scope_satisfied(wildcard, "security:audit"))


if __name__ == "__main__":
    unittest.main()
