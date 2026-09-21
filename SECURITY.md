# Security Policy

## Reporting a Vulnerability

We take the security of the Synapse Cognitive Substrate seriously. If you discover or suspect a security vulnerability, **please do not open a public GitHub issue**. Publicly disclosing flaws puts all downstream production deployments at risk.

Instead, please report vulnerabilities through one of our private channels:

1. **GitHub Private Vulnerability Reporting (Preferred)**: Navigate to the [Security Advisories page](https://github.com/721189/Synapse-Memory/security/advisories) and select **"Report a vulnerability"** to open a confidential discussion directly with the core maintainers.
2. **Dedicated Security Contact**: Send a detailed report directly to:
   - **`singhshivam20009@gmail.com`**
   - For sensitive cryptographic or multi-tenant isolation vulnerabilities, please include reproduction steps, expected vs. actual behavior, and affected versions.

### What to Include
To help us triage and resolve the issue quickly, please include:
- A description of the vulnerability and its potential impact.
- Step-by-step reproduction instructions or a minimal Proof-of-Concept (PoC) script.
- Affected versions, OS, Python/Node runtime environment, and dependency tree.
- Any suggested mitigations or candidate patches.

### Response Timelines
- **Initial Acknowledgment**: Within 24–48 hours.
- **Triage & Severity Assessment**: Within 5 business days.
- **Coordinated Patch & Public Advisory**: Target within 30 days of confirmed reproduction, following standard Coordinated Vulnerability Disclosure (CVD) practices.

## Supported Versions

| Version | Supported | Security Updates |
| :--- | :--- | :--- |
| `1.2.x` / `main` | Yes | Active patches & immediate hotfixes |
| `0.1.x` | Limited | Critical CVE fixes only |
| `< 0.1.0` | No | End of Life (upgrade immediately) |

## Security Architecture & Best Practices

1. **Cryptographic Key Management**:
   - Never commit `SYNAPSE_ENCRYPTION_KEY` to source control.
   - Use dedicated KMS/Vault providers (AWS KMS, GCP Cloud KMS, HashiCorp Vault) to inject 32-byte Fernet keys into containers at runtime.
   - Ensure secure file permissions (`chmod 600 .synapse_key`) when using local filesystem fallback keys.
2. **Multi-Tenant Memory Isolation**:
   - In shared cluster deployments, always enforce tenant boundary verification in API gateways.
   - Run pgvector databases with Row-Level Security (RLS) policies enabled.
3. **Model & Ingestion Sanitization**:
   - Run untrusted third-party prompts through the built-in PII and prompt injection quarantine scrubber (`/api/security/scrub`) before indexing into cognitive graphs.

