import os
import unittest
from synapse_memory.core.auth import SecurityManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore


class TestAuthAndMultiTenancy(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_auth_mt.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.auth = SecurityManager(self.db_path)
        self.store = SQLiteMemoryStore(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_api_key_generation_and_validation(self):
        token, record = self.auth.generate_api_key(
            name="Tenant A Developer Key",
            organization_id="org_alpha",
            project_id="proj_1",
            tenant_id="tenant_alpha",
            scopes=["memories:read", "memories:write"],
            role="developer"
        )
        self.assertTrue(token.startswith("syn_live_"))
        self.assertEqual(record.tenant_id, "tenant_alpha")

        # Validate with matching tenant
        valid, rec, reason = self.auth.validate_api_key(
            token=token,
            required_scope="memories:read",
            requested_tenant_id="tenant_alpha"
        )
        self.assertTrue(valid)
        self.assertIsNotNone(rec)

        # Validate with mismatched tenant should fail for non-admin
        valid, rec, reason = self.auth.validate_api_key(
            token=token,
            requested_tenant_id="tenant_beta"
        )
        self.assertFalse(valid)
        self.assertIn("Tenant authorization boundary violation", reason)

    def test_sqlite_tenant_isolation(self):
        mem_a = {
            "id": "mem_tenant_a_1",
            "tenant_id": "tenant_alpha",
            "content": "Secret credentials for alpha team",
            "category": "fact",
            "confidence": 0.95,
            "created_at": 1000.0,
            "access_count": 1,
            "token_cost": 25,
            "embedding": [0.1] * 128
        }
        mem_b = {
            "id": "mem_tenant_b_1",
            "tenant_id": "tenant_beta",
            "content": "Financial ledger for beta company",
            "category": "fact",
            "confidence": 0.90,
            "created_at": 1005.0,
            "access_count": 1,
            "token_cost": 30,
            "embedding": [0.2] * 128
        }

        self.store.insert_memory(mem_a)
        self.store.insert_memory(mem_b)

        # Query tenant alpha
        alpha_mems = self.store.get_all_memories(tenant_id="tenant_alpha")
        self.assertEqual(len(alpha_mems), 1)
        self.assertEqual(alpha_mems[0]["id"], "mem_tenant_a_1")
        self.assertEqual(alpha_mems[0]["content"], "Secret credentials for alpha team")

        # Query tenant beta
        beta_mems = self.store.get_all_memories(tenant_id="tenant_beta")
        self.assertEqual(len(beta_mems), 1)
        self.assertEqual(beta_mems[0]["id"], "mem_tenant_b_1")

        # Cross-tenant query should return empty or isolation
        gamma_mems = self.store.get_all_memories(tenant_id="tenant_gamma")
        self.assertEqual(len(gamma_mems), 0)

    def test_mark_superseded_is_tenant_scoped(self):
        """A supersede issued for one tenant must not rewrite another's row.

        SQLite has no RLS, so the tenant predicate has to live in the UPDATE.
        Regression: mark_superseded() accepted tenant_id but ignored it.
        """
        mem_a = {
            "id": "mem_tenant_a_1",
            "tenant_id": "tenant_alpha",
            "content": "Alpha fact",
            "category": "fact",
            "confidence": 0.95,
            "created_at": 1000.0,
            "access_count": 1,
            "token_cost": 25,
            "embedding": [0.1] * 128,
        }
        mem_b = {
            "id": "mem_tenant_b_1",
            "tenant_id": "tenant_beta",
            "content": "Beta fact",
            "category": "fact",
            "confidence": 0.90,
            "created_at": 1005.0,
            "access_count": 1,
            "token_cost": 30,
            "embedding": [0.2] * 128,
        }
        self.store.insert_memory(mem_a)
        self.store.insert_memory(mem_b)

        # Alpha tries to supersede Beta's memory id.
        self.store.mark_superseded("mem_tenant_b_1", "mem_hijack", tenant_id="tenant_alpha")

        beta_row = self.store.get_memory_by_id("mem_tenant_b_1")
        self.assertIsNone(beta_row["superseded_by"])
        self.assertEqual(len(self.store.get_all_memories(tenant_id="tenant_beta")), 1)

        # A legitimate in-tenant supersede still works, and hides the row.
        self.store.mark_superseded("mem_tenant_a_1", "mem_a_2", tenant_id="tenant_alpha")
        alpha_row = self.store.get_memory_by_id("mem_tenant_a_1")
        self.assertEqual(alpha_row["superseded_by"], "mem_a_2")
        self.assertEqual(len(self.store.get_all_memories(tenant_id="tenant_alpha")), 0)

    def test_key_rotation_revokes_old_and_issues_same_scope(self):
        """Zero-downtime rotation: new key works, old key is revoked."""
        old_token, old_rec = self.auth.generate_api_key(
            name="rotation-key",
            organization_id="org_alpha",
            project_id="proj_1",
            tenant_id="tenant_alpha",
            scopes=["memories:read", "memories:write"],
            role="developer"
        )
        valid_before, _, _ = self.auth.validate_api_key(token=old_token, requested_tenant_id="tenant_alpha")
        self.assertTrue(valid_before)

        new_token, new_rec = self.auth.generate_api_key(
            name=old_rec.name,
            organization_id=old_rec.organization_id,
            project_id=old_rec.project_id,
            tenant_id=old_rec.tenant_id,
            scopes=old_rec.scopes,
            role=old_rec.role,
        )
        self.assertTrue(self.auth.revoke_api_key(old_rec.key_id))

        valid_old, _, reason_old = self.auth.validate_api_key(token=old_token, requested_tenant_id="tenant_alpha")
        self.assertFalse(valid_old)
        self.assertIn("revoked", reason_old.lower())

        valid_new, new_validated, _ = self.auth.validate_api_key(
            token=new_token, required_scope="memories:write", requested_tenant_id="tenant_alpha"
        )
        self.assertTrue(valid_new)
        self.assertEqual(new_validated.organization_id, "org_alpha")
        self.assertEqual(new_validated.project_id, "proj_1")

    def test_security_manager_fail_closed_on_postgres_unavailable(self):
        with self.assertRaises(RuntimeError):
            SecurityManager(
                db_path="test_auth_fail_closed.db",
                connection_string="postgresql://invalid:invalid@127.0.0.1:9999/nonexistent",
                fail_closed=True
            )


if __name__ == "__main__":
    unittest.main()
