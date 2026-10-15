#!/usr/bin/env python3
"""Backup & restore drill for SynapseMemory (Phase 4 / remaining item 3).

Runnable locally for SQLite and in CI/ops for PostgreSQL. It turns the manual
procedure in ``docs/backup-recovery.md`` into an executable check that fails
loudly.

What it proves:
  1. A backup taken from a live store restores into a CLEAN target.
  2. Row counts match before/after (overall and per tenant).
  3. Content decrypts with the SAME key — key custody is part of the backup.
  4. A WRONG key fails safe (raises) instead of returning garbage.
  5. The ``schema_migrations`` ledger is present and at the current version.

Exit codes: 0 = all runnable checks passed, 1 = a check failed,
2 = nothing runnable in this environment (reported, not a silent pass).

Usage:
  python scripts/backup_restore_drill.py                 # SQLite drill
  python scripts/backup_restore_drill.py --postgres      # + Postgres (needs tools)
"""

import argparse
import os
import shutil
import sqlite3
# Operator-run drill: the subprocess calls below are intentional and reviewed.
# Justification for the blanket suppression markers below:
#   * argv lists with shell=False (never a shell string) — no shell injection;
#   * target_db is the hardcoded literal "synapse_drill_restore", and the DSNs
#     come from the operator's own --database-url/DATABASE_URL (the operator
#     already holds those credentials — this tool cannot elevate anything);
#   * client tools are resolved on PATH (verified via shutil.which first).
import subprocess  # nosec B404
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.join(ROOT, "sdk", "python")
if SDK not in sys.path:
    sys.path.insert(0, SDK)

from cryptography.fernet import Fernet  # noqa: E402
from synapse_memory.core.encryption import FernetEncryptionProvider  # noqa: E402
from synapse_memory.core.migrations import CURRENT_SCHEMA_VERSION  # noqa: E402
from synapse_memory.core.sqlite_db import SQLiteMemoryStore  # noqa: E402


def _memory(i, tenant):
    return {
        "id": "drill-" + str(i),
        "content": "drill record " + str(i) + " for " + tenant,
        "category": "general",
        "confidence": 0.9,
        "created_at": time.time(),
        "token_cost": 3,
        "embedding": [0.1, 0.2, 0.3],
        "tenant_id": tenant,
    }


def ledger_version(db_path):
    """Return the highest applied migration version in a SQLite file (0 if none)."""
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        except sqlite3.OperationalError:
            return 0  # ledger table not created yet
        return max(int(r[0]) for r in rows) if rows else 0
    finally:
        conn.close()


def sqlite_drill(workdir, keep=False):
    """Execute the full backup/restore drill against a throwaway SQLite store.

    Returns a dict of evidence. Raises AssertionError on any failed check.
    """
    key = Fernet.generate_key().decode()
    wrong = Fernet.generate_key().decode()
    src = os.path.join(workdir, "source.db")
    bak = os.path.join(workdir, "backup.db")
    dst = os.path.join(workdir, "restored.db")

    store = SQLiteMemoryStore(db_path=src, encryption_provider=FernetEncryptionProvider(key))
    tenants = ["acme", "globex"]
    for i in range(6):
        store.insert_memory(_memory(i, tenants[i % 2]))
    before = store.count_memories()
    store.close()
    assert before == 6, "expected 6 seeded rows, wrote " + str(before)

    # 1. Backup: copy the data file (WAL is checkpointed on close()).
    shutil.copy2(src, bak)
    assert os.path.getsize(bak) > 0, "backup file is empty"

    # 2. Restore into a CLEAN target.
    shutil.copy2(bak, dst)

    # 3. Reopen with the SAME key; counts + content must survive.
    restored = SQLiteMemoryStore(db_path=dst, encryption_provider=FernetEncryptionProvider(key))
    after = restored.count_memories()
    assert after == before, "row count mismatch: " + str(before) + " -> " + str(after)
    assert restored.count_memories(tenant_id="acme") == 3, "per-tenant count lost"
    assert restored.count_memories(tenant_id="globex") == 3, "per-tenant count lost"
    rec = restored.get_memory_by_id("drill-0", tenant_id="acme")
    assert rec is not None, "restored record missing"
    assert rec["content"] == "drill record 0 for acme", "content did not decrypt correctly"
    restored.close()

    # 4. Schema ledger present and current.
    version = ledger_version(dst)
    assert version == CURRENT_SCHEMA_VERSION, (
        "ledger v" + str(version) + " != current v" + str(CURRENT_SCHEMA_VERSION)
    )

    # 5. Wrong key must fail safe rather than silently return garbage.
    bad = SQLiteMemoryStore(db_path=dst, encryption_provider=FernetEncryptionProvider(wrong))
    failed_safe = False
    try:
        bad.get_memory_by_id("drill-0", tenant_id="acme")
    except Exception:
        failed_safe = True
    finally:
        bad.close()
    assert failed_safe, "wrong key did NOT fail safe (decryption should raise)"

    if not keep:
        shutil.rmtree(workdir, ignore_errors=True)

    return {"rows": before, "ledger": version, "tenants": {"acme": 3, "globex": 3}}


