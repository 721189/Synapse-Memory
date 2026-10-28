#!/usr/bin/env python3
"""Fail when release versions drift across the repository (P1).

Checked surfaces:
  * sdk/python/pyproject.toml            -> project.version
  * sdk/python/synapse_memory/__init__.py -> __version__
  * sdk/python/synapse_memory/client.py   -> SDK_VERSION
  * sdk/python/synapse_memory/api/fastapi_server.py -> OpenAPI version
  * sdk/python/setup.py                   -> legacy version metadata
  * pyproject.toml (root)                -> project.version
  * package.json / package-lock.json     -> version
  * README.md                            -> version badge
  * deploy/helm-chart/Chart.yaml         -> appVersion
  * deploy/docker-compose.yml            -> GATEWAY_VERSION fallback
  * deploy/.env.example                  -> GATEWAY_VERSION
  * deploy/helm-chart/values.yaml        -> image tags
  * CHANGELOG.md                          -> released state for this version

Usage: python scripts/check_version_consistency.py
Exit code 1 on any drift, with one line per mismatch.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"FAIL cannot read {rel}: {exc}")


def find(rel: str, pattern: str, label: str) -> str:
    match = re.search(pattern, read(rel), re.MULTILINE)
    if not match:
        raise SystemExit(f"FAIL {rel}: could not locate {label}")
    return match.group(1)


def main() -> int:
    # Python SDK release version is independent from the separately built and
    # published Node gateway / container image version.
    sdk_version = find(r"sdk/python/pyproject.toml", r'^version = "([^"]+)"', "SDK project.version")
    runtime_version = find(
        r"sdk/python/synapse_memory/__init__.py", r'__version__ = "([^"]+)"', "SDK __version__"
    )
    root_version = find("pyproject.toml", r'^version = "([^"]+)"', "workspace project.version")
    setup_version = find("sdk/python/setup.py", r'version="([^"]+)"', "legacy setup.py version")
    client_version = find("sdk/python/synapse_memory/client.py", r'^SDK_VERSION = "([^"]+)"', "SDK_VERSION")
    api_version = find(
        "sdk/python/synapse_memory/api/fastapi_server.py",
        r'^[ \t]+version="([^"]+)"',
        "FastAPI OpenAPI version",
    )
    changelog_heading = find(
        "CHANGELOG.md",
        rf"^## {re.escape(sdk_version)} — (.+)$",
        f"{sdk_version} changelog heading",
    )
    if "unreleased" in changelog_heading.lower():
        raise SystemExit(f"FAIL CHANGELOG.md still labels current version {sdk_version} as unreleased")

    readme_version = find(
        "README.md",
        r"\*\*Synapse Memory\*\* \(v([0-9]+\.[0-9]+\.[0-9]+)\)",
        "README source version in executive summary",
    )
    sdk_checks = {
        "sdk/python/pyproject.toml version": sdk_version,
        "sdk/python/synapse_memory/__init__.py __version__": runtime_version,
        "sdk/python/setup.py version": setup_version,
        "sdk/python/synapse_memory/client.py SDK_VERSION": client_version,
        "FastAPI OpenAPI version": api_version,
        "pyproject.toml (root) version": root_version,
        "README source version": readme_version,
    }
    sdk_drift = {key: value for key, value in sdk_checks.items() if value != sdk_version}

    gateway_version = json.loads(read("package.json"))["version"]
    package_lock = json.loads(read("package-lock.json"))
    lock_version = package_lock.get("version", "")
    lock_root_version = package_lock.get("packages", {}).get("", {}).get("version", "")
    deployment_app_version = find(
        "deploy/helm-chart/Chart.yaml",
        r'^appVersion: "([^"]+)"',
        "Helm appVersion",
    )
    compose_tags = set(
        re.findall(
            r"GATEWAY_VERSION:-v([0-9]+\.[0-9]+\.[0-9]+)",
            read("deploy/docker-compose.yml"),
        )
    )
    env_version = find(
        "deploy/.env.example",
        r"^GATEWAY_VERSION=v([0-9]+\.[0-9]+\.[0-9]+)",
        "GATEWAY_VERSION",
    )
    helm_tags = set(
        re.findall(
            r'^\s+tag: "v([0-9]+\.[0-9]+\.[0-9]+)"',
            read("deploy/helm-chart/values.yaml"),
            re.MULTILINE,
        )
    )
    deployment_checks = {
        "package.json version": gateway_version,
        "package-lock root version": lock_version,
        "package-lock package root version": lock_root_version,
        "Helm chart appVersion": deployment_app_version,
        "deploy/.env.example GATEWAY_VERSION": env_version,
    }
    for index, tag in enumerate(sorted(compose_tags), start=1):
        deployment_checks[f"deploy/docker-compose.yml image tag #{index}"] = tag
    for index, tag in enumerate(sorted(helm_tags), start=1):
        deployment_checks[f"deploy/helm-chart/values.yaml image tag #{index}"] = tag
    deployment_drift = {
        key: value for key, value in deployment_checks.items() if value != gateway_version
    }

    failed = False
    if sdk_drift:
        failed = True
        print(f"FAIL SDK release version drift: expected {sdk_version} everywhere")
        for key, value in sdk_drift.items():
            print(f"  {key} = {value} (expected {sdk_version})")
    if deployment_drift:
        failed = True
        print(
            "FAIL gateway/deployment version drift: "
            f"expected {gateway_version} everywhere"
        )
        for key, value in deployment_drift.items():
            print(f"  {key} = {value} (expected {gateway_version})")
    if failed:
        return 1

    print(
        f"OK SDK metadata agrees on {sdk_version} ({len(sdk_checks)} surfaces); "
        f"gateway/deployment metadata agrees on {gateway_version} "
        f"({len(deployment_checks)} surfaces)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
