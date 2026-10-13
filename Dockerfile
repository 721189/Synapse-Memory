# ─────────────────────────────────────────────────────────────────────────────
# ⚠️  This monolithic image is DEPRECATED and kept only for backward compat.
#
# SynapseMemory now ships as TWO purpose-built images:
#   • Dockerfile.engine  → Python cognitive engine (FastAPI + pgvector)  :8008
#   • Dockerfile.gateway → Node dashboard + API gateway (proxies engine) :3000
#
# The split image pair has proper non-root users, healthchecks, graceful
# shutdown (tini), and independent scaling. Use it via deploy/docker-compose.yml
# or deploy/helm-chart. Building THIS file runs the old combined container
# (uvicorn & node) which lacks those guarantees — only use for quick local demos.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN pip install --no-cache-dir -e sdk/python
RUN npm run build

EXPOSE 3000
EXPOSE 8008

ENV NODE_ENV=production
ENV PORT=3000
ENV PYTHONPATH=/app/sdk/python
ENV PYTHON_PORT=8008

# NOTE: '&' backgrounding has no supervisor/graceful shutdown. Prefer the split
# Dockerfile.engine + Dockerfile.gateway for anything beyond a local demo.
CMD ["sh", "-c", "python3 -m uvicorn synapse_memory.api.fastapi_server:app --host 0.0.0.0 --port 8008 & node dist/server.cjs"]

