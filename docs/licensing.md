# Licensing and Open-Core Policy

## Current repository license

The original Synapse Memory source code and documentation in this repository
are licensed under the [Apache License, Version 2.0](../LICENSE), unless a
specific file or directory states a different license. Copyright in the
original project work remains with its copyright holder; the Apache license
grants users the rights described in its terms.

This license permits commercial use, modification, redistribution, and
hosting of the covered work, subject to its conditions. It does not require
users to publish modifications. It does not grant rights to the Synapse
trademarks; see [TRADEMARKS.md](../TRADEMARKS.md).

## Open-core boundary

The current repository is the open-source developer core. This includes the
Python SDK, memory engine, retrieval components, agent execution controls,
HTTP API, self-hosted dashboard/gateway, deployment manifests, examples,
tests, and documentation present in this repository.

Future managed-cloud control-plane services, hosted operations, enterprise
support, service-level commitments, and other separately developed commercial
services may be offered under separate commercial terms. Keep future
proprietary implementation in a separately licensed repository or clearly
separated distribution with its own license notices. Do not silently place
proprietary source files under this repository's root Apache license.

The open-source core must remain useful for developers to install, modify,
self-host, and use in commercial applications. Do not remove essential
existing core capabilities solely to force a hosted-service upgrade.

## Third-party dependencies and assets

The repository license does not relicense dependencies or externally supplied
materials. Each dependency, model, dataset, font, icon, generated asset, base
image, and copied code fragment remains subject to its own license and notices.

Before adding or updating dependencies, record the package, resolved version,
license expression, source URL, and any attribution or redistribution
obligations. Review the full resolved dependency graph, not only direct
dependencies in `pyproject.toml`, `requirements*.txt`, or `package.json`.

### Observed Node lockfile metadata snapshot (2026-10-30)

The checked-in `package-lock.json` currently contains 275 package entries with
a populated `license` field. Its raw metadata groups as follows:

| License expression | Package entries |
|---|---:|
| MIT | 199 |
| Apache-2.0 | 30 |
| MPL-2.0 | 24 |
| BSD-3-Clause | 13 |
| ISC | 6 |
| CC-BY-4.0 | 1 |
| BSD-2-Clause | 1 |
| 0BSD | 1 |

The 24 MPL-2.0 entries are the platform-specific `lightningcss` packages
(versions 1.32.0 and 1.33.0). The single CC-BY-4.0 entry is
`caniuse-lite@1.0.30001819`, which the lockfile marks as a development
dependency. These dependencies retain their own licenses; they are not
covered by Synapse's Apache grant. Check the resolved production dependency
report and deployment image contents before release, and preserve required
third-party license texts and attributions.

This is a snapshot of lockfile metadata, not a completed legal compatibility
or binary-redistribution audit. The Python dependency graph has no checked-in
lockfile and still requires a resolved-environment inventory.

### Dependency report findings (initial resolved inventory, 2026-10-30)

The first CI-generated report resolved 89 Python distributions and 185 Node
packages. The raw reports are attached to the PR workflow run as the
`third-party-license-inventory` artifact.

Items requiring explicit notice/redistribution review include:

- `psycopg2-binary 2.9.13` is LGPL v3 or later with an OpenSSL linking
  exception. It is pulled by the optional PostgreSQL integration extra and
  must be considered when redistributing an image or bundle that includes it.
- `certifi 2026.10.23` includes an MPL-2.0 certificate bundle. `orjson
  3.13.0` and `tqdm 4.70.1` report mixed MPL/MIT license expressions; review
  the exact covered files and preserve the corresponding notices.
- Node's `lightningcss` packages (1.32.0 and 1.33.0) report MPL-2.0.
  `caniuse-lite 1.0.30001819` reports CC-BY-4.0 and is marked development-only
  in the lockfile; verify whether its data is incorporated into distributed
  build output and preserve attribution where required.
- `dirtyjson 1.0.8` reports an AFL/MIT dual-license expression. Select and
  record the applicable license basis when distributing it.

The initial Node report also marked the local gateway package `UNLICENSED`
because the lockfile root package metadata lacked its license field. The
lockfile root entry has since been aligned with `package.json` as
`Apache-2.0`; the refreshed report must confirm that the stale `UNLICENSED`
entry is gone.

This inventory is a useful triage result, not a legal sign-off. For binary
container releases, check the exact packages that make it into each image,
include the applicable license texts and attributions, and assess LGPL/MPL
source or relinking obligations for the shipped artifacts. The package-level
report alone does not determine whether a specific deployment image redistributes
a given dependency.

### Reproducible inventory commands

Run these in clean environments from the repository root and retain the
reports with release evidence.

Python SDK, including optional runtime integrations:

```bash
python -m venv .venv-license-audit
# Activate the environment, then:
python -m pip install --upgrade pip
python -m pip install -e "sdk/python[all]"
python -m pip install pip-licenses
pip-licenses --format=json --with-urls --with-license-file \
  > python-third-party-licenses.json
```

Node production dependency tree:

```bash
npm ci
npx --yes license-checker --production --summary
npx --yes license-checker --production --json \
  > node-third-party-licenses.json
```

These commands generate an inventory; they do not make a legal compatibility
decision. Review unknown, custom, dual, copyleft, source-available, or
restrictive license expressions manually. Confirm that all required notices
are included in source and binary distributions. Include licenses for
container base images and operating-system packages in the deployment SBOM
review as well.

Do not describe the dependency set as fully audited until the reports have
been reviewed and any required notices or remediation have been completed.
Do not copy the report output into the repository without reviewing it for
accuracy and reproducibility.

## Contributions

Contributions are accepted under Apache License 2.0 and require the
[Developer Certificate of Origin](../DCO) sign-off. We do not currently
require a CLA. A contributor must have the right to submit every part of
their contribution under the stated license.

## Releases and previously published packages

The Apache 2.0 grant applies to repository revisions and package artifacts
that actually include this licensing change. It does not retroactively change
the license of previously published PyPI artifacts, tagged source releases,
or copies already distributed under earlier terms. The next SDK release must
include the Apache license file and correct package metadata in both the wheel
and source distribution.

## Legal review

This document describes the project's intended licensing model; it is not
legal advice. Obtain qualified legal review of the final release, third-party
dependency inventory, trademark policy, and any separate hosted-service or
enterprise agreements.
