# Publishing the Python SDK to PyPI

## The name: `synapse-memory-engine`

The distribution published to PyPI is **`synapse-memory-engine`**. The import
package is, and remains, `synapse_memory`.

Why not `synapse-memory`: that name has been owned on PyPI since 2025-06-01 by
an unrelated third-party project (`synapse-memory` 0.1.0 by *kamome* — a
SQLite/ChromaDB experiment). PyPI names are global and permanent per owner; we
cannot and must not squat or impersonate it. `synapse-memory-sdk` is likewise
taken. `synapse-memory-engine` was verified unregistered before selection and
matches this project's own terminology (the FastAPI storage/retrieval engine).

Consequences of the rename:

| Surface | Value |
|---|---|
| PyPI project / dist name | `synapse-memory-engine` |
| Import package | `synapse_memory` (unchanged) |
| Wheel / sdist filename | `synapse_memory_engine-<ver>-…` |
| Console scripts | `synapse`, `synapse-cli`, `synapse-memory` (unchanged) |
| Published source tree | `sdk/python/` (root `pyproject.toml` is the non-published `synapse-memory-workspace` tooling manifest) |

A user must never be able to `pip install synapse-memory` and believe they got
this project — that name resolves to someone else's package. Always show
install commands as `pip install synapse-memory-engine[...]`.

## One-time setup (operator / maintainer)

1. **PyPI — pending trusted publisher.** On PyPI → Account settings →
   Publishing → *Add a new pending publisher*:
   - Project name: `synapse-memory-engine`
   - Owner: `721189` · Repository: `Synapse-Memory`
   - Workflow name: `publish-pypi.yml` · Environment name: `pypi`

   The *pending* publisher registers the project on first successful OIDC
   publish — no API token is ever created or stored. After the first release,
   PyPI shows it as a normal trusted publisher on the (now existing) project.
2. **GitHub — `pypi` environment.** Repo → Settings → Environments → New
   environment named `pypi`. Require reviewer approval for production releases
   where possible. A tag-triggered package workflow validates/builds the SDK;
   it is not a substitute for the full main-branch CI and deployment smoke test.

## Release procedure

1. Bump the **SDK** version in lockstep: `sdk/python/pyproject.toml`,
   `sdk/python/setup.py`, `sdk/python/synapse_memory/__init__.py`,
   `sdk/python/synapse_memory/client.py` (`SDK_VERSION`),
   `sdk/python/synapse_memory/api/fastapi_server.py` (OpenAPI version),
   root `pyproject.toml`, the source-version summary in `README.md`, and
   `CHANGELOG.md`. The SDK version is checked independently from
   `package.json` and deployment image tags: publishing to PyPI does **not**
   publish or retag the engine/gateway container images.
2. Update `CHANGELOG.md`; merge to `main`; wait for the full CI matrix, the
   3072-dimensional pgvector integration test, and deployment smoke test to pass.
   Do not tag a release from a commit whose deployment checks are failing.
3. Tag and push — the tag must equal `v<version>`:

   ```bash
   VERSION=1.4.0
   git tag -s "v${VERSION}"
   git push origin "v${VERSION}"
   ```

4. The `publish-pypi.yml` workflow then: runs the full unit suite → builds
   sdist+wheel → `twine check --strict` → asserts tag == package version →
   installs the wheel into a **fresh venv** and imports it (proving the
   published artifact is self-contained and owns the `synapse_memory` import)
   → publishes via OIDC.
5. Post-release smoke (the workflow proves wheel installation/import; a separate
   disposable PostgreSQL/pgvector smoke is still required for deployment readiness):

   ```bash
   python -m venv /tmp/smoke && /tmp/smoke/bin/pip install "synapse-memory-engine==<version>"
   /tmp/smoke/bin/python -c "import synapse_memory; print(synapse_memory.__version__)"
   ```

`workflow_dispatch` runs the identical pipeline without the upload — use it to
dry-run the build/verify stages at any time.

## Yanking

If a bad release ships, yank it on PyPI (don't delete): existing pins keep
working, new installs are blocked, and the version number is retired. Then fix
forward with a new patch version.
