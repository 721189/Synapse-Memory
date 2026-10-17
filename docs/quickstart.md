# SynapseMemory Quickstart (5 minutes)

Install, configure a backend, create a memory, retrieve it, handle an error.

## 1. Install (fresh environment)
```bash
pip install "synapse-memory-engine[pgvector]"   # prod backend
# or: pip install synapse-memory-engine         # SQLite dev backend only
```

## 2. Start the engine
```bash
export SYNAPSE_ENCRYPTION_KEY='<32-byte urlsafe-base64 Fernet key>'
export SYNAPSE_API_KEY='syn_live_your_key'
# Prod:
export DATABASE_URL='postgresql://user:pass@host:5432/synapse_cognitive'
export SYNAPSE_STORAGE_BACKEND=pgvector
uvicorn synapse_memory.api.fastapi_server:app --host 0.0.0.0 --port 8008
# Dev (SQLite): omit DATABASE_URL.
```

## 3. Create + retrieve (Python)
```python
from synapse_memory import SynapseClient, SynapseError

with SynapseClient("http://127.0.0.1:8008", api_key="syn_live_your_key", tenant_id="acme") as client:
    created = client.create_memory("The deploy password rotates every 90 days.", category="fact")
    print(created.id, created.status)
    result = client.retrieve("how often does the deploy password rotate?")
    print(result.fused_context)
    try:
        client.retrieve("")
    except SynapseError as e:
        print("handled:", e, "retryable:", e.retryable)
```

## 4. Same via curl
```bash
curl -X POST localhost:8008/ingest -H 'Content-Type: application/json' \
  -H 'X-Synapse-API-Key: syn_live_your_key' -H 'X-Tenant-ID: acme' \
  -d '{"content":"The deploy password rotates every 90 days.","category":"fact"}'
curl -X POST localhost:8008/query -H 'Content-Type: application/json' \
  -H 'X-Synapse-API-Key: syn_live_your_key' -H 'X-Tenant-ID: acme' \
  -d '{"prompt":"how often does the deploy password rotate?"}'
```

## 5. Troubleshooting
| Symptom | Cause | Fix |
|---|---|---|
| 401/403 | missing/invalid key or tenant mismatch | check key, `X-Tenant-ID` |
| 429 | rate limit | back off 60s; per-key (`RATE_LIMIT_PER_MIN`), Redis-backed when `REDIS_URL` set |
| 503 `/api/ready` | engine/DB down | check `/readiness`, `DATABASE_URL` |
| `RuntimeError: Field decryption failed` | wrong `SYNAPSE_ENCRYPTION_KEY` | restore the key used at write time |

Next: API reference (`docs/api-reference.md`), backups (`docs/backup-recovery.md`).
