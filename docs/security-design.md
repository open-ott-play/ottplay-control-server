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
authentication. When TLS is
terminated by a reverse proxy, the proxy needs its own certificate policy.
Setting `GODEBUG=fips140=on` alone is not a substitute: the tested Go 1.26.9 server
accepted its own RSA-1024 certificate with that setting before this check existed.

## Outbound HTTPS certificate policy

The Go healthcheck and the Android maintenance agent retain ordinary certificate
chain, validity and hostname verification. After that verification succeeds,
`VerifyConnection` requires at least one complete verified chain whose leaf,
intermediates and trust anchor all meet the same key minimums: RSA 2048 bits,
ECDSA 224 bits or a 32-byte Ed25519 key. The check also runs on resumed TLS
connections. An alternate valid strong chain remains acceptable even when a
weaker alternate path exists.

Upgrade controller/proxy certificates or trust anchors that use smaller keys
before updating clients. No custom CA is added by this policy: healthcheck uses
the system store; the Android agent preserves its existing system-plus-bundled
roots and custom dialer. HTTP behavior, redirect restrictions and timeouts are
unchanged. This policy does not govern external browsers, reverse proxies or the
Python CLI's separate TLS implementation.

## Python CLI HTTPS profile

The supported CLI runtime is CPython 3.12 or newer with standard OpenSSL defaults
at security level 2 or higher. Its `urllib` HTTPS handlers use the default
verified context; the CLI does not substitute an unverified context or reduce
the security level. Python documents the TLS 1.2 minimum and rejection of
RSA/DH keys below 2048 bits and ECC keys below 224 bits in these
[SSL context defaults](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext).

A local verification used CPython 3.12.14 with OpenSSL 3.5.8 and security level 2.
The actual controller, EPG and diagnostics openers each accepted a synthetic
RSA-2048 certificate chain and rejected RSA-1024 keys in the leaf, intermediate
or trusted root before any HTTP request or authorization header reached the
loopback server. This evidence applies to that runtime profile, not every
Python build or external reverse proxy. Older runtimes and vendor overrides
were not verified. Use the [runtime check](cli.md#check-the-python-and-tls-runtime)
with the interpreter that launches the CLI; retain ordinary CA and hostname
verification and replace weak server keys instead of weakening local defaults.

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

## Supported strong-key CLI configuration

For the complete direct-HTTPS configuration, run the Go server with its validated
`--tls-cert`/`--tls-key` chain; use HTTPS controller, EPG and archive endpoints;
and set `OTT_TLS_SECURITY_LEVEL=3` for Python CLI commands. This setting applies
to the controller, EPG, diagnostics and both archive redirect policies. It keeps
the normal CA/hostname verification and raises OpenSSL's minimum key strength to
128 bits (for example, RSA/DH 3072 and EC 256). It never lowers a stricter
platform setting. Values outside 2–5 fail before creating a network connection.

```sh
OTT_TLS_SECURITY_LEVEL=3 python3 cli/ott.py --config /private/path/client.json devices
```

Use certificates and trust anchors meeting this profile throughout the chain.
The usual default context remains compatible with existing RSA-2048 endpoints;
level 2 alone is not an exact RSA-2048-bit guarantee because OpenSSL may round
its security estimate for a 2047-bit key. The stricter profile intentionally
rejects RSA-2048 too. Upgrade endpoint certificates before enabling it.

The Android agent and Go healthcheck already validate actual verified-chain
key lengths. In this supported configuration, do not route traffic through a
separate TLS terminator or an HTTPS proxy whose certificate policy has not been
assessed. Plain HTTP remains an explicit legacy trusted-LAN configuration; it
provides neither confidentiality nor a strong-TLS claim. No deployment setting
has been changed by this documentation.

`tests/test_strict_tls.py` exercises the actual Python client openers over TLS
1.2 and 1.3. RSA-3072 chains succeed; 2047-bit leaf, intermediate and trust-anchor
keys fail before HTTP requests or synthetic credentials reach the server.

## Scoped command operators

Ordinary RPC and legacy command submitters can use separate credentials without
receiving the administrator credential. Add `operators` to the private server
configuration. Each entry has `id`, `credential_sha256` (the lowercase SHA-256
hex digest of a randomly generated 32–256 character bearer), `device_ids`, and
`actions`. IDs and digests must be unique; credentials must differ from device,
administrator and diagnostic credentials. Configuration is applied on restart.

Actions name the exact RPC, for example `status`, `profiles`, `settings_compare`
or `profile_settings`. RPCs with sub-operations require the exact operation:
`input.channel_up`, `playback.next_channel`, `aspect.get`, `favorites.list`,
`kiosk.status`, `app_update.status` or `command.set_volume`. No wildcards or
family-wide `input`/`command` grant is accepted. The supported allowlist is in
`internal/config/operators.go`. A `provider_settings` grant permits its validated
provider settings on the scoped device, so do not give it to a remote-button bridge.
Legacy command submissions and commands nested in RPCs share `command.*` scopes.

Operators can only read receipts they created and only while their device/action
scope still permits them. They cannot list all devices, read admin debug data,
poll device work, acknowledge work, pair devices, or access diagnostics. Explicit
`input.*` grants permit those physical-button equivalents, including their UI
navigation effects; they are not a read-only application permission.

Use a separate private CLI credentials file containing `operator_token` rather
than `admin_token`, and point the CLI's `server_config` at it. Resolve players
through the CLI `players` map. Keep administrator and operator files separate:
the operator cannot administer registrations. The existing administrator path and
player local consent/parental/kiosk checks remain unchanged.
