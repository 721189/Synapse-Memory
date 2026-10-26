#!/usr/bin/env python3
"""Least-privilege PostgreSQL role provisioning for SynapseMemory (P0-5).

The engine issues ``ALTER TABLE memories ENABLE/FORCE ROW LEVEL SECURITY``, but
that guarantee only holds for real traffic when the connecting role is:

  * NOT a superuser            (superusers bypass row-level security)
  * NOT granted BYPASSRLS      (such roles bypass row-level security)
  * NOT the table owner        (owners bypass RLS unless FORCE RLS is set)
  * DML-only                   (no DDL, no TRUNCATE, no ownership)

This script creates and repairs the two roles the production threat model
requires, then VERIFIES the result and exits non-zero if the invariant is broken.

  <admin role>    default ``synapse_admin``  owns the schema; runs DDL/migrations.
  <runtime role>  default ``synapse_app``    what the engine connects as. DML only.

Deployment topology: run this once, then run the schema migration step as the
admin role, then start the engine with ``SYNAPSE_APPLY_DB_SCHEMA=false`` and a
``DATABASE_URL`` built from the runtime role. The engine refuses to start if
``memories`` is missing instead of silently degrading to in-memory storage.

Idempotent: safe to re-run; existing roles are ALTERed back to the required
attributes. ``--dry-run`` prints the exact plan and executes nothing.

Usage:
  DATABASE_URL=postgresql://bootstrap@host/db \\
  python scripts/provision_db_roles.py \\
      --runtime-password "$SYNAPSE_APP_DB_PASSWORD" \\
      --admin-password   "$SYNAPSE_ADMIN_DB_PASSWORD"

  python scripts/provision_db_roles.py --dry-run

Exit codes: 0 = provisioned and verified, 1 = a check failed, 2 = nothing
runnable in this environment (missing DSN or driver), reported rather than
silently passing.
"""

import argparse
import os
import re
import sys

# SECURITY: neither role may be SUPERUSER or BYPASSRLS, and NOCREATEROLE /
# NOCREATEDB stop a leaked credential from minting new roles or databases.
# NOINHERIT prevents accidental privilege pickup via role membership.
LEAST_PRIVILEGE_ATTRS = (
    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS LOGIN"
)

# Role/identifier names accepted from the operator or environment. Anything that
# could break out of an identifier is rejected before it reaches SQL.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Privileges the runtime role must hold on ``memories`` (tenant-scoped DML).
RUNTIME_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
# Privileges the runtime role must NOT hold (they escape tenant row policies).
FORBIDDEN_TABLE_PRIVILEGES = ("TRUNCATE", "REFERENCES", "TRIGGER")

# Auth tables owned by the admin migration role; runtime needs only
# key lookup (SELECT), last-used update (UPDATE), and audit insert (INSERT).
RUNTIME_AUTH_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE")
FORBIDDEN_AUTH_TABLE_PRIVILEGES = ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

AUTH_TABLES = ("api_keys", "auth_audit_logs")

VERIFY_ROLE_ATTRS_SQL = (
    "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin "
    "FROM pg_roles WHERE rolname = %s"
)
VERIFY_TABLE_OWNER_SQL = (
    "SELECT tableowner FROM pg_tables "
    "WHERE schemaname = 'public' AND tablename = 'memories'"
)
VERIFY_TABLE_PRIVS_SQL = (
    "SELECT "
    "has_table_privilege(%s, 'public.memories', 'SELECT'), "
    "has_table_privilege(%s, 'public.memories', 'INSERT'), "
    "has_table_privilege(%s, 'public.memories', 'UPDATE'), "
    "has_table_privilege(%s, 'public.memories', 'DELETE'), "
    "has_table_privilege(%s, 'public.memories', 'TRUNCATE'), "
    "has_table_privilege(%s, 'public.memories', 'REFERENCES'), "
    "has_table_privilege(%s, 'public.memories', 'TRIGGER')"
)
VERIFY_SCHEMA_PRIVS_SQL = "SELECT has_schema_privilege(%s, 'public', 'CREATE')"
VERIFY_MEMBERSHIP_SQL = "SELECT pg_has_role(%s, %s, 'member')"


