# Changelog

## [0.1.1] - Development line

### Changes

- The 0.1.1 development line adds bounded remote screenshot requests, strict
  kiosk controls, shared playlist/EPG resolution and ordered Plex queues with
  a read-only preview. The separate Android maintenance agent supports native
  maintenance operations, including KitKat devices.
- Release publication now reads this version's reviewed notes from the exact
  source commit and verifies them before writing publication evidence or
  advancing the durable publication counter.

### Upgrade

Retain server configuration, private CLI presets and pairing credentials. Update
the player alongside the controller when adopting new remote commands; capability
checks still determine which commands a device supports. Queues remain ephemeral
and single-replica. Test the chosen binary/container and target player before
replacing a running installation; this change does not deploy it.

### Security

Keep the controller behind the documented HTTPS/authentication boundary and treat
screenshots, diagnostic responses and saved provider tokens as private data.
Confidential vulnerability reporting is documented in SECURITY.md. This release
tooling update rejects missing or incomplete notes before publication state changes;
it does not announce a new application CVE.
