"""Offline regression tests for least-privilege DB role provisioning (P0-5).

These run with NO database server. They lock down the security contract of
``scripts/provision_db_roles.py`` so a future edit cannot silently:

  * drop ``NOBYPASSRLS`` / ``NOSUPERUSER`` from the runtime role attributes,
  * hand the runtime role ownership of the ``memories`` table,
  * widen grants beyond tenant-scoped DML,
  * leak a password into SQL text or ``--dry-run`` output,
  * accept an identifier that could break out of its SQL context.

The live proof — cross-tenant denial performed by the real non-owner runtime role
— lives in ``tests/test_pgvector_integration.py::TestRuntimeRoleIsolation`` and
runs in CI against a pgvector container.
"""

import importlib.util
import io
import os
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "scripts", "provision_db_roles.py")

RUNTIME = "synapse_app"
ADMIN = "synapse_admin"


def load_module():
    spec = importlib.util.spec_from_file_location("provision_db_roles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProvisioningContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        # Worst case for the invariant: both roles already exist and the runtime
        # role currently OWNS the table (i.e. it was created by an older release).
        cls.plan = cls.mod.build_plan(
            RUNTIME, ADMIN, "runtime-pw", "admin-pw",
            runtime_exists=True, admin_exists=True,
            has_memories_table=True, current_owner=RUNTIME)

    def test_script_and_module_load(self):
        self.assertTrue(os.path.isfile(SCRIPT))
        self.assertTrue(hasattr(self.mod, "main"))
        self.assertEqual(self.mod.RUNTIME_TABLE_PRIVILEGES,
                         ("SELECT", "INSERT", "UPDATE", "DELETE"))

    def test_runtime_role_attributes_exclude_superuser_and_bypassrls(self):
        attrs = self.mod.LEAST_PRIVILEGE_ATTRS
        tokens = attrs.split()
        for required in ("NOSUPERUSER", "NOBYPASSRLS", "NOCREATEROLE",
                         "NOCREATEDB", "NOINHERIT", "NOREPLICATION", "LOGIN"):
            self.assertIn(required, tokens)
        # The dangerous attributes must never appear as grants.
        for forbidden in ("SUPERUSER", "BYPASSRLS", "CREATEROLE", "CREATEDB",
                          "REPLICATION", "INHERIT"):
            self.assertNotIn(forbidden, tokens)

    def test_plan_never_grants_ownership_to_the_runtime_role(self):
        for label, sql, _params in self.plan:
            self.assertNotIn('OWNER TO "' + RUNTIME + '"', sql)
        ownership = [sql for label, sql, _p in self.plan if "OWNER TO" in sql]
        self.assertEqual(len(ownership), 1)
        self.assertIn('OWNER TO "' + ADMIN + '"', ownership[0])
        self.assertEqual(self.mod.ownership_statements(ADMIN, ADMIN, ADMIN), [])
        self.assertEqual(self.mod.ownership_statements(RUNTIME, ADMIN, "some_other"), [])

    def test_runtime_grants_are_tenant_scoped_dml_only(self):
        dml = [sql for _l, sql, _p in self.plan
               if sql.startswith("GRANT ") and "TABLE public.memories" in sql]
        self.assertEqual(len(dml), 1)
        self.assertEqual(
            dml[0],
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.memories '
            'TO "synapse_app"')
        joined = " ".join(sql for _l, sql, _p in self.plan)
        self.assertNotIn("GRANT ALL", joined)
        self.assertNotIn("TRUNCATE ON TABLE public.memories TO", joined)
        self.assertIn("REVOKE ALL PRIVILEGES ON TABLE public.memories", joined)
        self.assertIn("REVOKE TRUNCATE ON TABLE public.memories", joined)
        self.assertIn("REVOKE CREATE ON SCHEMA public", joined)
        self.assertIn("GRANT USAGE ON SCHEMA public", joined)

    def test_migration_role_may_create_objects_but_runtime_may_not(self):
        for label, sql, _p in self.plan:
            if "GRANT CREATE" in sql:
                self.assertIn('"synapse_admin"', sql, label)
                self.assertNotIn('"synapse_app"', sql, label)
        joined = " ".join(sql for _l, sql, _p in self.plan)
        self.assertIn('GRANT CREATE ON SCHEMA public TO "synapse_admin"', joined)
        self.assertIn('REVOKE CREATE ON SCHEMA public FROM "synapse_app"', joined)

    def test_plan_only_touches_the_two_declared_roles(self):
        joined = " ".join(sql for _l, sql, _p in self.plan)
        self.assertIn('"synapse_app"', joined)
        self.assertIn('"synapse_admin"', joined)
        for other in ('"public_reader"', '"postgres"', 'PUBLIC;'):
            self.assertNotIn(other, joined)
        # Runtime must not be able to inherit the admin role.
        self.assertIn('REVOKE "synapse_admin" FROM "synapse_app"', joined)

    def test_passwords_never_appear_in_sql_text(self):
        for label, sql, params in self.plan:
            self.assertNotIn("runtime-pw", sql, label)
            self.assertNotIn("admin-pw", sql, label)
            self.assertNotIn("PASSWORD '", sql, label)
            if "PASSWORD" in sql:
                self.assertIn("%s", sql, label)
                self.assertTrue(params, label)

    def test_verification_sql_covers_the_invariants(self):
        self.assertIn("rolsuper", self.mod.VERIFY_ROLE_ATTRS_SQL)
        self.assertIn("rolbypassrls", self.mod.VERIFY_ROLE_ATTRS_SQL)
        self.assertIn("rolcreaterole", self.mod.VERIFY_ROLE_ATTRS_SQL)
        self.assertIn("rolcreatedb", self.mod.VERIFY_ROLE_ATTRS_SQL)
        self.assertIn("tableowner", self.mod.VERIFY_TABLE_OWNER_SQL)
        self.assertIn("has_table_privilege", self.mod.VERIFY_TABLE_PRIVS_SQL)
        self.assertIn("has_schema_privilege", self.mod.VERIFY_SCHEMA_PRIVS_SQL)
        self.assertIn("pg_has_role", self.mod.VERIFY_MEMBERSHIP_SQL)
        # 7 privilege slots: 4 required + 3 forbidden.
        self.assertEqual(self.mod.VERIFY_TABLE_PRIVS_SQL.count("%s"), 7)


class TestVerificationLogic(unittest.TestCase):
    """The verification step must fail closed on every dangerous role shape."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_evaluate_role_row_flags_superuser_and_bypassrls(self):
        clean = (False, False, False, False, True)
        self.assertEqual(self.mod.evaluate_role_row(RUNTIME, clean), [])
        for bad_row, marker in (
            ((True, False, False, False, True), "SUPERUSER"),
            ((False, True, False, False, True), "BYPASSRLS"),
            ((False, False, True, False, True), "CREATEROLE"),
            ((False, False, False, True, True), "CREATEDB"),
            ((False, False, False, False, False), "cannot LOGIN"),
        ):
            failures = self.mod.evaluate_role_row(RUNTIME, bad_row)
            self.assertTrue(failures, bad_row)
            self.assertTrue(any(marker in f for f in failures), failures)
        self.assertEqual(self.mod.evaluate_role_row(RUNTIME, None),
                         [RUNTIME + ": role does not exist"])

    def test_evaluate_privileges_flags_every_escalation_path(self):
        # (SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER)
        hostile = (True, True, True, True, True, True, True)
        failures, advisories = self.mod.evaluate_privileges(
            RUNTIME, hostile, can_create_schema=True, runtime_in_admin=True,
            owner=RUNTIME, admin_role=ADMIN)
        self.assertEqual(advisories, [])
        joined = " | ".join(failures)
        self.assertIn("CREATE on schema public", joined)
        self.assertIn("is a member of " + ADMIN, joined)
        self.assertIn("OWNS public.memories", joined)
        for priv in ("TRUNCATE", "REFERENCES", "TRIGGER"):
            self.assertIn("must NOT hold " + priv, joined)
        self.assertGreaterEqual(len(failures), 6)

    def test_evaluate_privileges_flags_missing_dml(self):
        failures, _ = self.mod.evaluate_privileges(
            RUNTIME, (True, False, False, False, False, False, False),
            can_create_schema=False, runtime_in_admin=False,
            owner=ADMIN, admin_role=ADMIN)
        joined = " | ".join(failures)
        for priv in ("INSERT", "UPDATE", "DELETE"):
            self.assertIn("missing required " + priv, joined)

    def test_evaluate_privileges_is_clean_for_a_compliant_role(self):
        failures, advisories = self.mod.evaluate_privileges(
            RUNTIME, (True, True, True, True, False, False, False),
            can_create_schema=False, runtime_in_admin=False,
            owner=ADMIN, admin_role=ADMIN)
        self.assertEqual(failures, [])
        self.assertEqual(advisories, [])

    def test_unexpected_owner_is_advisory_not_fatal(self):
        failures, advisories = self.mod.evaluate_privileges(
            RUNTIME, (True, True, True, True, False, False, False),
            can_create_schema=False, runtime_in_admin=False,
            owner="someone_else", admin_role=ADMIN)
        self.assertEqual(failures, [])
        self.assertEqual(len(advisories), 1)
        self.assertIn("FORCE ROW LEVEL SECURITY", advisories[0])

    def test_missing_table_skips_privilege_checks(self):
        failures, advisories = self.mod.evaluate_privileges(
            RUNTIME, None, can_create_schema=False, runtime_in_admin=False,
            owner=None, admin_role=ADMIN)
        self.assertEqual(failures, [])
        self.assertEqual(advisories, [])


class TestCliSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_identifier_validation_rejects_injection(self):
        for hostile in ("synapse_app; DROP TABLE memories", "synapse app",
                        '"synapse_app"', "1abc", "", "a-b", "app'--",
                        "a" * 64, None, 42):
            with self.assertRaises(self.mod.IdentifierError):
                self.mod.validate_identifier(hostile)

    def test_quote_ident_returns_validated_identifier(self):
        self.assertEqual(self.mod.quote_ident("synapse_app"), '"synapse_app"')

    def test_build_plan_rejects_identical_roles(self):
        with self.assertRaises(self.mod.IdentifierError):
            self.mod.build_plan("same", "same", "p", "p", True, True, True, None)

    def test_dry_run_redacts_passwords_and_executes_nothing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.mod.main(["--dry-run", "--runtime-password", "sup3rsecret",
                                  "--admin-password", "adm1nsecret"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("sup3rsecret", out)
        self.assertNotIn("adm1nsecret", out)
        self.assertIn("<redacted:11 chars>", out)
        self.assertIn("NOBYPASSRLS", out)
        self.assertIn("REVOKE TRUNCATE", out)

    def test_main_rejects_injected_role_name(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.mod.main(["--dry-run", "--runtime-role",
                                  "app; DROP TABLE memories"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", buf.getvalue())

    def test_no_dsn_reports_instead_of_silently_passing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.mod.main(["--database-url", ""])
        self.assertEqual(code, 2)
        self.assertIn("No DATABASE_URL configured", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

