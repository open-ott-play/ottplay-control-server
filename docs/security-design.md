# Security design and verification

## Scope and trust boundaries

The project provides a Go command queue, diagnostics service and CLI for OTT-play players.

An administrator token authorizes command submission; each device has a distinct token. Configuration, pairing data, diagnostic captures and command payloads may contain secrets. Keep credentials separate and rotate exposed values. The default all-interface listener is reachable on the LAN; terminate verified HTTPS before crossing an untrusted network. Legacy plaintext HTTP does not protect credentials or commands.

## Source and operating documentation

- [internal/config/config.go](../internal/config/config.go)
- [internal/control/server.go](../internal/control/server.go)
- [docs/api.md](../docs/api.md)
- [docs/remote-diagnostics.md](../docs/remote-diagnostics.md)

## Regression evidence

- [internal/control/diagnostics_auth_test.go](../internal/control/diagnostics_auth_test.go)
- [internal/control/server_test.go](../internal/control/server_test.go)
- [tests/test_pairing_cli.py](../tests/test_pairing_cli.py)

Run the documented commands in [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[CI workflow](../.github/workflows/ci.yml). Preserve negative tests for rejected inputs,
unavailable dependencies, authorization failures and cancellation. A passing
test run describes its fixtures and environment; it does not certify every
upstream service, hardware model or production deployment.

## Remaining security assessment

Evaluate legacy HTTP applicability, warning policy and dynamic-analysis coverage for Go and the Android agent. Verify current release notes describe user changes and security fixes rather than only build provenance.

Report new issues through [SECURITY.md](../SECURITY.md). An OpenSSF assessment
records evidence and applicability; it is not a guarantee that a system is safe.
