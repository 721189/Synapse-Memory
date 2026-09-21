# Multi-stage production container for SynapseMemory Unified Platform
FROM python:3.11-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python SDK and requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy package manifests for Node
COPY package*.json ./
RUN npm ci

# Copy entire codebase
COPY . .

# Install local Synapse Python package
RUN pip install --no-cache-dir -e sdk/python

# Build Vite frontend and server bundle
RUN npm run build

# Expose ports: 3000 (Gateway UI/API) and 8008 (FastAPI Engine)
EXPOSE 3000
EXPOSE 8008

ENV NODE_ENV=production
ENV PORT=3000
ENV PYTHONPATH=/app/sdk/python
ENV PYTHON_PORT=8008

# Start script
CMD ["sh", "-c", "python3 -m uvicorn synapse_memory.api.fastapi_server:app --host 0.0.0.0 --port 8008 & node dist/server.cjs"]
