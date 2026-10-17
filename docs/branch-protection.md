# Branch Protection — required settings

CI is green, but nothing enforces it yet. Apply these in
GitHub → Settings → Branches → Add rule for `main` (repeat for
`production-hardening` until it is deleted after the final merge).

## Required rule (both branches)

- [ ] Require a pull request before merging — required approvals: **1**,
  dismiss stale approvals on new commits: **on**, require CODEOWNERS review:
  **on** (add a `CODEOWNERS` file naming the maintainers).
- [ ] Require status checks to pass before merging — require branches up to
  date: **on**. Select every terminal job (not individual matrix legs):
  - `Python 3.10/3.11/3.12 Lint & Type Check`
  - `Python 3.10/3.11/3.12 Tests`
  - `Node.js Frontend & Gateway Build`
  - `Split Docker Images + Compose Smoke`
  - `PostgreSQL PGVector Integration Tests`
  - `Helm Template Validation`
  - `Secret Scan`
- [ ] Require conversation resolution before merging: **on**.
- [ ] Do not allow bypassing the above settings: **on** (no admin bypass).
- [ ] Allow force pushes: **off**. Allow deletions: **off**.
- [ ] Require signed commits: **on** (recommended).

## Why this matters

Phases 1–4 were proven by a green CI run, not by inspection. Without required
checks, a single direct push can reintroduce a fake-success fallback, an
unscoped endpoint, or an unpinned action. The rule makes the release gate
mechanical: red CI = no merge, no exceptions.

## Verification

After applying, open a draft PR with a one-line comment change and confirm:
(1) all checks are listed as required, (2) merge is blocked until they pass,
(3) an admin cannot bypass without a recorded exception.
