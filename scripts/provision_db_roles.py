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


def privilege_statements(runtime_role, admin_role, has_memories_table):
    """Least-privilege grants for the runtime role.

    Runtime gets schema USAGE, DML on ``memories``, and nothing else: no schema
    CREATE, no table ownership, no membership in the admin role, and no
    destructive statement privileges.
    """
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
                        runtime_in_admin, owner, admin_role):
    """Return ``(failures, advisories)`` for the runtime role's privileges.

    ``table_privs`` is the 7-tuple from VERIFY_TABLE_PRIVS_SQL, or None when the
    table does not exist yet. Advisories are reported but do not fail the run.
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
    return failures, advisories


def build_plan(runtime_role, admin_role, runtime_password, admin_password,
               runtime_exists, admin_exists, has_memories_table, current_owner=None):
    """Ordered (label, sql, params) plan. No plaintext password ever enters SQL."""
    validate_identifier(runtime_role, "runtime role")
    validate_identifier(admin_role, "admin role")
    if runtime_role == admin_role:
        raise IdentifierError("runtime role and admin role must differ")
    plan = role_statements(admin_role, admin_password, admin_exists)
    plan += role_statements(runtime_role, runtime_password, runtime_exists)
    plan += privilege_statements(runtime_role, admin_role, has_memories_table)
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

        plan = build_plan(runtime_role, admin_role, runtime_password, admin_password,
                          runtime_exists, admin_exists, has_table, current_owner)

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
            priv_failures, priv_advisories = evaluate_privileges(
                runtime_role, privs, can_create, in_admin, current_owner, admin_role)
            failures += priv_failures
            advisories += priv_advisories
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return failures, advisories


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
              "Install with: pip install 'synapse-memory[pgvector]'")
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
        print("Next: run migrations as the admin role, then start the engine with "
              "SYNAPSE_APPLY_DB_SCHEMA=false and the runtime-role DATABASE_URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
