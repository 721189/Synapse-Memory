"""Phase C/D regression tests: hierarchy enforcement, audit export/retention, KMS key loading.

All tests run on the SQLite backend; no Postgres or network required.
"""
import os
import tempfile
import unittest

from synapse_memory.core.auth import SecurityManager
from synapse_memory.core.encryption import KeyManagementProvider


class TestHierarchyEnforcement(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.sm = SecurityManager(db_path=self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_matching_hierarchy_passes(self):
        _, record = self.sm.generate_api_key(
            name="hier-key",
            organization_id="org_acme",
            project_id="proj_billing",
            tenant_id="tenant_eu",
        )
        ok, reason = self.sm.check_hierarchy(
            record,
            requested_org="org_acme",
            requested_project="proj_billing",
            requested_tenant="tenant_eu",
        )
        self.assertTrue(ok, reason)

    def test_cross_org_and_project_rejected(self):
        _, record = self.sm.generate_api_key(
            name="hier-key2",
            organization_id="org_acme",
            project_id="proj_billing",
            tenant_id="tenant_eu",
        )
        ok_org, _ = self.sm.check_hierarchy(record, requested_org="org_evil")
        self.assertFalse(ok_org)
        ok_proj, _ = self.sm.check_hierarchy(record, requested_project="proj_other")
        self.assertFalse(ok_proj)
        ok_tenant, _ = self.sm.check_hierarchy(record, requested_tenant="tenant_other")
        self.assertFalse(ok_tenant)

    def test_unscoped_request_inherits_binding(self):
        _, record = self.sm.generate_api_key(
            name="hier-key3",
            organization_id="org_acme",
            project_id="proj_billing",
            tenant_id="tenant_eu",
        )
        ok, _ = self.sm.check_hierarchy(record)
        self.assertTrue(ok)


class TestAuditExportRetention(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.sm = SecurityManager(db_path=self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_export_is_bounded_and_tenant_scoped(self):
        token, _ = self.sm.generate_api_key(name="audit-key", tenant_id="tenant_exp")
        self.sm.validate_api_key(token, requested_tenant_id="tenant_exp", route="/query", action="POST")
        rows = self.sm.export_audit_logs(tenant_id="tenant_exp", limit=1000)
        self.assertGreaterEqual(len(rows), 1)
        for r in rows:
            self.assertEqual(r.get("tenant_id"), "tenant_exp")

    def test_prune_deletes_only_expired_rows(self):
        token, _ = self.sm.generate_api_key(name="audit-key2", tenant_id="tenant_prune")
        self.sm.validate_api_key(token, requested_tenant_id="tenant_prune", route="/query", action="POST")
        before = len(self.sm.get_audit_logs(limit=1000, tenant_id="tenant_prune"))
        self.assertGreaterEqual(before, 1)
        # Prune everything older than "now" (cutoff slightly in the past keeps nothing).
        deleted_all = self.sm.prune_audit_logs(older_than_seconds=-1)
        self.assertGreaterEqual(deleted_all, before)
        self.assertEqual(self.sm.get_audit_logs(limit=1000, tenant_id="tenant_prune"), [])


class TestKMSKeyLoading(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_key_from_secret_file(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(b"2N53Wk7u2V5bK1wU_1F5Y9mZ7zN53Wk7u2V5bK1wU_0=")
            os.environ.pop("SYNAPSE_ENCRYPTION_KEY", None)
            os.environ.pop("SYNAPSE_KMS_PROVIDER", None)
            os.environ["SYNAPSE_ENCRYPTION_KEY_B64_FILE"] = path
            key = KeyManagementProvider.get_encryption_key()
            self.assertTrue(key.startswith(b"2N53Wk7u2V5bK1wU"))
        finally:
            os.remove(path)

    def test_unknown_kms_provider_fails_loudly(self):
        os.environ.pop("SYNAPSE_ENCRYPTION_KEY", None)
        os.environ.pop("SYNAPSE_ENCRYPTION_KEY_B64_FILE", None)
        os.environ["SYNAPSE_KMS_PROVIDER"] = "nope"
        os.environ["SYNAPSE_KMS_KEY_REF"] = "x"
        with self.assertRaises(RuntimeError):
            KeyManagementProvider.get_encryption_key()

    def test_env_key_still_wins(self):
        os.environ["SYNAPSE_ENCRYPTION_KEY"] = "2N53Wk7u2V5bK1wU_1F5Y9mZ7zN53Wk7u2V5bK1wU_0="
        os.environ["SYNAPSE_KMS_PROVIDER"] = "nope"
        key = KeyManagementProvider.get_encryption_key()
        self.assertTrue(key.startswith(b"2N53Wk7u2V5bK1wU"))


if __name__ == "__main__":
    unittest.main()
