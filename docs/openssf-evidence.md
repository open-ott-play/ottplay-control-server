# OpenSSF Best Practices evidence

This evidence index supports the project's own Passing and Silver self-assessments. The public badge service is authoritative for awarded status. A planned or partially completed criterion is not an award.

## Project, participation and releases

Go controller, Python control/diagnostic CLI, Android Go maintenance agent and deployment/release tooling.

The [README](../README.md) explains installation and interfaces. [CONTRIBUTING](../CONTRIBUTING.md) gives the issue/PR process, standards, test commands and review policy. [GOVERNANCE](../GOVERNANCE.md), [Code of Conduct](../CODE_OF_CONDUCT.md) and [ROADMAP](../ROADMAP.md) record roles, private conduct reporting, priorities and the missing backup-maintainer arrangement. Source is public Git under [MIT](../LICENSE), with third-party notices preserved.

The latest reviewed stable source/release is [v0.1.2](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2); its human-readable notes describe changes, upgrade implications, security scope and exact source. [RELEASING](../RELEASING.md) and [CHANGELOG](../CHANGELOG.md) define future release expectations. This work does not deploy a device or publish a new stable release.

## Testing and analysis

490 Python tests passed locally (one platform-dependent case skipped); the new strict-profile test includes TLS 1.2/1.3, RSA-3072 success and 2047-bit leaf/intermediate/root rejection before HTTP credentials. Required CI also runs Go race/vet/vulnerability checks, Android and four desktop/host platforms, binary/container smoke and deployment packaging.

The [test sources](../tests), [CI workflows](../.github/workflows), [quality limits](quality.md) and [security policy](../SECURITY.md) retain the exact commands and scope. Automated tests execute with assertions. Required analysis includes CodeQL on the maintained languages; practical language/syntax checks remain enabled. New confirmed findings must be fixed or narrowly explained, never hidden by disabling a gate.

The v0.1.2 release records three native Go fuzz targets at source `675004d00afb8af066ea53fd79f63d62a27b8811`, run [38844308604](https://github.com/open-ott-play/ottplay-control-server/actions/runs/38844308604): result binding, Android binary manifest and native parameters. This historical campaign is not a claim of new mutation fuzzing on the current CLI change.

## Security and human evidence

[The security design/profile](../docs/security-design.md) documents active trust boundaries, default behavior and the complete supported strong-key configuration. Crypto claims apply to that configuration; they do not certify arbitrary third-party infrastructure or deployment settings.

On 16 October 2026 the authenticated issue/advisory inventory contained no non-PR issues or advisories, and Dependabot contained no open alerts. Visible code-scanning findings were Scorecard process findings; an empty advisory inventory is not a claim that no vulnerability can exist. Private GitHub reporting was enabled. History scan: two generic-key matches were synthetic unit-test idempotency keys and a deliberate `TOP_SECRET` redaction sentinel, not live credentials.

The maintainer's secure-design/common-vulnerability knowledge and no outside-GitHub reports in the preceding 12 months are recorded in [quality limits](quality.md). These are human statements; they are not inferred from test results.

## Honest Silver progress

No backup person or tested one-week recovery arrangement exists, so access continuity and bus factor remain unmet. Complete cross-language statement coverage, systematic accessibility/internationalization, independent penetration testing and a full assurance case are not established. See [quality limits](quality.md); individual criteria remain separately assessed.
