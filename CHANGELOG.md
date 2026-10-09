# Changelog

## [0.1.1] - Development line

### Changes

- Add explicit `aspect` readback and `aspect fit|fill` controls, including the
  full names `Fit to screen` and `Fill screen`. The CLI checks support before
  sending runtime-bound requests and reports saved-setting readback;
  Fill preserves proportions and crops the edges. The existing `key aspect`
  input remains separate. Changes execute only after acknowledgement and
  remain subject to player kiosk, PIN and protected-input restrictions.

- Android native diagnostics now expose shared web media identity when available,
  native runtime/OS boot markers, and bounded app-surface/audio evidence.
  A private 64-entry, 24-hour operation history survives reloads and agent
  restarts; `android operation ID` reads receipts without replaying effects.
  Physical display/audio and successful recovery are not inferred from counters
  or handler completion. Existing separate native binding and V1 requests remain.

- Direct HTTPS validates all supplied certificate keys before listening and
  serves the checked key pair without reloading it from disk.
- The 0.1.1 development line adds bounded remote screenshot requests, strict
  kiosk controls, shared playlist/EPG resolution and ordered Plex queues with
  a read-only preview. The separate Android maintenance agent supports native
  maintenance operations, including KitKat devices.
- Add `doctor`, `inspect`, `operation`, private evidence bundles and fixed
  `health`/`media-progress` scenarios. Optional JSON request receipts expose the
  request ID without changing ordinary command output. Inspection validates
  runtime, section and operation binding; progress checks reject incompatible
  identities and invalid capture-clock observations.
- Release publication now reads this version's reviewed notes from the exact
  source commit and verifies them before writing publication evidence or
  advancing the durable publication counter.

- Version updates preserve quoted TOML keys containing `=` or `#`, including
  unrelated keys, without changing comments or surrounding file layout.

- Release notes accept optional closing hashes in Markdown headings while
  continuing to reject duplicate version sections.

- The localhost HTTPS diagnostics test fixture explicitly requires TLS 1.2 or
  newer while retaining certificate and redirect validation coverage.

### Upgrade

Aspect commands require a matching updated CLI, controller and player. An older
player without the capability receives no aspect mutation. The player's existing
storage scope remains authoritative: the current live channel, or the shared
media setting for VOD. Read `aspect` after a change; an accepted request does not
by itself confirm application or persistent storage.

If using `--tls-cert` and `--tls-key`, replace any certificate chain containing
RSA keys below 2048 bits or ECDSA keys below 224 bits before upgrading. Ed25519 is
also supported; other key types are rejected. A reverse proxy still needs its
own TLS policy.

Retain server configuration, private CLI presets and pairing credentials. Update
the player alongside the controller when adopting new remote commands; capability
checks still determine which commands a device supports. Queues remain ephemeral
and single-replica. Test the chosen binary/container and target player before
replacing a running installation; this change does not deploy it.

Keep all six CLI Python files from the same revision together. The workbench
commands are not part of stable CLI v0.1.0 and require a player advertising the
corresponding inspection capabilities. Separately bound Android maintenance
observations remain a distinct lane. Evidence exports require a new directory;
keep previous exports and choose another location after an incomplete export.

### Security

Go healthcheck and Android maintenance-agent HTTPS requests now reject
undersized keys throughout verified peer chains, including the trust anchor,
before sending an HTTP request. Existing trust and hostname checks still apply,
and resumed connections receive the same key check. Replace affected controller,
reverse-proxy or CA certificates before upgrading these clients.

The direct HTTPS listener now rejects undersized keys in the supplied leaf,
intermediate and included root certificates before accepting connections.
Client certificate verification and HTTP-client trust policies are unchanged.

Keep the controller behind the documented HTTPS/authentication boundary and treat
screenshots, diagnostic responses and saved provider tokens as private data.
Confidential vulnerability reporting is documented in SECURITY.md. This release
tooling update rejects missing or incomplete notes before publication state changes;
it does not announce a new application CVE.

Inspection and scenarios do not repair, start playback or capture a screen.
Existing credentials and device policy remain authoritative. Exported observations
exclude credentials, provider URLs, screenshots and raw exceptions; POSIX exports
use private directory/file permissions. Missing replies remain unknown and are
not automatically retried as mutations. Neither a request receipt nor advancing
decoder time proves physical presentation.
