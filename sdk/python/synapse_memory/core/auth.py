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
import threading
import time
from collections import defaultdict
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
    # Owner identity bound to this key (user id or service principal id).
    # Used to gate who may mint/rotate/revoke the key and what it may delegate.
    owner_id: Optional[str] = None


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


# Canonical role hierarchy: higher index = more privilege. A principal may only
# delegate down (or within) this hierarchy, never up, and never to a role whose
# index is higher than their own. Legacy role "admin" (pre-multi-user data)
# normalizes to workspace_admin unless bound to a system/org-root principal.
_ROLE_RANK = {"platform_admin": 5, "organization_admin": 4, "workspace_admin": 3, "developer": 2, "service": 2, "viewer": 1, "member": 1}

# Roles allowed to mint new API keys (v1 secure default). Members, viewers,
# developers and service keys cannot mint; only workspace admins and above.
_KEY_MINT_ROLES = frozenset({"platform_admin", "organization_admin", "workspace_admin"})

# Scopes a non-platform caller may delegate. "admin" is never delegable
# except by a platform_admin holding it.
_ADMIN_ONLY_SCOPES = frozenset({"admin", "security:audit"})


# --- Auth hot-path hardening (P2) ---
# Brief TTL for in-memory API-key lookups (seconds). A revoked/expired key can
# still validate until its cache entry expires or is evicted (revoke evicts).
_AUTH_KEY_CACHE_TTL = 30.0
# Flush buffered audit rows when the buffer reaches this size or this many
# seconds have elapsed since the last flush, whichever comes first.
_AUDIT_FLUSH_MAX = 50
_AUDIT_FLUSH_INTERVAL = 2.0
# IP-based rate limiting for unauthenticated attempts (per IP, sliding window).
_IP_RATE_WINDOW = 60.0
_IP_RATE_MAX_FAILURES = 15
# Max failed-lookup audit rows written per IP per window (caps log flooding).
_IP_MAX_FAILED_AUDITS = 5


def _role_rank(role: str) -> int:
    return _ROLE_RANK.get(role, 0)


def _can_delegate(role_a: str, role_b: str) -> Tuple[bool, str]:
    """Return (allowed, reason): caller with role_a may only delegate to role_b
    if role_b is at-or-below role_a in the hierarchy (equal allowed, higher
    denied). This enforces 'a workspace admin cannot mint a platform admin key'. """
    rank_a = _role_rank(role_a)
    rank_b = _role_rank(role_b)
    if rank_b > rank_a:
        return False, f"Caller role '{role_a}' cannot mint role '{role_b}'."
    if rank_b == 5 and role_a not in ("platform_admin",):
        return False, "Only platform_admin may mint platform-wide keys."
    return True, "Delegate check passed"


def normalize_role(principal: APIKeyRecord) -> str:
    """Resolve a key's stored role to its effective canonical role.

    System/org-root bindings resolve to platform_admin; a legacy stored role
    of "admin" outside a system binding normalizes to workspace_admin;
    every other known role passes through unchanged. Unknown roles map to
    "member" (least privilege) so a crafted role string can never escalate.
    """
    if principal.organization_id in ("org_root", "org_system") and principal.project_id in ("proj_root",):
        return "platform_admin"
    if principal.organization_id == "org_root":
        return "organization_admin"
    stored = (principal.role or "").strip()
    if stored == "admin":
        return "workspace_admin"
    if stored in _ROLE_RANK:
        return stored
    return "member"


def _role_from_scoped_key(principal: APIKeyRecord) -> str:
    # Backward-compatible alias: effective role always derives from the
    # stored role + binding, never defaults to workspace_admin.
    return normalize_role(principal)


