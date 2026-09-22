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

    def test_security_manager_fail_closed_on_postgres_unavailable(self):
        with self.assertRaises(RuntimeError):
            SecurityManager(
                db_path="test_auth_fail_closed.db",
                connection_string="postgresql://invalid:invalid@127.0.0.1:9999/nonexistent",
                fail_closed=True
            )


if __name__ == "__main__":
    unittest.main()
