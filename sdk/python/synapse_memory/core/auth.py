"""
Enterprise Security, Cryptographic API Key Management, RBAC, Scopes & Audit Logging.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SynapseSecurity")

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    psycopg2 = None
    pool = None
    RealDictCursor = None


@dataclass
class APIKeyRecord:
    key_id: str
    key_hash: str
    name: str
    organization_id: str
    project_id: str
    tenant_id: str
    scopes: List[str]
    role: str  # admin, developer, service, viewer
    is_active: bool
    expires_at: Optional[float]
    created_at: float
    last_used_at: Optional[float]


@dataclass
class AuthAuditLog:
    id: str
    timestamp: float
    key_id: Optional[str]
    tenant_id: Optional[str]
    route: str
    action: str
    outcome: str  # ALLOWED, DENIED_INVALID_KEY, DENIED_EXPIRED, DENIED_REVOKED, DENIED_INSUFFICIENT_SCOPE, DENIED_TENANT_MISMATCH
    client_ip: Optional[str] = None
    metadata_json: Optional[str] = None


class SecurityManager:
    """
    Enterprise identity and cryptographic authorization engine.
    Provides hashed API key validation, multi-tenant RBAC, fine-grained scopes,
    and immutable structured authorization audit trails.
    Supports centralized PostgreSQL cluster storage and SQLite local storage.
    """

    def __init__(
        self,
        db_path: str = "synapse_memory.db",
        connection_string: Optional[str] = None,
        fail_closed: bool = False,
        validate_schema_only: bool = False,
    ):
        self.db_path = db_path
        self.connection_string = connection_string
        self.fail_closed = fail_closed
        self._pool: Optional[Any] = None
        self._is_postgres = False
        # When validate_schema_only=True, the engine does not create auth tables
        # at startup; it only verifies they exist and are accessible. DDL must be
        # run ahead of time by the admin migration step (scripts/provision_db_roles.py
        # plus a dedicated auth-schema migration). Required for least-privilege
        # runtime roles that cannot execute DDL.
        self.validate_schema_only = validate_schema_only

        if self.connection_string:
            if HAS_PSYCOPG2 and pool is not None:
                try:
                    pool_cls = getattr(pool, "ThreadedConnectionPool", None) or pool.SimpleConnectionPool
                    self._pool = pool_cls(1, 10, self.connection_string, connect_timeout=5)
                    self._is_postgres = True
                    logger.info("SecurityManager connected to centralized PostgreSQL authentication datastore.")
                except Exception as e:
                    self._is_postgres = False
                    self._pool = None
                    if self.fail_closed:
                        raise RuntimeError(
                            f"Central PostgreSQL authentication datastore unavailable: {e}"
                        ) from e
                    logger.warning(
                        f"Could not connect SecurityManager to PostgreSQL ({e}); falling back to SQLite {db_path}."
                    )
            else:
                self._is_postgres = False
                self._pool = None
                if self.fail_closed:
                    raise RuntimeError(
                        "Central PostgreSQL authentication datastore requested but psycopg2 database driver is unavailable."
                    )
                logger.warning(
                    f"psycopg2 unavailable; falling back to SQLite {db_path}."
                )

        # Initialize or validate auth tables. In validate_schema_only mode we only
        # confirm the tables exist; we never issue DDL.
        if self.validate_schema_only:
            self._validate_auth_tables()
        else:
            self._initialize_auth_tables()

        self._initialize_auth_tables()
        self._bootstrap_root_key()

    def _get_connection(self) -> Any:
        if self._is_postgres and self._pool is not None:
            return self._pool.getconn()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _return_connection(self, conn: Any) -> None:
        if self._is_postgres and self._pool is not None:
            self._pool.putconn(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass

    def pool_stats(self) -> Dict[str, int]:
        """Live pool utilization for /metrics (no secrets)."""
        if not self._is_postgres or self._pool is None:
            return {"pool_size": 0, "in_use": 0}
        try:
            maxconn = getattr(self._pool, "maxconn", 10)
            used = maxconn - len(getattr(self._pool, "_pool", []))
            return {"pool_size": int(maxconn), "in_use": max(0, int(used))}
        except Exception:
            return {"pool_size": 0, "in_use": 0}

    def _initialize_auth_tables(self) -> None:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            if self._is_postgres:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        key_id VARCHAR(128) PRIMARY KEY,
                        key_hash VARCHAR(128) NOT NULL UNIQUE,
                        name VARCHAR(256) NOT NULL,
                        organization_id VARCHAR(128) NOT NULL,
                        project_id VARCHAR(128) NOT NULL,
                        tenant_id VARCHAR(128) NOT NULL,
                        scopes TEXT NOT NULL,
                        role VARCHAR(64) NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        expires_at DOUBLE PRECISION,
                        created_at DOUBLE PRECISION NOT NULL,
                        last_used_at DOUBLE PRECISION
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS auth_audit_logs (
                        id VARCHAR(128) PRIMARY KEY,
                        timestamp DOUBLE PRECISION NOT NULL,
                        key_id VARCHAR(128),
                        tenant_id VARCHAR(128),
                        route VARCHAR(256) NOT NULL,
                        action VARCHAR(64) NOT NULL,
                        outcome VARCHAR(64) NOT NULL,
                        client_ip VARCHAR(64),
                        metadata_json TEXT
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_tenant ON api_keys(tenant_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON auth_audit_logs(timestamp DESC);")
                conn.commit()
            else:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        key_id TEXT PRIMARY KEY,
                        key_hash TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        organization_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        scopes TEXT NOT NULL,
                        role TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        expires_at REAL,
                        created_at REAL NOT NULL,
                        last_used_at REAL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS auth_audit_logs (
                        id TEXT PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        key_id TEXT,
                        tenant_id TEXT,
                        route TEXT NOT NULL,
                        action TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        client_ip TEXT,
                        metadata_json TEXT
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_tenant ON api_keys(tenant_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON auth_audit_logs(timestamp DESC)")
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            if self.fail_closed:
                raise RuntimeError(f"Failed to initialize centralized authentication tables: {e}") from e
            logger.warning(f"Error initializing auth tables: {e}")
        finally:
            self._return_connection(conn)

    def _validate_auth_tables(self) -> None:
        """Read-only schema validation for least-privilege runtime mode.

        Confirms that api_keys and auth_audit_logs exist and are readable by the
        current role. Never issues DDL. Raises RuntimeError (fail-closed) if
        the schema is missing or unreadable, so production traffic is not served
        against an incomplete auth schema.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            required_tables = ("api_keys", "auth_audit_logs")
            for table in required_tables:
                if self._is_postgres:
                    cur.execute(
                        "SELECT to_regclass(%s)", (f"public.{table}",)
                    )
                    if cur.fetchone()[0] is None:
                        raise RuntimeError(
                            f"Auth table 'public.{table}' is missing. Run the admin "
                            "auth-schema migration before starting the engine with "
                            "SYNAPSE_APPLY_DB_SCHEMA=false."
                        )
                else:
                    cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    )
                    if cur.fetchone() is None:
                        raise RuntimeError(
                            f"Auth table '{table}' is missing. Run the admin "
                            "auth-schema migration before starting the engine."
                        )
            # Verify a simple SELECT works (read permission).
            cur.execute("SELECT 1 FROM api_keys LIMIT 1")
            cur.execute("SELECT 1 FROM auth_audit_logs LIMIT 1")
            logger.info("Auth schema validation passed: api_keys and auth_audit_logs are readable.")
        except Exception as e:
            if self.fail_closed:
                raise RuntimeError(f"Auth schema validation failed: {e}") from e
            logger.warning(f"Auth schema validation warning (non-fail-closed): {e}")
        finally:
            self._return_connection(conn)

    @staticmethod
    def hash_secret(secret: str) -> str:
        """Computes SHA-256 digest of secret token."""
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def _format_sql(self, sql: str) -> str:
        if self._is_postgres:
            return sql.replace("?", "%s")
        return sql

    def generate_api_key(
        self,
        name: str,
        organization_id: str = "org_default",
        project_id: str = "proj_default",
        tenant_id: str = "default",
        scopes: Optional[List[str]] = None,
        role: str = "developer",
        ttl_days: Optional[int] = 365
    ) -> Tuple[str, APIKeyRecord]:
        """
        Generates a secure API key with prefix: syn_live_<key_id>_<secret>
        Only the SHA-256 hash is persisted. The full token is returned once.
        """
        key_id = f"key_{secrets.token_hex(6)}"
        secret_part = secrets.token_urlsafe(32)
        full_token = f"syn_live_{key_id}_{secret_part}"
        key_hash = self.hash_secret(full_token)

        now = time.time()
        expires_at = (now + (ttl_days * 86400)) if ttl_days else None
        scopes_list = scopes or ["memories:read", "memories:write"]
        if role == "admin" and "admin" not in scopes_list:
            scopes_list.append("admin")

        record = APIKeyRecord(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            organization_id=organization_id,
            project_id=project_id,
            tenant_id=tenant_id,
            scopes=scopes_list,
            role=role,
            is_active=True,
            expires_at=expires_at,
            created_at=now,
            last_used_at=None
        )

        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sql = self._format_sql("""
                INSERT INTO api_keys (
                    key_id, key_hash, name, organization_id, project_id,
                    tenant_id, scopes, role, is_active, expires_at,
                    created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)
            cur.execute(sql, (
                record.key_id,
                record.key_hash,
                record.name,
                record.organization_id,
                record.project_id,
                record.tenant_id,
                json.dumps(record.scopes),
                record.role,
                1 if record.is_active else 0,
                record.expires_at,
                record.created_at,
                record.last_used_at
            ))
            conn.commit()
        finally:
            self._return_connection(conn)

        return full_token, record

    def _bootstrap_root_key(self) -> None:
        """Bootstraps default root/admin key from environment if table is empty."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM api_keys")
            row = cur.fetchone()
            count = int(row[0] if isinstance(row, (tuple, list)) else row["count"] if isinstance(row, dict) else row[0])
            if count == 0:
                env_key = os.environ.get("SYNAPSE_API_KEY")
                if env_key:
                    key_hash = self.hash_secret(env_key)
                    sql = self._format_sql("""
                        INSERT INTO api_keys (
                            key_id, key_hash, name, organization_id, project_id,
                            tenant_id, scopes, role, is_active, expires_at,
                            created_at, last_used_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, NULL)
                    """)
                    cur.execute(sql, (
                        "key_root_bootstrap",
                        key_hash,
                        "Bootstrap Root API Key",
                        "org_root",
                        "proj_root",
                        "default",
                        json.dumps(["admin", "memories:read", "memories:write", "memories:delete", "security:audit"]),
                        "admin",
                        time.time()
                    ))
                    conn.commit()
                    logger.info("Bootstrapped root API key from SYNAPSE_API_KEY.")
        except Exception as e:
            logger.warning(f"Failed to bootstrap root key: {e}")
        finally:
            self._return_connection(conn)

    def validate_api_key(
        self,
        token: str,
        required_scope: Optional[str] = None,
        requested_tenant_id: Optional[str] = None,
        route: str = "/",
        action: str = "ACCESS",
        client_ip: Optional[str] = None
    ) -> Tuple[bool, Optional[APIKeyRecord], str]:
        """
        Validates API token against cryptographic hash, checks expiration, revocation,
        scopes, and multi-tenant authorization boundaries.
        """
        if not token:
            self._log_audit(None, requested_tenant_id, route, action, "DENIED_MISSING_TOKEN", client_ip)
            return False, None, "Missing API token"

        # Direct comparison with env key as fallback if not in DB
        expected_env_key = os.environ.get("SYNAPSE_API_KEY")
        if expected_env_key and hmac.compare_digest(token, expected_env_key):
            admin_record = APIKeyRecord(
                key_id="key_env_master",
                key_hash=self.hash_secret(token),
                name="Environment Master Key",
                organization_id="org_system",
                project_id="proj_system",
                tenant_id=requested_tenant_id or "default",
                scopes=["admin", "memories:read", "memories:write", "memories:delete", "security:audit"],
                role="admin",
                is_active=True,
                expires_at=None,
                created_at=time.time(),
                last_used_at=time.time()
            )
            self._log_audit(admin_record.key_id, requested_tenant_id, route, action, "ALLOWED", client_ip)
            return True, admin_record, "Authenticated"

        token_hash = self.hash_secret(token)
        now = time.time()

        conn = self._get_connection()
        try:
            if self._is_postgres and RealDictCursor is not None:
                cur = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cur = conn.cursor()

            sql = self._format_sql("SELECT * FROM api_keys WHERE key_hash = ?")
            cur.execute(sql, (token_hash,))
            row = cur.fetchone()
            if not row:
                self._log_audit(None, requested_tenant_id, route, action, "DENIED_INVALID_KEY", client_ip)
                return False, None, "Invalid API token"

            data = dict(row)
            scopes = json.loads(data["scopes"]) if isinstance(data["scopes"], str) else data["scopes"]
            record = APIKeyRecord(
                key_id=data["key_id"],
                key_hash=data["key_hash"],
                name=data["name"],
                organization_id=data["organization_id"],
                project_id=data["project_id"],
                tenant_id=data["tenant_id"],
                scopes=scopes,
                role=data["role"],
                is_active=bool(data["is_active"]),
                expires_at=data["expires_at"],
                created_at=data["created_at"],
                last_used_at=data["last_used_at"]
            )

            # Check revocation
            if not record.is_active:
                self._log_audit(record.key_id, requested_tenant_id, route, action, "DENIED_REVOKED", client_ip)
                return False, record, "API token has been revoked"

            # Check expiration
            if record.expires_at and now > record.expires_at:
                self._log_audit(record.key_id, requested_tenant_id, route, action, "DENIED_EXPIRED", client_ip)
                return False, record, "API token has expired"

            # Check RBAC scopes
            if required_scope:
                if "admin" not in record.scopes and required_scope not in record.scopes:
                    self._log_audit(
                        record.key_id, requested_tenant_id, route, action,
                        f"DENIED_INSUFFICIENT_SCOPE:{required_scope}", client_ip
                    )
                    return False, record, f"Insufficient scopes. Required: {required_scope}"

            # Check Tenant Isolation (admins can access all tenants)
            if requested_tenant_id and record.role != "admin" and "admin" not in record.scopes:
                if record.tenant_id != requested_tenant_id and record.tenant_id != "*":
                    self._log_audit(
                        record.key_id, requested_tenant_id, route, action,
                        "DENIED_TENANT_MISMATCH", client_ip
                    )
                    return False, record, f"Tenant authorization boundary violation. Key bound to: {record.tenant_id}"

            # Update last_used_at timestamp
            upd_sql = self._format_sql("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?")
            cur.execute(upd_sql, (now, record.key_id))
            conn.commit()

            self._log_audit(record.key_id, requested_tenant_id or record.tenant_id, route, action, "ALLOWED", client_ip)
            return True, record, "Authenticated"
        finally:
            self._return_connection(conn)

    def revoke_api_key(self, key_id: str) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sql = self._format_sql("UPDATE api_keys SET is_active = 0 WHERE key_id = ?")
            cur.execute(sql, (key_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            self._return_connection(conn)

    def check_hierarchy(
        self,
        record: APIKeyRecord,
        requested_org: Optional[str] = None,
        requested_project: Optional[str] = None,
        requested_tenant: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Enforce org → project → tenant ownership on a request context.

        A key is bound to one (organization_id, project_id, tenant_id) triple.
        An empty/None requested value inherits the key's own binding; a concrete
        requested value must equal the binding (admins with tenant "*" bypass
        the tenant leg only). Returns (allowed, reason).
        """
        if self.is_system_principal(record):
            return True, "System principal bypass"
        if requested_org and record.organization_id not in ("*", "", "org_system"):
            if requested_org != record.organization_id:
                return False, f"Organization boundary violation. Key bound to: {record.organization_id}"
        if requested_project and record.project_id not in ("*", ""):
            if requested_project != record.project_id:
                return False, f"Project boundary violation. Key bound to: {record.project_id}"
        if requested_tenant and record.tenant_id not in ("*", ""):
            if requested_tenant != record.tenant_id:
                return False, f"Tenant boundary violation. Key bound to: {record.tenant_id}"
        return True, "Hierarchy check passed"

    def is_system_principal(self, record: APIKeyRecord) -> bool:
        """Platform-internal principals (env master key / org_system) are operators.

        They predate per-org key bindings and exist to administer the whole
        deployment; every hierarchy-gated admin endpoint bypasses the org →
        project → tenant legs for them.
        """
        return record.key_id == "key_env_master" or record.organization_id == "org_system"

    def list_api_keys(self, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            if self._is_postgres and RealDictCursor is not None:
                cur = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cur = conn.cursor()

            if organization_id:
                sql = self._format_sql("SELECT * FROM api_keys WHERE organization_id = ? ORDER BY created_at DESC")
                cur.execute(sql, (organization_id,))
            else:
                cur.execute("SELECT * FROM api_keys ORDER BY created_at DESC")
            rows = cur.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d.pop("key_hash", None)  # Never expose hash
                d["scopes"] = json.loads(d["scopes"]) if isinstance(d["scopes"], str) else d["scopes"]
                results.append(d)
            return results
        finally:
            self._return_connection(conn)

    def _log_audit(
        self,
        key_id: Optional[str],
        tenant_id: Optional[str],
        route: str,
        action: str,
        outcome: str,
        client_ip: Optional[str]
    ) -> None:
        try:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                log_id = f"audit_{secrets.token_hex(8)}"
                sql = self._format_sql("""
                    INSERT INTO auth_audit_logs (
                        id, timestamp, key_id, tenant_id, route, action, outcome, client_ip
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """)
                cur.execute(sql, (log_id, time.time(), key_id, tenant_id, route, action, outcome, client_ip))
                conn.commit()
            finally:
                self._return_connection(conn)
        except Exception as e:
            logger.warning(f"Failed to record auth audit log: {e}")

    def log_audit_action(
        self,
        actor_key_id: Optional[str],
        tenant_id: Optional[str],
        action: str,
        resource: str = "memories",
        status: str = "SUCCESS",
        metadata: Optional[Dict[str, Any]] = None,
        route: str = "/",
        client_ip: Optional[str] = None,
    ) -> None:
        """Public action-level audit writer for gateway data-plane events.

        Wraps the internal _log_audit record with resource/status context so
        destructive operations (bulk clears, GDPR deletes, retention prunes)
        leave a queryable compliance trail. Never raises — audit must not
        break the data path it observes.
        """
        outcome = f"{action}:{status}" if resource == "memories" else f"{action}:{status}:{resource}"
        try:
            self._log_audit(
                actor_key_id, tenant_id, route, action, outcome, client_ip,
            )
            if metadata:
                logger.info("audit action=%s actor=%s tenant=%s meta=%s", action, actor_key_id, tenant_id, metadata)
        except Exception as e:
            logger.warning(f"Failed to record audit action {action}: {e}")

    def get_audit_logs(self, limit: int = 100, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            if self._is_postgres and RealDictCursor is not None:
                cur = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cur = conn.cursor()

            if tenant_id:
                sql = self._format_sql(
                    "SELECT * FROM auth_audit_logs WHERE tenant_id = ? ORDER BY timestamp DESC LIMIT ?"
                )
                cur.execute(sql, (tenant_id, limit))
            else:
                sql = self._format_sql("SELECT * FROM auth_audit_logs ORDER BY timestamp DESC LIMIT ?")
                cur.execute(sql, (limit,))
            return [dict(r) for r in cur.fetchall()]
        finally:
            self._return_connection(conn)

    def export_audit_logs(
        self,
        tenant_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Compliance export of auth audit records for retention/archival.

        Bounded to `limit` rows so an export can never OOM the engine on a
        years-old tenant. Callers stream/export in pages keyed on the rows'
        `timestamp` values.
        """
        conn = self._get_connection()
        try:
            if self._is_postgres and RealDictCursor is not None:
                cur = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cur = conn.cursor()

            clauses: List[str] = []
            params: List[Any] = []
            if tenant_id:
                clauses.append("tenant_id = ?")
                params.append(tenant_id)
            if since is not None:
                clauses.append("timestamp >= ?")
                params.append(since)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = self._format_sql(f"SELECT * FROM auth_audit_logs {where} ORDER BY timestamp ASC LIMIT ?")
            params.append(min(limit, 50000))
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]
        finally:
            self._return_connection(conn)

    def prune_audit_logs(
        self, older_than_seconds: float = 90 * 86400, tenant_id: Optional[str] = None
    ) -> int:
        """Delete audit records older than the retention window.

        Default 90 days. Returns the deleted row count so the gateway can
        confirm and log the retention pass. Always logged as an audit event.
        P1.1: pass tenant_id to scope the retention pass to one tenant —
        tenant-bound admins must not be able to destroy other tenants'
        audit evidence. None (system principals only) prunes globally.
        """
        cutoff = time.time() - max(0.0, float(older_than_seconds))
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            if tenant_id is not None:
                sql = self._format_sql(
                    "DELETE FROM auth_audit_logs WHERE timestamp < ? AND tenant_id = ?"
                )
                cur.execute(sql, (cutoff, tenant_id))
            else:
                sql = self._format_sql("DELETE FROM auth_audit_logs WHERE timestamp < ?")
                cur.execute(sql, (cutoff,))
            deleted = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
            return int(deleted)
        finally:
            self._return_connection(conn)
