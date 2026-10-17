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
   environment named `pypi`. Optionally require N reviewers for manual
   protection of releases.

## Release procedure

1. Bump the version in lockstep (see the compatibility matrix in the root
   `README.md`): `sdk/python/pyproject.toml`, `sdk/python/setup.py`,
   `sdk/python/synapse_memory/__init__.py`, and root `pyproject.toml`.
2. Update `CHANGELOG.md`; merge to `main`; wait for the CI matrix to go green.
3. Tag and push — the tag must equal `v<version>`:

   ```bash
   git tag v1.2.1
   git push origin v1.2.1
   ```

4. The `publish-pypi.yml` workflow then: runs the full unit suite → builds
   sdist+wheel → `twine check --strict` → asserts tag == package version →
   installs the wheel into a **fresh venv** and imports it (proving the
   published artifact is self-contained and owns the `synapse_memory` import)
   → publishes via OIDC.
5. Post-release smoke (fresh environment, the guarantee from the workflow):

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
