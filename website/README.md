# Synapse public website

This directory contains the standalone, dependency-free public product website. It is deliberately separate from the root Vite/React dashboard and Node gateway.

## Local preview

From the repository root:

- Python: <code>python -m http.server 8080 --directory website</code>
- Open <code>http://localhost:8080</code>

The site is plain HTML, CSS, and JavaScript. No build step, package install, analytics, cookie banner, or third-party font request is required.

## GitHub Pages

The workflow at <code>.github/workflows/pages.yml</code> publishes only this directory. After merging the website branch, open **Repository Settings → Pages** and set the build/deployment source to **GitHub Actions** if it is not already configured. The workflow then deploys to the repository's GitHub Pages URL.

The current project-site URL used in metadata is:

<code>https://721189.github.io/Synapse-Memory/</code>

A custom domain can be configured later; do not point a domain here until the Pages deployment is active and verified.

## Feedback flow

The feedback form does not submit data to a server. It can copy the user's answers locally or open a prefilled GitHub issue draft using the product-feedback template. Users must review and submit the draft themselves. No analytics or visitor tracking is included.

## Content maintenance

Keep release labels synchronized with <code>sdk/python/pyproject.toml</code>, the PyPI release page, and the repository changelog. Distinguish published package functionality from capabilities available only in the current source tree. Do not describe CI, synthetic fault injection, or a source release as independent production validation or customer success.

The security section intentionally calls out the current plaintext search projections/derivatives and the required PostgreSQL runtime-role configuration. Update it only when the underlying code and security documentation change.