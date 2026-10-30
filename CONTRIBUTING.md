# Contributing to Synapse Memory

Contributions are welcome. By contributing, you agree that your contribution
will be made available under the repository's [Apache License 2.0](LICENSE).

## Developer Certificate of Origin

Every contribution must include a Developer Certificate of Origin (DCO)
sign-off. Add a `Signed-off-by` trailer to each commit:

```bash
git commit -s -m "fix: describe the change"
```

The sign-off certifies that you have the right to submit the contribution
under the [DCO](DCO). Do not sign off on code, documentation, assets, or other
material that you did not author or are not authorized to contribute.

We do not require a separate Contributor License Agreement at this time.
Contributions are accepted under Apache License 2.0; no contribution grants
the project owner a separate right to relicense your work under proprietary
terms.

## Development Workflow

1. Fork and clone the repository.
2. Create an isolated Python environment and install the SDK development
   dependencies as described in the repository documentation.
3. Create a focused branch with a descriptive name.
4. Follow the existing code style and type annotations.
5. Add or update tests for behavior changes.
6. Update documentation when changing an API, configuration, or user-facing
   behavior.
7. Run the relevant tests, linting, type checks, and packaging checks locally.

## Pull Request Process

1. Explain the problem, implementation, compatibility impact, and test results.
2. Link related issues and include relevant screenshots or logs with secrets
   and personal data removed.
3. Ensure all commits carry the required DCO sign-off.
4. Ensure required CI checks pass.
5. Disclose any new dependencies, copied material, generated assets, or
   licensing restrictions in the pull request description.

## Third-Party Material

Do not copy code, documentation, model weights, datasets, fonts, icons, or
other assets into the repository unless their license permits the intended
use and redistribution. Preserve required notices and record new third-party
materials in the licensing inventory. Linking to a dependency does not
relicense that dependency.

## Code of Conduct

Be respectful and professional in all project interactions.
