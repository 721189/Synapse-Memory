#!/usr/bin/env python3
"""Fail when release versions drift across the repository (P1).

Checked surfaces:
  * sdk/python/pyproject.toml            -> project.version
  * sdk/python/synapse_memory/__init__.py -> __version__
  * pyproject.toml (root)                -> project.version
  * package.json                         -> version
  * deploy/helm-chart/Chart.yaml         -> appVersion
  * deploy/docker-compose.yml            -> GATEWAY_VERSION fallback
  * deploy/.env.example                  -> GATEWAY_VERSION
  * deploy/helm-chart/values.yaml        -> image tags

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
    sdk_version = find(r"sdk/python/pyproject.toml", r'^version = "([^"]+)"', "project.version")
    runtime_version = find(
        r"sdk/python/synapse_memory/__init__.py", r'__version__ = "([^"]+)"', "__version__"
    )
    root_version = find("pyproject.toml", r'^version = "([^"]+)"', "project.version")
    pkg_version = json.loads(read("package.json"))["version"]
    chart_app = find(r"deploy/helm-chart/Chart.yaml", r'^appVersion: "([^"]+)"', "appVersion")

    compose_tags = set(re.findall(r"GATEWAY_VERSION:-v([0-9]+\.[0-9]+\.[0-9]+)", read("deploy/docker-compose.yml")))
    env_version = find(r"deploy/.env.example", r"^GATEWAY_VERSION=v([0-9]+\.[0-9]+\.[0-9]+)", "GATEWAY_VERSION")
    helm_tags = set(re.findall(r'^\s+tag: "v([0-9]+\.[0-9]+\.[0-9]+)"', read("deploy/helm-chart/values.yaml"), re.MULTILINE))

    nominal = sdk_version
    checks = {
        "sdk/python/pyproject.toml version": sdk_version,
        "sdk/python/synapse_memory/__init__.py __version__": runtime_version,
        "pyproject.toml (root) version": root_version,
        "package.json version": pkg_version,
        "deploy/helm-chart/Chart.yaml appVersion": chart_app,
        "deploy/.env.example GATEWAY_VERSION": env_version,
    }
    for i, tag in enumerate(sorted(compose_tags)):
        checks[f"deploy/docker-compose.yml GATEWAY_VERSION fallback #{i + 1}"] = tag
    for i, tag in enumerate(sorted(helm_tags)):
        checks[f"deploy/helm-chart/values.yaml image tag #{i + 1}"] = tag

    drift = {k: v for k, v in checks.items() if v != nominal}
    if drift:
        print(f"FAIL version drift: expected {nominal} everywhere")
        for key, value in drift.items():
            print(f"  {key} = {value} (expected {nominal})")
        return 1

    print(f"OK all release versions agree on {nominal} ({len(checks)} surfaces)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
