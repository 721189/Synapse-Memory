#!/usr/bin/env python3
"""End-to-end deployment smoke test for the Compose stack (P1).

This is deliberately NOT a unit test: it boots the real stack (engine + gateway
+ PostgreSQL + Redis) with production-shaped settings, waits for health, then
performs an authenticated write and read through the GATEWAY, and always tears
the stack down.

Embedding provider: a CI runner has no semantic-provider credentials, so this
smoke runs the engine with the deterministic provider and an explicit
SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING=true. That opt-in is what the production
guard forbids: the guard itself is asserted in the `production-startup` CI job,
so this file never weakens it. Semantics are out of scope here — the smoke
proves wiring: routing, auth, storage, readiness.

Usage:
    python scripts/deploy_smoke.py
Exit code 0 only if every step passed and teardown ran.
"""
import base64
import json
import os
import secrets
# subprocess is required to drive docker compose; argv is fixed, never a shell.
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = ["docker", "compose", "-f", os.path.join(ROOT, "deploy", "docker-compose.yml")]
GATEWAY = "http://127.0.0.1:3000"

# No literal credentials: everything is generated per run unless the caller
# supplies it, so no secret-looking constant ever lives in the repository.
GENERATED = {
    "api_key": "syn_live_" + secrets.token_hex(24),
    "encryption_key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "pg_password": secrets.token_urlsafe(24),
    "app_password": secrets.token_urlsafe(24),
}

ENV = {
    "SYNAPSE_API_KEY": os.environ.get("SYNAPSE_API_KEY", GENERATED["api_key"]),
    "SYNAPSE_ENCRYPTION_KEY": os.environ.get("SYNAPSE_ENCRYPTION_KEY", GENERATED["encryption_key"]),
    "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", GENERATED["pg_password"]),
    "SYNAPSE_APP_DB_PASSWORD": os.environ.get("SYNAPSE_APP_DB_PASSWORD", GENERATED["app_password"]),
    # Wiring-only exception, see module docstring.
    "SYNAPSE_EMBEDDING_PROVIDER": "deterministic",
    "SYNAPSE_ALLOW_DETERMINISTIC_EMBEDDING": "true",
    "GATEWAY_PORT": os.environ.get("GATEWAY_PORT", "3000"),
}


def log(message):
    print(f"[smoke] {message}", flush=True)


def run(cmd, check=True, env=None):
    merged = dict(os.environ)
    merged.update(ENV)
    if env:
        merged.update(env)
    log(" ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else ""))
    # Fixed argv, no shell, no user input: this is the deployment harness itself.
    return subprocess.run(cmd, cwd=ROOT, env=merged, check=check)  # nosec B603 B607


def http(method, path, payload=None, headers=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(GATEWAY + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.status, json.loads(response.read() or b"{}")


def wait_for_health(deadline_seconds=240):
    started = time.time()
    last = None
    while time.time() - started < deadline_seconds:
        try:
            status, body = http("GET", "/api/ready", timeout=5)
            if status == 200 and body.get("status") == "ready":
                log(f"gateway ready after {int(time.time() - started)}s")
                return
            last = body
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(3)
    raise SystemExit(f"FAIL gateway never became ready (last: {last})")


def main():
    api_key = ENV["SYNAPSE_API_KEY"]
    auth = {"X-Synapse-API-Key": api_key, "X-Tenant-ID": "smoke_tenant"}

    log("building and starting the stack")
    run(COMPOSE + ["up", "-d", "--build"])
    try:
        wait_for_health()

        content = f"smoke memory {int(time.time())}"
        status, created = http("POST", "/api/memories/add", {"content": content}, auth)
        if status not in (200, 201):
            raise SystemExit(f"FAIL ingest returned {status}: {created}")
        log(f"ingested {created.get('data', {}).get('id', '?')}")

        status, listed = http("GET", "/api/memories", headers=auth)
        if status != 200:
            raise SystemExit(f"FAIL list returned {status}")
        texts = json.dumps(listed)
        if content not in texts:
            raise SystemExit("FAIL the ingested memory is not readable back through the gateway")
        log("read-back OK")

        # Anonymous access must be refused (fail-closed).
        try:
            http("GET", "/api/memories", headers={"X-Tenant-ID": "smoke_tenant"})
            raise SystemExit("FAIL anonymous request was accepted")
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise SystemExit(f"FAIL anonymous request returned {exc.code} instead of 401/403")
            log(f"anonymous correctly refused ({exc.code})")

        log("PASS")
        return 0
    finally:
        log("tearing the stack down")
        run(COMPOSE + ["down", "-v"], check=False)


if __name__ == "__main__":
    sys.exit(main())
