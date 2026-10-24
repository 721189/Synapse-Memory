# Synapse Memory — developer tasks
#
# Everything runs against the Python in `.venv` and the Node toolchain in
# `node_modules`. `make install` sets both up.
PY ?= python
VENV ?= .venv
BIN := $(VENV)/Scripts
ifeq ($(OS),Windows_NT)
  PY_IN := $(BIN)/python.exe
else
  PY_IN := $(BIN)/python
endif
SDK := sdk/python

.PHONY: help install test test-fast lint typecheck check dev build docker-up docker-down bench clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install Python + Node dependencies
	$(PY) -m venv $(VENV)
	$(PY_IN) -m pip install --upgrade pip
	$(PY_IN) -m pip install -e "$(SDK)[dev,pgvector]"
	npm install --no-audit --no-fund

test: ## Run the full Python test suite
	$(PY_IN) -m pytest $(SDK)/tests -q -p no:cacheprovider

test-fast: ## Run only the security/contract regressions (no DB, fast)
	$(PY_IN) -m pytest $(SDK)/tests -q -p no:cacheprovider \
		-k "f01 or f02 or f05 or f10 or helm or p0 or p1"

lint: ## flake8 (undefined names + syntax)
	$(PY_IN) -m flake8 -j 1 --select=F,E9 --max-line-length=127 $(SDK)/synapse_memory scripts

typecheck: ## mypy over the package and the TypeScript gateway
	$(PY_IN) -m mypy --config-file setup.cfg $(SDK)/synapse_memory
	npx tsc --noEmit

check: lint typecheck test ## Everything CI runs, locally

dev: ## Run the engine (8008) and gateway (3000) for development
	$(PY_IN) -m uvicorn synapse_memory.api.fastapi_server:app --host 127.0.0.1 --port 8008 --reload & \
	npx tsx server.ts

build: ## Build the dashboard + bundled gateway
	npm run build

docker-up: ## Full stack from deploy/.env (requires the documented secrets)
	docker compose -f deploy/docker-compose.yml up -d --build

docker-down: ## Stop the stack
	docker compose -f deploy/docker-compose.yml down

bench: ## Run the ingest/query benchmark harness
	$(PY_IN) $(SDK)/benchmark/bench_suite.py

clean: ## Remove build + cache artifacts
	rm -rf dist .venv $(SDK)/build $(SDK)/*.egg-info