def can_create_api_key(
    principal: APIKeyRecord,
    target_org: str,
    target_workspace: Optional[str],
    requested_role: str,
    requested_scopes: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Authorize a caller to mint a new API key.

    Rules:
      - A member/viewer/developer/service key cannot mint any key.
      - An organization admin can only mint keys inside its own organization,
        at or below organization_admin.
      - A workspace admin can only mint keys inside their own workspace and
        only at or below workspace_admin (never platform/org-wide).
      - A platform admin can mint anywhere, and is the ONLY role that may
        grant platform_admin or the admin/security:audit scopes.
      - The requested role must be grantable by the caller (delegation check).
    """
    # Resolve the caller's effective role from their key binding + stored role.
    caller_role = _role_from_scoped_key(principal)
    if caller_role not in _KEY_MINT_ROLES:
        return False, f"Role '{caller_role}' is not permitted to create API keys."
    allowed, reason = _can_delegate(caller_role, requested_role)
    if not allowed:
        return False, reason

    # The admin/security:audit scopes are never delegable by non-platform
    # callers, even if _can_delegate passed on role rank alone.
    if requested_scopes:
        leaked = [s for s in requested_scopes if s in _ADMIN_ONLY_SCOPES]
        if leaked and caller_role != "platform_admin":
            return False, f"Scope(s) {leaked} require platform_admin; caller is '{caller_role}'."

    # platform_admin is the only role that may mint across organizations.
    if caller_role == "platform_admin":
        return True, "Platform admin allowed."

    # A principal bound to org_root/proj_root acts as org admin for target_org.
    if principal.organization_id in ("org_root", "org_system") and principal.project_id in ("proj_root",):
        return True, f"Org admin for {target_org} allowed."

    # Otherwise the caller is bound to a concrete workspace; restrict to that org.
    if target_org != principal.organization_id:
        return False, f"Organization boundary violation. Key organization {target_org} not controlled by caller {principal.organization_id}."

    # Workspace admins and below may only mint into workspaces they control:
    # the same workspace they are bound to (plus org-scoped workspaces where the
    # caller is a workspace admin). For the v1 check, require exact workspace match.
    if target_workspace is not None and target_workspace != principal.tenant_id:
        return False, f"Workspace boundary violation. Caller bound to workspace {principal.tenant_id}, target {target_workspace}."

    return True, "Key mint permission granted."


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

        # Auth hot-path / abuse-protection state (P2). The gateway runs a single
        # shared SecurityManager instance, so this is shared across requests and
        # guarded by self._auth_lock.
        self._key_cache: Dict[str, Tuple[float, APIKeyRecord]] = {}
        self._audit_buffer: List[
            Tuple[str, float, Optional[str], Optional[str], str, str, str, Optional[str]]
        ] = []
        self._auth_lock = threading.Lock()
        self._last_audit_flush = 0.0
        self._ip_failures: Dict[str, List[float]] = defaultdict(list)
        self._ip_audited_failures: Dict[str, int] = defaultdict(int)

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
        # confirm the tables exist and are readable; DDL is never issued here and
        # the root-key bootstrap happens through an explicit admin setup procedure.
        if self.validate_schema_only:
            self._validate_auth_tables()
            self._verify_db_usable()
        else:
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
            try:
                # Roll back any aborted or still-open transaction so the
                # connection returned to the pool is clean for the next borrower.
                # Without this, a failed query leaves the connection in a
                # poisoned state (e.g. an aborted transaction) that breaks the
                # next request that borrows it.
                conn.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
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
                        last_used_at DOUBLE PRECISION,
                        owner_id TEXT
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
                        last_used_at REAL,
                        owner_id TEXT
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

    def _verify_db_usable(self) -> None:
        """Fail-closed verification that the PostgreSQL/authorization store is
        usable by this runtime role and is **not** a superuser/DDL role.

        Bootstrapping the engine against a schema it cannot read, or against a
        superuser that silently bypasses RLS, must fail clearly rather than
        silently degrading to an in-memory fallback.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT rolsuper, rolcanlogin FROM pg_roles WHERE rolname = current_user;"
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(
                    "Cannot resolve the current PostgreSQL role; authentication "
                    "store is unusable. Run the migration step as a DBA role first."
                )
            is_super, can_login = row
            if is_super:
                raise RuntimeError(
                    "Authentication store is connected as a superuser. The runtime "
                    "role must be a least-privilege non-superuser with no "
                    "BYPASSRLS. Do not run the engine as 'synapse_admin'."
                )
            if not can_login:
                raise RuntimeError(
                    "Authentication store role is not a login role. "
                    "Run the migration step as a DBA role first."
                )
            logger.info("Auth store verification passed: non-superuser, login role is usable.")
        except Exception as e:
            if self.fail_closed:
                raise RuntimeError(f"Auth store verification failed: {e}") from e
            logger.warning(f"Auth store verification warning (non-fail-closed): {e}")
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
        ttl_days: Optional[int] = 365,
        owner_id: Optional[str] = None,
    ) -> Tuple[str, APIKeyRecord]:
        """
        Generates a secure API key with prefix: syn_live_<key_id>_<secret>
        Only the SHA-256 hash is persisted. The full token is returned once.

        The role and scopes are taken verbatim from the authorized caller: they are
        NOT upgraded automatically, and a caller can never mint an 'admin' key by
        submitting role='admin' or including 'admin' in scopes by accident. The
        caller's own role (see _can_delegate) is validated by the route that
        invokes this method.
        """
        key_id = f"key_{secrets.token_hex(6)}"
        secret_part = secrets.token_urlsafe(32)
        full_token = f"syn_live_{key_id}_{secret_part}"
        key_hash = self.hash_secret(full_token)

        now = time.time()
        expires_at = (now + (ttl_days * 86400)) if ttl_days else None
        # NEVER auto-promote 'admin': the role/scopes above are authoritative and
        # come from the authenticated caller, so submitting role='admin' yields an
        # admin-scoped key only if that caller is actually allowed to delegate it.
        scopes_list = scopes or ["memories:read", "memories:write"]

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
            last_used_at=None,
            owner_id=owner_id,
        )

        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sql = self._format_sql("""
                INSERT INTO api_keys (
                    key_id, key_hash, name, organization_id, project_id,
                    tenant_id, scopes, role, is_active, expires_at,
                    created_at, last_used_at, owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                record.last_used_at,
                record.owner_id,
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

    def _env_master_revoked_or_expired(self, env_token: str) -> Optional[str]:
        """Honor DB revocation/expiry for the env master key hash, if present.

        Returns the DENIED_* outcome string when a matching api_keys row is
        revoked or expired, else None (env master still valid).
        """
        try:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                sql = self._format_sql("SELECT is_active, expires_at FROM api_keys WHERE key_hash = ?")
                cur.execute(sql, (self.hash_secret(env_token),))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row) if not isinstance(row, (tuple, list)) else {"is_active": row[0], "expires_at": row[1]}
                if not data.get("is_active"):
                    return "DENIED_REVOKED"
                if data.get("expires_at") and time.time() > float(data["expires_at"]):
                    return "DENIED_EXPIRED"
                return None
            finally:
                self._return_connection(conn)
        except Exception as e:
            logger.warning(f"Env-master revocation check failed (fail-open to DB auth): {e}")
            return None

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

        Successful lookups are cached briefly (_AUTH_KEY_CACHE_TTL) so a hot key
        does not hit the database, the last_used_at write, or the audit sink on
        every request. Revoked keys are evicted from the cache by revoke_api_key.
        """
        if not token:
            self._log_audit(None, requested_tenant_id, route, action, "DENIED_MISSING_TOKEN", client_ip)
            return False, None, "Missing API token"

        # Rate limit unauthenticated attempts by IP before any database/auth work.
        # A flood of invalid keys is rejected here instead of reaching the keys
        # table or writing an audit row per attempt.
        if client_ip and self._is_ip_rate_limited(client_ip):
            return False, None, "Too many failed attempts. Try again later."

        # Environment master-key fallback. This is an explicit bootstrap
        # credential, not a permanent bypass: it is only honored when the
        # caller explicitly opts in via SYNAPSE_ALLOW_ENV_MASTER_KEY=true, it
        # is rejected when a DB row with the same hash is revoked/expired,
        # and every use is audit-logged. Prefer DB-backed keys with an owner,
        # expiry and revocation state; disable the env master in production.
        expected_env_key = os.environ.get("SYNAPSE_API_KEY")
        allow_env_master = os.environ.get("SYNAPSE_ALLOW_ENV_MASTER_KEY", "false").lower() == "true"
        if expected_env_key and allow_env_master and hmac.compare_digest(token.encode("utf-8"), expected_env_key.encode("utf-8")):
            revoked_or_expired = self._env_master_revoked_or_expired(expected_env_key)
            if revoked_or_expired is not None:
                self._log_audit("key_env_master", requested_tenant_id, route, action, revoked_or_expired, client_ip)
                return False, None, "Environment master key is revoked or expired"
            if required_scope and required_scope not in ("admin", "memories:read", "memories:write", "memories:delete", "security:audit"):
                self._log_audit("key_env_master", requested_tenant_id, route, action, f"DENIED_INSUFFICIENT_SCOPE:{required_scope}", client_ip)
                return False, None, f"Insufficient scopes. Required: {required_scope}"
            admin_record = APIKeyRecord(
                key_id="key_env_master",
                key_hash=self.hash_secret(token),
                name="Environment Master Key",
                organization_id="org_system",
                project_id="proj_system",
                tenant_id=requested_tenant_id or "default",
                scopes=["admin", "memories:read", "memories:write", "memories:delete", "security:audit"],
                role="platform_admin",
                is_active=True,
                expires_at=None,
                created_at=time.time(),
                last_used_at=time.time()
            )
            self._log_audit(admin_record.key_id, requested_tenant_id, route, action, "ALLOWED", client_ip)
            return True, admin_record, "Authenticated"

        token_hash = self.hash_secret(token)
        now = time.time()

        # Brief key-lookup cache: skip the DB, the last_used_at write and the
        # audit write on a cache hit for an active, non-expired key. Scope and
        # tenant checks are still enforced in memory so a cached key can never
        # bypass authorization.
        cached = self._get_cached_key(token_hash, now)
        if cached is not None:
            record = cached
            if record.expires_at and now > record.expires_at:
                self._evict_cached_key(record.key_id)
                self._log_audit(record.key_id, requested_tenant_id, route, action, "DENIED_EXPIRED", client_ip)
                return False, record, "API token has expired"
            if required_scope:
                if "admin" not in record.scopes and required_scope not in record.scopes:
                    self._log_audit(
                        record.key_id, requested_tenant_id, route, action,
                        f"DENIED_INSUFFICIENT_SCOPE:{required_scope}", client_ip
                    )
                    return False, record, f"Insufficient scopes. Required: {required_scope}"
            if requested_tenant_id and record.role != "admin" and "admin" not in record.scopes:
                if record.tenant_id != requested_tenant_id and record.tenant_id != "*":
                    self._log_audit(
                        record.key_id, requested_tenant_id, route, action,
                        "DENIED_TENANT_MISMATCH", client_ip
                    )
                    return False, record, f"Tenant authorization boundary violation. Key bound to: {record.tenant_id}"
            self._log_audit(record.key_id, requested_tenant_id or record.tenant_id, route, action, "ALLOWED", client_ip)
            return True, record, "Authenticated"

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
                # Invalid key: count the failure for IP rate limiting and cap
                # audit writes so a flood of bad keys does not flood the log.
                if client_ip:
                    self._record_ip_failure(client_ip)
                    if self._is_ip_rate_limited(client_ip):
                        return False, None, "Too many failed attempts. Try again later."
                if self._should_audit_failure(client_ip):
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

            # Cache the validated record briefly; persist last_used_at once on
            # this (cache-miss) load. Cache hits skip both the DB and this write.
            self._cache_key(token_hash, now, record)
            upd_sql = self._format_sql("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?")
            cur.execute(upd_sql, (now, record.key_id))
            conn.commit()

            self._log_audit(record.key_id, requested_tenant_id or record.tenant_id, route, action, "ALLOWED", client_ip)
            return True, record, "Authenticated"
        finally:
            self._return_connection(conn)

    def _get_cached_key(self, token_hash: str, now: float) -> Optional[APIKeyRecord]:
        """Return a cached, still-valid key record, or None on cache miss.

        Entries past their TTL are evicted. Revoked keys are evicted by
        revoke_api_key so revocation is honored immediately.
        """
        with self._auth_lock:
            entry = self._key_cache.get(token_hash)
            if entry is None:
                return None
            expires_at, record = entry
            if now > expires_at:
                del self._key_cache[token_hash]
                return None
            return record

    def _cache_key(self, token_hash: str, now: float, record: APIKeyRecord) -> None:
        with self._auth_lock:
            self._key_cache[token_hash] = (now + _AUTH_KEY_CACHE_TTL, record)

    def _evict_cached_key(self, key_id: str) -> None:
        with self._auth_lock:
            for token_hash, (_expires_at, record) in list(self._key_cache.items()):
                if record.key_id == key_id:
                    del self._key_cache[token_hash]

    def _record_ip_failure(self, client_ip: str) -> None:
        with self._auth_lock:
            self._ip_failures[client_ip].append(time.time())

    def _is_ip_rate_limited(self, client_ip: str) -> bool:
        """Sliding-window per-IP failure count. True = deny before auth/DB."""
        with self._auth_lock:
            now = time.time()
            window_start = now - _IP_RATE_WINDOW
            hits = [t for t in self._ip_failures.get(client_ip, []) if t >= window_start]
            self._ip_failures[client_ip] = hits
            return len(hits) >= _IP_RATE_MAX_FAILURES

    def _should_audit_failure(self, client_ip: Optional[str]) -> bool:
        """Cap audit writes for failed lookups so a flood of bad keys cannot
        flood the audit log. Returns True if this failure should be audited."""
        if client_ip is None:
            return True
        with self._auth_lock:
            if self._ip_audited_failures[client_ip] >= _IP_MAX_FAILED_AUDITS:
                return False
            self._ip_audited_failures[client_ip] += 1
            return True

    def revoke_api_key(self, key_id: str) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sql = self._format_sql("UPDATE api_keys SET is_active = 0 WHERE key_id = ?")
            cur.execute(sql, (key_id,))
            conn.commit()
            revoked = cur.rowcount > 0
            if revoked:
                # Evict from the in-memory key cache so a revoked key is denied
                # immediately instead of living out its cache TTL.
                self._evict_cached_key(key_id)
            return revoked
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
        """Buffer an audit row. Rows are flushed in batches (one connection,
        one transaction) so the auth hot path never opens a dedicated
        connection per audit event. Never raises."""
        record = (
            f"audit_{secrets.token_hex(8)}",
            time.time(),
            key_id,
            tenant_id,
            route,
            action,
            outcome,
            client_ip,
        )
        with self._auth_lock:
            self._audit_buffer.append(record)
            if (
                len(self._audit_buffer) >= _AUDIT_FLUSH_MAX
                or (time.time() - self._last_audit_flush) >= _AUDIT_FLUSH_INTERVAL
            ):
                self._flush_audit_buffer_locked()

    def _flush_audit_buffer_locked(self) -> None:
        """Write all buffered audit rows in a single connection/transaction.

        Must be called with self._auth_lock held. On failure the batch is put
        back at the head of the buffer so a later flush retries it (audit
        evidence is never silently dropped).
        """
        if not self._audit_buffer:
            return
        batch = self._audit_buffer
        self._audit_buffer = []
        self._last_audit_flush = time.time()
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sql = self._format_sql(
                "INSERT INTO auth_audit_logs (id, timestamp, key_id, tenant_id, route, action, outcome, client_ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            )
            cur.executemany(sql, batch)
            conn.commit()
        except Exception as e:
            self._audit_buffer = batch + self._audit_buffer
            logger.warning(f"Failed to flush auth audit batch ({len(batch)} rows): {e}")
        finally:
            self._return_connection(conn)

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