def _pg_tools_available():
    return all(shutil.which(t) for t in ("pg_dump", "pg_restore", "psql"))


def postgres_drill(database_url):
    """Full pg_dump -> pg_restore drill when client tools + a DSN are present.

    Returns True if it ran, False if it was skipped (reported, not a silent pass).
    """
    if not database_url:
        print("[postgres] SKIPPED: DATABASE_URL is not set.")
        return False
    if not _pg_tools_available():
        print("[postgres] SKIPPED: pg_dump/pg_restore/psql not on PATH. "
              "Install postgresql-client or run the CI pgvector job.")
        return False

    target_db = "synapse_drill_restore"
    head, _sep, _tail = database_url.rpartition("/")
    target_dsn = head + "/" + target_db
    tmp = tempfile.mkdtemp(prefix="synapse-pgdrill-")
    dump = os.path.join(tmp, "synapse.dump")
    try:
        print("[postgres] pg_dump -> " + dump)
        subprocess.run(["pg_dump", "-Fc", "-d", database_url, "-f", dump], check=True)  # nosec
        assert os.path.getsize(dump) > 0, "pg_dump produced an empty file"

        print("[postgres] recreating target database " + target_db)
        drop_sql = 'DROP DATABASE IF EXISTS "' + target_db + '";'
        create_sql = 'CREATE DATABASE "' + target_db + '";'
        subprocess.run(["psql", "-d", database_url, "-v", "ON_ERROR_STOP=1", "-c", drop_sql], check=True)  # nosec
        subprocess.run(["psql", "-d", database_url, "-v", "ON_ERROR_STOP=1", "-c", create_sql], check=True)  # nosec

        print("[postgres] pg_restore into " + target_db)
        subprocess.run(["pg_restore", "-d", target_dsn, dump], check=True)  # nosec

        try:
            import psycopg2  # type: ignore
        except Exception:
            print("[postgres] restored OK; psycopg2 not installed, skipping row/ledger verification.")
            return True

        src_conn = psycopg2.connect(database_url)
        dst_conn = psycopg2.connect(target_dsn)
        try:
            cur_src = src_conn.cursor()
            cur_dst = dst_conn.cursor()
            cur_src.execute("SELECT count(*) FROM memories;")
            cur_dst.execute("SELECT count(*) FROM memories;")
            src_rows = cur_src.fetchone()[0]
            dst_rows = cur_dst.fetchone()[0]
            assert src_rows == dst_rows, "row mismatch " + str(src_rows) + " -> " + str(dst_rows)
            cur_dst.execute("SELECT max(version) FROM schema_migrations;")
            ver = cur_dst.fetchone()[0]
            assert int(ver) == CURRENT_SCHEMA_VERSION, "restored ledger v" + str(ver)
        finally:
            src_conn.close()
            dst_conn.close()
        print("[postgres] restored rows=" + str(dst_rows) + " ledger=v" + str(ver))
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="SynapseMemory backup/restore drill")
    parser.add_argument("--postgres", action="store_true",
                        help="also run the PostgreSQL drill (needs pg_dump/pg_restore/psql)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the SQLite drill working directory for inspection")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""),
                        help="Postgres DSN (defaults to $DATABASE_URL)")
    args = parser.parse_args(argv)

    ran = 0
    print("== SynapseMemory backup/restore drill ==")
    workdir = tempfile.mkdtemp(prefix="synapse-drill-")
    try:
        evidence = sqlite_drill(workdir, keep=args.keep)
        ran += 1
        print("[sqlite] PASS rows=" + str(evidence["rows"]) + " ledger=v" + str(evidence["ledger"]))
    except AssertionError as exc:
        print("[sqlite] FAIL " + str(exc))
        return 1

    if args.postgres:
        try:
            if postgres_drill(args.database_url):
                ran += 1
        except subprocess.CalledProcessError as exc:
            print("[postgres] FAIL command exited " + str(exc.returncode))
            return 1
        except AssertionError as exc:
            print("[postgres] FAIL " + str(exc))
            return 1

    if ran == 0:
        print("No drill could run in this environment.")
        return 2
    print("Drill complete: " + str(ran) + " backend(s) verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
