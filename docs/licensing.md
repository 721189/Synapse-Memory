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
pip-licenses --format=markdown --with-urls --with-license-file \
  > python-third-party-licenses.md
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
