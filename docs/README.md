# Synapse-Memory Documentation

Sovereign, token-aware cognitive memory substrate: DB-level multi-tenancy, Fernet encryption, 0/1 Knapsack DP context budgeting, hybrid retrieval (BM25 + pgvector HNSW + RRF), Ebbinghaus decay, deduplication, RLAIF feedback.

## Quick Start

```bash
git clone https://github.com/721189/Synapse-Memory.git && cd Synapse-Memory
cp deploy/.env.example .env
# Edit .env: SYNAPSE_API_KEY, SYNAPSE_ENCRYPTION_KEY, POSTGRES_PASSWORD
docker compose -f deploy/docker-compose.yml up -d
curl http://localhost:8008/healthz
curl -X POST http://localhost:8008/api/memories/add -H "X-Synapse-API-Key: $SYNAPSE_API_KEY" -H "Content-Type: application/json" -d '{"content": "User prefers TypeScript", "category": "preference"}'
curl -X POST http://localhost:8008/query -H "X-Synapse-API-Key: $SYNAPSE_API_KEY" -H "Content-Type: application/json" -d '{"prompt": "What language does the user prefer?", "max_token_budget": 500}'
```

## Installation

```bash
pip install -e ".[local-models]"    # 384-d MiniLM embeddings
pip install -e ".[pgvector]"        # PostgreSQL + pgvector
pip install -e ".[langchain]"       # LangChain integration
pip install -e ".[llamaindex]"      # LlamaIndex integration
```

## API Reference

All responses: `{ success, data, request_id }` envelope.

### Memory

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/api/memories/add` | `memories:write` | Ingest (auto-dedup + prune) |
| POST | `/api/memories/query` | `memories:read` | Hybrid search + decay + Knapsack |
| GET | `/api/memories` | `memories:read` | List (`?category=&tenant_id=`) |
| DELETE | `/api/memories/{id}` | `memories:delete` | Delete one |
| POST | `/api/memories/clear` | `memories:delete` | Clear all |
| POST | `/api/memories/bulk-ingest` | `memories:write` | Bulk (≤100) |
| POST | `/feedback` | `memories:write` | RLAIF boost/demote |

### Security & Observability

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/api/auth/keys` | `admin` | Generate key (hashed) |
| GET | `/api/auth/keys` | `admin` | List keys |
| POST | `/api/auth/keys/revoke` | `admin` | Revoke |
| POST | `/api/auth/keys/rotate` | `admin` | Rotate |
| GET | `/api/auth/audit` | `admin` | Audit export |
| POST | `/api/security/scrub-pii` | `memories:read` | Scrub PII |
| POST | `/api/security/gdpr-delete` | `memories:delete` | GDPR delete |
| GET | `/healthz` `/readyz` `/metrics` | none | Probes + Prometheus |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_API_KEY` | — | Root admin key (required in prod) |
| `SYNAPSE_ENCRYPTION_KEY` | — | 32-byte Fernet key (required in prod) |
| `DATABASE_URL` | — | PostgreSQL DSN (required in prod) |
| `SYNAPSE_STORAGE_BACKEND` | `auto` | `auto` / `pgvector` / `sqlite` |
| `SYNAPSE_EMBEDDING_PROVIDER` | `local` | `local` / `deterministic` / `gemini` / `openai` |
| `TOKEN_BUDGET_MAX` | `4000` | Knapsack token window cap |
| `RATE_LIMIT_PER_MIN` | `120` | Per-key limit |
| `ENABLE_DEMO_ROUTES` | `false` in prod | Simulated demo endpoints |
| `NODE_ENV` | `development` | `production` = strict auth + fail-closed |

## Docker Compose / Helm

```bash
docker compose -f deploy/docker-compose.yml up -d
helm install synapse deploy/helm-chart --set config.nodeEnv=production
```

## Python SDK

```python
from synapse_memory import MemoryManager, SQLiteMemoryStore, SynapseEmbedder
store = SQLiteMemoryStore("my_memory.db")
embedder = SynapseEmbedder(provider="local", model_name="all-MiniLM-L6-v2")
manager = MemoryManager(store=store, embedder=embedder)
manager.ingest_with_deduplication(content="User prefers PostgreSQL", category="preference", tenant_id="acme")
```

## LangChain

```python
from synapse_memory.integrations.langchain import SynapseMemory
memory = SynapseMemory(tenant_id="acme", memory_manager=manager, searcher=searcher, decay=decay, packer=packer, embedder=embedder)
memory.save_context(inputs={"input": "Capital of France?"}, outputs={"output": "Paris"})
```

## Security

- SHA-256 hashed keys + scope RBAC (`memories:read/write/delete`, `admin`)
- DB-level RLS (pgvector) or app-level tenant scoping (SQLite)
- Fernet (AES-128-CBC + HMAC) field encryption; env/file/KMS key management
- Audit log on every auth event + destructive action
- PII scrubbing: emails, phones, SSNs, `sk-*` keys, credit cards

## Development

```bash
pip install -r requirements-dev.txt && pip install -e "sdk/python[all,dev]"
PYTHONPATH=sdk/python pytest sdk/python/tests -v
flake8 sdk/python/synapse_memory --config=setup.cfg
npm ci && npm run lint && npm run build
```

## License

MIT — see [LICENSE](../LICENSE).
