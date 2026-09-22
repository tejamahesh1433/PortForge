# Security Policy

## Supported Versions

Currently, the following versions are supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| < 1.1.0 | :x:                |

## Reporting a Vulnerability

Please do not open a public issue for active vulnerabilities. 

To privately report a vulnerability, please use the GitHub Security Advisory feature for this repository, or contact the repository owner privately via documented contact channels. 

When reporting, please include:
- A description of the vulnerability and its impact.
- Steps to reproduce the issue.
- Any suggested mitigations.

We ask that you do not publish details about active vulnerabilities until we have had an opportunity to review and remediate them.

## Credential Handling Guidance

- **Never commit secrets:** Passwords, tokens, and SSH private keys must never be committed to the repository. 
- **Use Environment Variables:** PortForge services are designed to consume secrets via `.env` files or environment variables. 
- **Private Identity:** PortForge uses generated UUIDs and identity files for agent enrollment. These are private and should be kept secure on each host.

## Deployment Security Assumptions

PortForge relies on the following deployment assumptions:
- **Localhost by Default:** The Central API, Dashboard, and PostgreSQL services bind to `127.0.0.1` by default. 
- **No Built-in Authentication:** The Dashboard and Central API intentionally do not include browser or API authentication at this time. 
- **Trusted Network:** If you expose these services to a LAN or remote network (by setting `PORTFORGE_*_BIND=0.0.0.0`), you **must** use a trusted network environment (e.g., VPN, Tailscale, WireGuard) or implement a secure reverse proxy with authentication (e.g., TLS client certificates, OAuth proxy).
- **Database Exposure:** PostgreSQL should remain bound to localhost (`127.0.0.1`) even if the API and Dashboard are exposed. Agents communicate via the API, not directly to the database. Do not expose PostgreSQL to the LAN unless absolutely necessary.
