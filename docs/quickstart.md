# Synapse Memory Quickstart

This guide has two paths. Start with the local SQLite path to verify the API
without provisioning external services. Use the production path only after
reading the security and database-role requirements.

## Path A — first successful write and retrieval (local development)

### 1. Install

Python 3.10 or newer is required. Create and activate a virtual environment:

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install synapse-memory-engine==1.3.1
```

The package is named `synapse-memory-engine`; the Python import is
`synapse_memory`. The SDK and original source in this repository are licensed
under Apache License 2.0; third-party dependencies retain their own licenses.
See [LICENSE](../LICENSE) and the [licensing policy](licensing.md).

### 2. Start a local engine

In terminal 1, from a directory where you want the local database and key file
to live:

```bash
export SYNAPSE_STORAGE_BACKEND=sqlite
export SYNAPSE_SQLITE_DB_PATH=./synapse_memory.db
python -m uvicorn synapse_memory.api.fastapi_server:app --host 127.0.0.1 --port 8008
```

For Windows PowerShell, use:

```powershell
$env:SYNAPSE_STORAGE_BACKEND = "sqlite"
$env:SYNAPSE_SQLITE_DB_PATH = "./synapse_memory.db"
python -m uvicorn synapse_memory.api.fastapi_server:app --host 127.0.0.1 --port 8008
```

Wait for Uvicorn to report that the application has started. Keep this terminal
running. In this local-only configuration, authentication is intentionally
development-open; do not expose the port to a network or use this setup in
production.

The default deterministic hash embedder works offline but is **not semantic**.
This path verifies write/read plumbing, not semantic-retrieval quality. The
engine may create `.synapse_key` for encryption. Keep it safe and stable while
the database exists; do not commit it, and do not delete it while retaining the
database.

### 3. Write and retrieve a memory

In terminal 2, with the same virtual environment active, save the following as
`quickstart.py`:

```python
from synapse_memory import SynapseClient, SynapseError

with SynapseClient(
    "http://127.0.0.1:8008",
    tenant_id="local-dev",
    timeout=10,
) as client:
    created = client.create_memory(
        "The project uses PostgreSQL in production and SQLite for local demos.",
        category="fact",
    )
    print(f"Created: id={created.id}, status={created.status}")

    result = client.retrieve("Which database is used for local demos?")
    print("Retrieved context:")
    print(result.fused_context or "(No context returned; inspect engine logs.)")

    # Example of explicit API error handling:
    try:
        client.retrieve("")
    except SynapseError as exc:
        print(f"Request failed: {exc}; retryable={exc.retryable}")
```

Run it:

```bash
python quickstart.py
```

Expected result: the script prints a created memory ID and retrieved context
containing the saved fact. If context is empty, check that the engine is still
running and inspect its terminal logs before continuing.

### 4. Confirm readiness and stop

In a third terminal:

```bash
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8008/readiness').read().decode())"
```

Stop the engine with `Ctrl+C` in terminal 1. Keep the database and `.synapse_key`
together if you want to reuse local data.

## Path B — production-shaped PostgreSQL setup

Do not use Path A as a production configuration. A production deployment needs
PostgreSQL with pgvector, a real semantic embedding provider, stable encryption
and API secrets, and separate bootstrap, migration, and least-privilege runtime
database roles.

1. Install the PostgreSQL extra:

   ```bash
   python -m pip install "synapse-memory-engine[pgvector]==1.3.1"
   ```

2. Provision PostgreSQL, pgvector, the schema, and the separate roles before
   starting the engine. Follow the repository's [security guide](../SECURITY.md)
   and [operations runbook](ops-runbook.md); do not use a bootstrap/superuser DSN
   as the application runtime connection.
3. Configure `DATABASE_URL` with the runtime role, set
   `SYNAPSE_STORAGE_BACKEND=pgvector` and
   `SYNAPSE_APPLY_DB_SCHEMA=false`, provide a stable
   `SYNAPSE_ENCRYPTION_KEY`, and choose a semantic provider using
   `SYNAPSE_EMBEDDING_PROVIDER` plus its API key or local-model dependencies.
4. Configure a non-default `SYNAPSE_API_KEY` and keep all secrets outside source
   control. Use TLS and restrict network access to the engine.

Example runtime command after provisioning and configuration:

```bash
python -m uvicorn synapse_memory.api.fastapi_server:app --host 0.0.0.0 --port 8008
```

Do not copy placeholder credentials literally. See [the operations runbook](ops-runbook.md)
and [backup/recovery guide](backup-recovery.md) before handling real data.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Connection refused | Engine is not running or bound to another port | Keep terminal 1 open; check the Uvicorn startup log and port 8008 |
| HTTP 403 | Auth is configured or the supplied key/tenant is invalid | Check `SYNAPSE_API_KEY`, `X-Synapse-API-Key`, and `X-Tenant-ID`; never rely on dev-open auth outside local development |
| HTTP 429 | Rate limit reached | Back off; see `RATE_LIMIT_PER_MIN` and Redis configuration |
| HTTP 503 / readiness failure | Engine or database is unavailable | Inspect engine logs, `DATABASE_URL`, and `/readiness` |
| Decryption failure | Wrong/missing key for existing encrypted data | Restore the exact encryption key used when writing the data |
| Empty retrieval context | Query did not retrieve a candidate, or provider/configuration mismatch | Confirm the write succeeded; inspect engine logs and use a semantic embedding provider for meaningful semantic retrieval |

Next: [API reference](api-reference.md), [security guide](../SECURITY.md), and
[backup/recovery](backup-recovery.md).
