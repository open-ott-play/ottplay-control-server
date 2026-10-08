# Security policy

Report vulnerabilities through this repository's [private vulnerability reporting form](https://github.com/open-ott-play/ottplay-control-server/security/advisories/new). Do not place tokens, configuration files, or customer command payloads in public issues.

Only the latest release receives security fixes. Use unique device tokens and a separate administrator token. Serve HTTPS when commands cross an untrusted network. Plain HTTP is supported for legacy players on a trusted LAN; it does not encrypt credentials or commands.

## Response commitments

Maintainers aim to acknowledge private reports within 14 days; follow up
privately if there is no response. Triage confirmed issues by impact, prioritize
critical defects, and coordinate remediation/disclosure with the reporter.
Security changes must have release notes with affected versions and upgrade
actions. Fixes target the current default branch and latest release, rather than
unmaintained historical versions. These are project policies, not assertions
about the existence or response times of past reports.

See [security design](docs/security-design.md) for project-specific trust boundaries.
