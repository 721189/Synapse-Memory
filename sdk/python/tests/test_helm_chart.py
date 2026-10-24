"""Offline regression tests for the Helm chart's production contract (P1-2 residual).

These run with NO helm binary and NO cluster. They lock down chart semantics
that `helm lint` / YAML parsing silently PASS but production breaks on:

  * PostgreSQL is a DOCUMENTED external dependency — no inert ``postgresql:``
    values subtree pretending to deploy a database the chart never creates,
    and no undeclared ``Chart.yaml`` dependencies.
  * ``PYTHON_ENGINE_URL`` is DERIVED from the release fullname, so gateway ->
    engine routing works for ANY release name (a hardcoded hostname only
    works for one specific naming scheme).
  * kubelet ``$(VAR)`` expansion only sees variables defined EARLIER in the
    same env list: ``DB_PASSWORD`` must precede ``DATABASE_URL`` and
    ``REDIS_PASSWORD`` must precede ``REDIS_URL``, otherwise pods boot with
    the literal ``$(DB_PASSWORD)`` as the password and fail-closed startup
    crash-loops.
  * Engine env parity with ``deploy/docker-compose.yml``: REDIS_URL,
    RATE_LIMIT_PER_MIN, ALLOWED_ORIGINS, GEMINI_API_KEY, SYNAPSE_APPLY_DB_SCHEMA.
  * Service port, container port, and the derived engine URL all track
    ``config.pythonPort`` so an override can never split them.

The live proof (helm lint/template/parse) runs in CI's ``helm-validate`` job.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CHART = os.path.join(ROOT, "deploy", "helm-chart")
VALUES = os.path.join(CHART, "values.yaml")
CHART_YAML = os.path.join(CHART, "Chart.yaml")
CONFIGMAP = os.path.join(CHART, "templates", "configmap.yaml")
DEPLOYMENT = os.path.join(CHART, "templates", "deployment.yaml")
SERVICE = os.path.join(CHART, "templates", "service.yaml")
NOTES = os.path.join(CHART, "templates", "NOTES.txt")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def engine_section(text):
    """Return the engine half of a template that also defines the gateway."""
    marker = "app.kubernetes.io/component: gateway"
    return text.split(marker, 1)[0]


class TestChartFilesExist(unittest.TestCase):
    def test_all_chart_files_present(self):
        for path in (VALUES, CHART_YAML, CONFIGMAP, DEPLOYMENT, SERVICE, NOTES):
            self.assertTrue(os.path.isfile(path), f"missing chart file: {path}")


class TestExternalPostgresDependency(unittest.TestCase):
    """P1-2 residual: the `postgresql:` values subtree was inert — the chart
    had no `dependencies:` block, so the values configured a database that
    nothing ever deployed. PostgreSQL must be an explicit external dependency."""

    def test_no_inert_postgresql_values_subtree(self):
        values = read(VALUES)
        self.assertIsNone(
            re.search(r"(?m)^postgresql:", values),
            "values.yaml must not define a `postgresql:` subtree: the chart "
            "does not deploy a database. Document PostgreSQL as an external "
            "dependency instead (see the operator checklist in values.yaml).",
        )

    def test_chart_declares_no_undeclared_dependencies(self):
        # Comments are allowed (they document the deliberate choice); an actual
        # `dependencies:` key would require `helm dependency build` in CI and
        # pgvector support the subchart cannot provide.
        chart = read(CHART_YAML)
        self.assertIsNone(
            re.search(r"(?m)^dependencies\s*:", chart),
            "Chart.yaml must not declare dependencies without a CI "
            "`helm dependency build` step and a pgvector-capable image.",
        )

    def test_operator_checklist_documents_external_db(self):
        values = read(VALUES)
        self.assertIn("EXTERNAL dependency", values)
        self.assertIn("provision_db_roles.py", values)

    def test_apply_db_schema_switch_is_values_controlled(self):
        values = read(VALUES)
        # F-04: the shipped default must be the least-privilege, migrate-then-run
        # arrangement. The runtime role (dbUser) is a NON-owner and cannot run
        # DDL, so a "true" default would make a stock install fail closed.
        self.assertRegex(values, r'(?m)^\s+applyDbSchema:\s*"false"')
        self.assertIn("SYNAPSE_APPLY_DB_SCHEMA", read(DEPLOYMENT))

    def test_default_db_user_is_the_non_owner_runtime_role(self):
        """F-04: dbUser must not default to the schema-owning admin role."""
        values = read(VALUES)
        match = re.search(r'(?m)^\s+dbUser:\s*"(.*)"', values)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "synapse_app")
        self.assertIn("provision_db_roles.py", values)


class TestEngineUrlDerivation(unittest.TestCase):
    """A hardcoded `http://synapse-memory-engine:8008` only resolves if the
    Service happens to be named that; `helm install synapse` renders
    `synapse-synapse-memory-engine`, so the gateway would 503 forever."""

    def test_configmap_derives_url_from_fullname_when_unset(self):
        configmap = read(CONFIGMAP)
        self.assertIn("PYTHON_ENGINE_URL: {{ .Values.config.pythonEngineUrl | default (printf", configmap)
        self.assertIn("http://%s-engine:%v", configmap)
        self.assertIn('include "synapse-memory.fullname"', configmap)

    def test_values_default_is_empty_so_derivation_wins(self):
        match = re.search(r'(?m)^\s+pythonEngineUrl:\s*"(.*)"', read(VALUES))
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "", "leave pythonEngineUrl empty; set it only to override")

    def test_engine_url_port_tracks_python_port(self):
        self.assertIn(".Values.config.pythonPort | int", read(CONFIGMAP))


class TestKubeletExpansionOrder(unittest.TestCase):
    """kubelet expands `$(VAR)` sequentially against ALREADY-DEFINED vars.
    A password variable declared AFTER its consumer ships the literal
    `$(DB_PASSWORD)` into the DSN -> auth failure -> fail-closed crash loop."""

    def _assert_defines_before(self, text, first, second, label):
        i_def = text.find(f"- name: {first}")
        i_use = text.find(f"- name: {second}")
        self.assertNotEqual(i_def, -1, f"{label}: {first} not defined")
        self.assertNotEqual(i_use, -1, f"{label}: {second} not defined")
        self.assertLess(
            i_def, i_use,
            f"{label}: `{first}` must be defined BEFORE `{second}` — kubelet can "
            f"only expand `$(...)` against variables defined earlier in the same "
            f"env list, otherwise the literal placeholder ships to production.",
        )

    def test_db_password_precedes_database_url(self):
        self._assert_defines_before(engine_section(read(DEPLOYMENT)),
                                    "DB_PASSWORD", "DATABASE_URL", "engine")

    def test_redis_password_precedes_redis_url(self):
        self._assert_defines_before(engine_section(read(DEPLOYMENT)),
                                    "REDIS_PASSWORD", "REDIS_URL", "engine")


class TestEngineEnvParityWithCompose(unittest.TestCase):
    """deploy/docker-compose.yml wires these on the engine; Helm must too or
    production k8s silently runs with different behaviour (e.g. distributed
    rate limiting never activates because the engine never sees REDIS_URL)."""

    @classmethod
    def setUpClass(cls):
        cls.engine = engine_section(read(DEPLOYMENT))

    def test_distributed_rate_limit_env_present(self):
        for name in ("REDIS_URL", "REDIS_PASSWORD", "RATE_LIMIT_PER_MIN"):
            self.assertIn(f"- name: {name}", self.engine, f"engine env missing {name}")

    def test_cors_and_embedder_env_present(self):
        for name in ("ALLOWED_ORIGINS", "GEMINI_API_KEY"):
            self.assertIn(f"- name: {name}", self.engine, f"engine env missing {name}")

    def test_migrate_then_run_env_present(self):
        self.assertIn("- name: SYNAPSE_APPLY_DB_SCHEMA", self.engine)

    def test_db_fallback_stays_fail_closed(self):
        self.assertRegex(self.engine, r'SYNAPSE_ALLOW_DB_FALLBACK\s*\n\s*value: "false"')

    def test_configmap_no_longer_carries_unexpandable_redis_url(self):
        # envFrom values are passed literally; a passwordless REDIS_URL in the
        # ConfigMap would be a second, wrong definition of the same setting.
        self.assertNotIn("REDIS_URL:", read(CONFIGMAP))


class TestPortConsistency(unittest.TestCase):
    def test_service_port_uses_python_port(self):
        engine = engine_section(read(SERVICE))
        self.assertIn(".Values.config.pythonPort | int", engine)
        self.assertNotIn("- port: 8008", engine)

    def test_container_port_uses_python_port(self):
        engine = engine_section(read(DEPLOYMENT))
        self.assertIn("containerPort: {{ .Values.config.pythonPort | int }}", engine)


class TestNotesGuideOperators(unittest.TestCase):
    def test_notes_document_external_db_and_roles(self):
        notes = read(NOTES)
        self.assertIn("EXTERNAL dependency", notes)
        self.assertIn("provision_db_roles.py", notes)
        self.assertIn("applyDbSchema", notes)
        self.assertIn("/readiness", notes)


if __name__ == "__main__":
    unittest.main()

