"""Pytest configuration for the Synapse-Memory test suite.

Sets a deterministic, isolated environment BEFORE any test module imports
`synapse_memory.api.fastapi_server` (which reads env vars at import time).
This guarantees the suite passes identically on a fresh clone, in CI, and on a
developer machine with leftover local state — no manual env setup required.
"""
import os
import sys
import tempfile

# Make the SDK importable regardless of how pytest is invoked.
_SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)

# Deterministic encryption key (matches CI). Prevents cryptography.InvalidToken
# from stale local DBs that were encrypted under a different auto-generated key.
os.environ.setdefault(
    "SYNAPSE_ENCRYPTION_KEY",
    "2N53Wk7u2V5bK1wU_1F5Y9mZ7zN53Wk7u2V5bK1wU_0=",
)

# Configure a root API key so the auth gateway treats anonymous requests as
# UNAUTHORIZED — matching the production/CI security posture that the auth
# tests assert against.
os.environ.setdefault("SYNAPSE_API_KEY", "syn_live_test_key")

# Isolate the gateway's SQLite state into a per-run temp file instead of the
# repo-root synapse_memory.db, so tests never mutate or read real local data.
if not os.environ.get("SYNAPSE_SQLITE_DB_PATH"):
    os.environ["SYNAPSE_SQLITE_DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"synapse_test_gateway_{os.getpid()}.db"
    )

# Clean up any stray test SQLite artifacts from previous runs at the repo root
# so a dirty working tree can never influence this run.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _stale in ("test_synapse_core_suite.db", "test_integration.db", "synapse_memory.db"):
    for _suffix in ("", "-wal", "-shm"):
        _path = os.path.join(_REPO_ROOT, _stale + _suffix)
        try:
            if os.path.exists(_path):
                os.remove(_path)
        except OSError:
            pass