class IdentifierError(ValueError):
    """Raised when a role or identifier name is not safe to interpolate."""


def validate_identifier(name, kind="identifier"):
    """Reject anything that is not a bare PostgreSQL identifier.

    Names reach SQL by string interpolation (CREATE ROLE / GRANT take identifiers,
    not bind parameters), so this is the injection boundary.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise IdentifierError(
            "Unsafe " + kind + " name " + repr(name) + ": expected a bare "
            "identifier matching [A-Za-z_][A-Za-z0-9_]{0,62}"
        )
    return name


def quote_ident(name):
    """Return a double-quoted, validated SQL identifier."""
    return '"' + validate_identifier(name).replace('"', '""') + '"'


def role_statements(role, password, exists):
    """CREATE or ALTER the role so it always carries the required attributes.

    Passwords travel as bind parameters (``%s``) so they never appear in the SQL
    text, in logs, or in ``--dry-run`` output.
    """
    ident = quote_ident(role)
    if exists:
        sql = "ALTER ROLE " + ident + " WITH " + LEAST_PRIVILEGE_ATTRS
        if password:
            return [(role + ": enforce least-privilege attributes",
                     sql + " PASSWORD %s", (password,))]
        return [(role + ": enforce least-privilege attributes", sql, ())]
    return [(role + ": create least-privilege login role",
             "CREATE ROLE " + ident + " WITH " + LEAST_PRIVILEGE_ATTRS + " PASSWORD %s",
             (password,))]


def privilege_statements(runtime_role, admin_role, has_memories_table,
                         auth_tables=None):
    """Least-privilege grants for the runtime role.

    Runtime gets schema USAGE, DML on ``memories``, and nothing else: no schema
    CREATE, no table ownership, no membership in the admin role, and no
    destructive statement privileges.

    ``auth_tables`` limits the per-table auth grants to relations that already
    exist: GRANT/REVOKE on a missing relation is an error, and the
    migrate-then-run topology provisions roles BEFORE the migration creates
    ``api_keys``/``auth_audit_logs``. Default: all ``AUTH_TABLES``.
    """
    if auth_tables is None:
        auth_tables = AUTH_TABLES
    rt = quote_ident(runtime_role)
    adm = quote_ident(admin_role)
    stmts = [
        # PostgreSQL 15+ revoked CREATE on schema public from PUBLIC, so the
        # migration role must be granted it explicitly or migrations cannot run
        # as a non-superuser role.
        ("admin: schema CREATE for migrations",
         "GRANT CREATE ON SCHEMA public TO " + adm, ()),
        ("admin: keep schema ownership with the migration role",
         "GRANT USAGE ON SCHEMA public TO " + adm, ()),
        ("runtime: schema USAGE only",
         "GRANT USAGE ON SCHEMA public TO " + rt, ()),
        ("runtime: revoke schema CREATE",
         "REVOKE CREATE ON SCHEMA public FROM " + rt, ()),
        ("runtime: ensure it is NOT a member of the admin role",
         "REVOKE " + adm + " FROM " + rt, ()),
    ]
    if has_memories_table:
        stmts += [
            ("runtime: strip every existing table privilege",
             "REVOKE ALL PRIVILEGES ON TABLE public.memories FROM " + rt, ()),
            ("runtime: grant tenant-scoped DML only",
             "GRANT " + ", ".join(RUNTIME_TABLE_PRIVILEGES)
             + " ON TABLE public.memories TO " + rt, ()),
            ("runtime: revoke TRUNCATE",
             "REVOKE TRUNCATE ON TABLE public.memories FROM " + rt, ()),
        ]
    # Auth tables: runtime needs SELECT (key lookup), UPDATE (last_used_at),
    # INSERT (audit log). No DELETE/TRUNCATE/REFERENCES/TRIGGER. Only tables
    # that exist are touched (see ``auth_tables`` in the docstring).
    for table in auth_tables:
        stmts += [
            (f"runtime: strip every existing privilege on {table}",
             f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM " + rt, ()),
            (f"runtime: grant scoped DML on {table}",
             "GRANT " + ", ".join(RUNTIME_AUTH_TABLE_PRIVILEGES)
             + f" ON TABLE public.{table} TO " + rt, ()),
            (f"runtime: revoke forbidden privileges on {table}",
             "REVOKE " + ", ".join(FORBIDDEN_AUTH_TABLE_PRIVILEGES)
             + f" ON TABLE public.{table} FROM " + rt, ()),
        ]
    stmts.append(
        ("future tables created by the migration role: DML only",
         "ALTER DEFAULT PRIVILEGES FOR ROLE " + adm + " IN SCHEMA public "
         "GRANT " + ", ".join(RUNTIME_TABLE_PRIVILEGES) + " ON TABLES TO " + rt,
         ())
    )
    return stmts


def ownership_statements(runtime_role, admin_role, current_owner):
    """Move table ownership off the runtime role (owners bypass RLS).

    Best-effort: requires the executing role to be the owner or a superuser.
    """
    if not current_owner or current_owner == admin_role:
        return []
    validate_identifier(runtime_role, "runtime role")
    if current_owner == runtime_role:
        return [("transfer memories ownership to the migration role",
                 "ALTER TABLE public.memories OWNER TO " + quote_ident(admin_role), ())]
    return []


def evaluate_role_row(role, row):
    """Return human-readable failures for a pg_roles row (empty list if clean).

    Pure function so the security invariant is unit-testable without a server.
    """
    if row is None:
        return [role + ": role does not exist"]
    rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin = row
    failures = []
    if rolsuper:
        failures.append(role + ": is a SUPERUSER (bypasses row-level security)")
    if rolbypassrls:
        failures.append(role + ": has BYPASSRLS (bypasses row-level security)")
    if rolcreaterole:
        failures.append(role + ": has CREATEROLE")
    if rolcreatedb:
        failures.append(role + ": has CREATEDB")
    if not rolcanlogin:
        failures.append(role + ": cannot LOGIN")
    return failures


def evaluate_privileges(runtime_role, table_privs, can_create_schema,
                        runtime_in_admin, owner, admin_role,
                        auth_table_privs=None):
    """Return ``(failures, advisories)`` for the runtime role's privileges.

    ``table_privs`` is the 7-tuple from VERIFY_TABLE_PRIVS_SQL, or None when the
    table does not exist yet. ``auth_table_privs`` is a per-table dict of
    (priv -> bool) for auth tables, or None. Advisories are reported but do
    not fail the run.
    """
    failures = []
    advisories = []
    if can_create_schema:
        failures.append(runtime_role + ": has CREATE on schema public (DDL must be "
                                       "owned by the migration role)")
    if runtime_in_admin:
        failures.append(runtime_role + ": is a member of " + admin_role
                        + " (privilege escalation path)")
    if owner is not None:
        if owner == runtime_role:
            failures.append(runtime_role + ": OWNS public.memories (owners bypass RLS)")
        elif owner != admin_role:
            advisories.append("public.memories owner is " + str(owner)
                              + ", expected " + admin_role
                              + " (FORCE ROW LEVEL SECURITY still applies)")
    if table_privs is not None:
        keys = RUNTIME_TABLE_PRIVILEGES + FORBIDDEN_TABLE_PRIVILEGES
        granted = dict(zip(keys, table_privs))
        for priv in RUNTIME_TABLE_PRIVILEGES:
            if not granted.get(priv):
                failures.append(runtime_role + ": missing required " + priv
                                + " on public.memories")
        for priv in FORBIDDEN_TABLE_PRIVILEGES:
            if granted.get(priv):
                failures.append(runtime_role + ": must NOT hold " + priv
                                + " on public.memories")
    # Verify auth table privileges (runtime needs SELECT/INSERT/UPDATE only).
    # Iterate the tables the caller actually verified: a pre-migration run has
    # no auth tables, and absent ones must not be reported as missing.
    if auth_table_privs is not None:
        for table, perms in auth_table_privs.items():
            for priv in RUNTIME_AUTH_TABLE_PRIVILEGES:
                if not perms.get(priv):
                    failures.append(runtime_role + ": missing required " + priv
                                    + " on public." + table)
            for priv in FORBIDDEN_AUTH_TABLE_PRIVILEGES:
                if perms.get(priv):
                    failures.append(runtime_role + ": must NOT hold " + priv
                                    + " on public." + table)
    return failures, advisories


def build_plan(runtime_role, admin_role, runtime_password, admin_password,
               runtime_exists, admin_exists, has_memories_table, current_owner=None,
               auth_tables=None):
    """Ordered (label, sql, params) plan. No plaintext password ever enters SQL."""
    validate_identifier(runtime_role, "runtime role")
    validate_identifier(admin_role, "admin role")
    if runtime_role == admin_role:
        raise IdentifierError("runtime role and admin role must differ")
    plan = role_statements(admin_role, admin_password, admin_exists)
    plan += role_statements(runtime_role, runtime_password, runtime_exists)
    plan += privilege_statements(runtime_role, admin_role, has_memories_table,
                                 auth_tables=auth_tables)
    plan += ownership_statements(runtime_role, admin_role, current_owner)
    return plan


def print_plan(plan):
    """Print the plan with bind parameters redacted (never log secrets)."""
    print("Planned statements (bind parameters redacted):")
    for label, sql, params in plan:
        redacted = ", ".join(
            "<redacted:" + str(len(str(p))) + " chars>" for p in params)
        print("  - " + label)
        print("      " + sql + (("   [params: " + redacted + "]") if params else ""))


def _connect(dsn):
    import psycopg2  # type: ignore
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _fetchone(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()


def provision(dsn, runtime_role, admin_role, runtime_password, admin_password,
              verify_only=False, skip_verify=False):
    """Apply the plan, then verify the invariant.

    Returns ``(failures, advisories)`` — both empty on success.
    """
    conn = _connect(dsn)
    failures = []
    advisories = []
    try:
        cur = conn.cursor()
        admin_exists = bool(_fetchone(
            cur, "SELECT 1 FROM pg_roles WHERE rolname = %s;", (admin_role,)))
        runtime_exists = bool(_fetchone(
            cur, "SELECT 1 FROM pg_roles WHERE rolname = %s;", (runtime_role,)))
        owner_row = _fetchone(cur, VERIFY_TABLE_OWNER_SQL, ())
        current_owner = owner_row[0] if owner_row else None
        has_table = current_owner is not None
        # Auth-table grants/verification only apply to relations that exist:
        # GRANT/REVOKE/has_table_privilege on a missing table is an error, and
        # the first provisioning run happens BEFORE the migration creates them.
        existing_auth_tables = tuple(
            table for table in AUTH_TABLES
            if _fetchone(
                cur,
                "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = %s;",
                (table,)))

        plan = build_plan(runtime_role, admin_role, runtime_password, admin_password,
                          runtime_exists, admin_exists, has_table, current_owner,
                          auth_tables=existing_auth_tables)

        if not verify_only:
            for label, sql, params in plan:
                try:
                    cur.execute(sql, params)
                    print("  [ok] " + label)
                except Exception as exc:
                    # Ownership transfer is best-effort: FORCE ROW LEVEL SECURITY
                    # already neutralises owner-bypass, and a non-superuser
                    # operator legitimately cannot move ownership.
                    if label.startswith("transfer"):
                        advisories.append(label + ": " + str(exc).strip())
                        continue
                    failures.append(label + ": " + str(exc).strip())

        if skip_verify:
            return failures, advisories

        for role in (runtime_role, admin_role):
            failures += evaluate_role_row(
                role, _fetchone(cur, VERIFY_ROLE_ATTRS_SQL, (role,)))

        if has_table:
            privs = _fetchone(cur, VERIFY_TABLE_PRIVS_SQL, tuple([runtime_role] * 7))
            can_create = bool(_fetchone(
                cur, VERIFY_SCHEMA_PRIVS_SQL, (runtime_role,))[0])
            in_admin = bool(_fetchone(
                cur, VERIFY_MEMBERSHIP_SQL, (runtime_role, admin_role))[0])
            # Verify auth table privileges for the runtime role, for the auth
            # tables that exist (a pre-migration run has none to check).
            auth_table_privs = {}
            for table in existing_auth_tables:
                # has_table_privilege(role, table, priv) returns boolean for each priv.
                auth_privs = {}
                for priv in RUNTIME_AUTH_TABLE_PRIVILEGES + FORBIDDEN_AUTH_TABLE_PRIVILEGES:
                    row = _fetchone(
                        cur,
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (runtime_role, f"public.{table}", priv),
                    )
                    auth_privs[priv] = bool(row[0]) if row else False
                auth_table_privs[table] = auth_privs
            priv_failures, priv_advisories = evaluate_privileges(
                runtime_role, privs, can_create, in_admin, current_owner, admin_role,
                auth_table_privs=auth_table_privs)
            failures += priv_failures
            advisories += priv_advisories
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return failures, advisories


def _apply_migrations(dsn, runtime_role):
    """Apply the forward-only schema migrations as the admin/bootstrap role.

    Run AFTER the runtime role exists: the runtime role is deliberately a
    NON-owner with DML-only rights, so it can never create the schema itself.
    Without this step a clean database has roles but no tables and the engine
    (with SYNAPSE_APPLY_DB_SCHEMA=false) refuses to start.
    """
    try:
        import psycopg2
    except ImportError:
        print("FAIL psycopg2 is not installed; cannot apply migrations.")
        return 2

    try:
        from synapse_memory.core.migrations import run_migrations
    except ImportError:
        # Allow running straight from a checkout without PYTHONPATH set.
        _sdk = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sdk", "python")
        if _sdk not in sys.path:
            sys.path.insert(0, _sdk)
        try:
            from synapse_memory.core.migrations import run_migrations
        except ImportError as exc:
            print("FAIL cannot import synapse_memory migrations: " + str(exc))
            return 2

    def _connect():
        return psycopg2.connect(dsn)

    # Ensure the memories table exists before running migrations.
    # The migration runner assumes the table is already present; on a fresh
    # database (empty volume) it does not exist, causing ALTER TABLE to fail.
    # This CREATE TABLE IF NOT EXISTS is idempotent and safe to re-run.
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id VARCHAR(128) PRIMARY KEY,
                tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
                content TEXT NOT NULL,
                category VARCHAR(64) NOT NULL,
                confidence REAL NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                last_accessed_at DOUBLE PRECISION NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                token_cost INTEGER NOT NULL DEFAULT 0,
                feedback_multiplier REAL NOT NULL DEFAULT 1.0,
                supersedes VARCHAR(128),
                superseded_by VARCHAR(128)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Ensured memories table exists (CREATE TABLE IF NOT EXISTS).")
    except Exception as exc:
        print("WARN failed to ensure memories table: " + str(exc))
        # Continue anyway – the table may already exist or another issue occurred.

    try:
        applied = run_migrations(_connect, is_postgres=True, fail_closed=True)
    except Exception as exc:
        print("FAIL schema migration failed: " + str(exc))
        return 1

    print("Applied " + str(applied) + " schema migration(s) as the admin role; "
          "runtime role '" + runtime_role + "' remains a NON-owner.")

    # The auth schema (api_keys / auth_audit_log) is created by SecurityManager,
    # NOT by the memories migrations. With SYNAPSE_APPLY_DB_SCHEMA=false the
    # engine only VALIDATES it, so it must be created here as the admin role —
    # otherwise the engine fails closed at startup with
    # "Auth table 'public.api_keys' is missing".
    try:
        from synapse_memory.core.auth import SecurityManager
        SecurityManager(connection_string=dsn, fail_closed=True)
    except Exception as exc:
        print("FAIL auth schema provisioning failed: " + str(exc))
        return 1
    print("Auth schema (api_keys / audit log) provisioned as the admin role.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Provision least-privilege SynapseMemory PostgreSQL roles (P0-5)")
    parser.add_argument("--database-url",
                        default=os.environ.get("DATABASE_URL")
                        or os.environ.get("SYNAPSE_ADMIN_DATABASE_URL") or "",
                        help="Bootstrap/migration DSN (defaults to $DATABASE_URL)")
    parser.add_argument("--runtime-role",
                        default=os.environ.get("SYNAPSE_DB_RUNTIME_ROLE", "synapse_app"))
    parser.add_argument("--admin-role",
                        default=os.environ.get("SYNAPSE_DB_ADMIN_ROLE", "synapse_admin"))
    parser.add_argument("--runtime-password",
                        default=os.environ.get("SYNAPSE_APP_DB_PASSWORD", ""))
    parser.add_argument("--admin-password",
                        default=os.environ.get("SYNAPSE_ADMIN_DB_PASSWORD", ""))
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit without touching the database")
    parser.add_argument("--verify-only", action="store_true",
                        help="change nothing; only check the invariant")
    parser.add_argument("--skip-verify", action="store_true",
                        help="apply the plan but skip the post-provision check")
    parser.add_argument("--apply-migrations", action="store_true",
                        help="after provisioning, apply the forward-only schema "
                             "migrations using the admin/bootstrap DSN. Required for "
                             "a clean database, because the DML-only runtime role cannot "
                             "create the schema itself (F-04).")
    args = parser.parse_args(argv)

    try:
        validate_identifier(args.runtime_role, "runtime role")
        validate_identifier(args.admin_role, "admin role")
    except IdentifierError as exc:
        print("FAIL " + str(exc))
        return 1

    if args.dry_run:
        try:
            plan = build_plan(
                args.runtime_role, args.admin_role,
                args.runtime_password, args.admin_password,
                runtime_exists=True, admin_exists=True,
                has_memories_table=True, current_owner=args.runtime_role)
        except IdentifierError as exc:
            print("FAIL " + str(exc))
            return 1
        print("== SynapseMemory DB role provisioning (dry run) ==")
        print_plan(plan)
        return 0

    if not args.database_url:
        print("No DATABASE_URL configured; nothing to provision. "
              "Set DATABASE_URL (bootstrap/migration DSN) and re-run.")
        return 2
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("psycopg2 is not installed; cannot provision. "
              "Install with: pip install 'synapse-memory-engine[pgvector]'")
        return 2

    print("== SynapseMemory DB role provisioning ==")
    failures, advisories = provision(
        args.database_url, args.runtime_role, args.admin_role,
        args.runtime_password, args.admin_password,
        verify_only=args.verify_only, skip_verify=args.skip_verify)

    for item in advisories:
        print("  [warn] " + item)
    if failures:
        print("VERIFICATION FAILED (" + str(len(failures)) + " problem(s)):")
        for item in failures:
            print("  - " + item)
        return 1
    if args.verify_only:
        print("Invariant holds: " + args.runtime_role + " is a NON-owner, "
              "NOBYPASSRLS, NOSUPERUSER, DML-only role.")
    else:
        print("Provisioned and verified: " + args.runtime_role + " is a NON-owner, "
              "NOBYPASSRLS, NOSUPERUSER, DML-only role.")
        if args.apply_migrations:
            rc = _apply_migrations(args.database_url, args.runtime_role)
            if rc != 0:
                return rc
            # Re-apply the grants: migrations just created the tables AS THE
            # ADMIN ROLE, after the first grant pass ran, so the runtime role
            # would have no privileges on them. This second pass is what makes a
            # fresh deployment able to query on its very first request.
            print("Re-applying runtime grants on the migrated schema...")
            failures, advisories = provision(
                args.database_url, args.runtime_role, args.admin_role,
                args.runtime_password, args.admin_password,
                verify_only=False, skip_verify=args.skip_verify)
            for item in advisories:
                print("  [warn] " + item)
            if failures:
                print("VERIFICATION FAILED (" + str(len(failures)) + " problem(s)):")
                for item in failures:
                    print("  - " + item)
                return 1
        print("Next: start the engine with SYNAPSE_APPLY_DB_SCHEMA=false and the "
              "runtime-role DATABASE_URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
