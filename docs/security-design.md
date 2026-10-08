# Security design and verification

## Scope and trust boundaries

The project provides a Go command queue, diagnostics service and CLI for OTT-play players.

An administrator token authorizes command submission; each device has a distinct token. Configuration, pairing data, diagnostic captures and command payloads may contain secrets. Keep credentials separate and rotate exposed values. The default all-interface listener is reachable on the LAN; terminate verified HTTPS before crossing an untrusted network. Legacy plaintext HTTP does not protect credentials or commands.

## Direct HTTPS certificate policy

The `serve --tls-cert ... --tls-key ...` path loads and checks its key pair before
opening the listener. Every supplied certificate, including intermediates and an
included root, must use RSA with at least 2048 bits, ECDSA with at least 224 bits,
or a 32-byte Ed25519 public key. Other key types are rejected. The listener serves
that validated in-memory chain rather than reopening the files after validation.
TLS itself still determines which supported certificate algorithms can be used
in the negotiated protocol version.

An undersized or invalid key pair stops startup with a generic error that does
not print certificate paths or contents. Replace such a certificate before
upgrading. TLS 1.2 remains the minimum protocol version. A local regression test
builds and runs the real binary: RSA-1024 in the leaf, intermediate or supplied
root is rejected; RSA-2048, P-256, Ed25519 and a mixed strong chain complete
verified HTTPS to a loopback health endpoint.

This check concerns the chain configured on this listener. Connecting clients
still validate certificate trust, hostname and validity; their trust stores and
any omitted root are outside this check. It does not introduce client-certificate
authentication or change the healthcheck/native-agent HTTP clients. When TLS is
terminated by a reverse proxy, the proxy needs its own certificate policy.
Setting `GODEBUG=fips140=on` alone is not a substitute: the tested Go 1.26.8 server
accepted its own RSA-1024 certificate with that setting before this check existed.

## Cryptographic implementation evidence

Server credentials, pairing secrets and request identifiers are generated with
the Go standard library's `crypto/rand`; see
[configuration](../internal/config/config.go),
[pairing](../internal/control/pairing.go) and
[request creation](../internal/control/requests.go). The native-agent signing
utility uses `ed25519.GenerateKey(rand.Reader)` and Ed25519 signatures, with
SHA-256 for envelope integrity; see
[sign-update](../native/android-agent/cmd/sign-update/main.go).
These mechanisms use published algorithms and the FLOSS Go implementation.
This evidence does not establish the certificate-key policy of every client,
reverse proxy or legacy HTTP deployment.

## Source and operating documentation

- [internal/config/config.go](../internal/config/config.go)
- [internal/control/server.go](../internal/control/server.go)
- [docs/api.md](../docs/api.md)
- [docs/remote-diagnostics.md](../docs/remote-diagnostics.md)

## Regression evidence

- [internal/control/diagnostics_auth_test.go](../internal/control/diagnostics_auth_test.go)
- [internal/control/server_test.go](../internal/control/server_test.go)
- [tests/test_pairing_cli.py](../tests/test_pairing_cli.py)
- [cmd/ottplay-control-server/tls_test.go](../cmd/ottplay-control-server/tls_test.go)

Run the documented commands in [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[CI workflow](../.github/workflows/ci.yml). Preserve negative tests for rejected inputs,
unavailable dependencies, authorization failures and cancellation. A passing
test run describes its fixtures and environment; it does not certify every
upstream service, hardware model or production deployment.

## Remaining security assessment

Evaluate legacy HTTP applicability, warning policy and dynamic-analysis coverage for Go and the Android agent. Verify current release notes describe user changes and security fixes rather than only build provenance.

Report new issues through [SECURITY.md](../SECURITY.md). An OpenSSF assessment
records evidence and applicability; it is not a guarantee that a system is safe.
