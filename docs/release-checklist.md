# Release checklist

`main` is protected: changes land through a pull request with review and green
CI. A commit on `main` is **not** a release — a release exists only when all of
the artifacts below carry the same version and the tests for that commit passed.

## Version map (must match)

| Artifact | File |
|---|---|
| Python SDK | `sdk/python/pyproject.toml` → `version` |
| Runtime string | `sdk/python/synapse_memory/__init__.py` → `__version__` |
| Workspace manifest | `pyproject.toml` (root) → `version` |
| Gateway package | `package.json` → `version` |
| Engine/gateway images | `deploy/docker-compose.yml` → `GATEWAY_VERSION` default |
| Helm chart | `deploy/helm-chart/Chart.yaml` → `version` (chart) and `appVersion` (code) |
| Documentation | `README.md` version badge and compatibility table |
| Changelog | `CHANGELOG.md` — a dated `## x.y.z` section |

## Steps

1. **CI green on the exact commit.** Every job in *Synapse CI/CD Matrix
   Pipeline* must pass, including `Python PGVector Integration Tests` (live
   Postgres + non-owner runtime role) and `Split Docker Images + Compose Smoke`.
   A red run means no release, regardless of what the README says.
2. **Version bump** in every row of the table above; add a dated `CHANGELOG.md`
   section describing user-visible changes and any migration notes.
3. **Tag** `vX.Y.Z` and push the tag (`git tag -s vX.Y.Z && git push origin vX.Y.Z`).
4. **Publish** the SDK via the `publish-pypi.yml` workflow (trusted publishing),
   and build/push the engine + gateway images with matching `vX.Y.Z` tags.
5. **Verify the published artifact**, not the working tree:
   `pip install synapse-memory-engine==X.Y.Z` in a clean venv and run
   `python -c "import synapse_memory; print(synapse_memory.__version__)"`.
6. **Create the GitHub release** referencing the changelog section, then confirm
   the release page lists the tag and the assets.
7. **Only then** update README/docs to call the version released.

## Documentation honesty rules

- Do not claim "CI green", "P0–P2 complete" or "production hardened" for a
  commit whose pipeline is red.
- Do not describe a fix as released before the tag and artifacts exist.
- Capacity and performance claims must cite `docs/capacity.md` or a benchmark
  artifact committed to the repository, never an assumption.
