# Changelog

## [0.1.1] - Development line

### Changes

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

### Upgrade

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
