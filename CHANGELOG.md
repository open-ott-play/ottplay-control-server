# Changelog

## Unreleased

### Added

- Explicit media pages with `ott PLAYER s --limit N [--offset N] [TEXT]`,
  totals and next-page commands. Complete listings and title selection remain
  complete, skip unnecessary category pages, and wait within their deadline
  when read submissions are rejected because retained request results fill the queue.
- Capability-gated media categories in `ott`: list with `categories`/`cats`,
  browse with `category`/`cat`, and select opaque category/video IDs. Updated
  Plex and standalone VPortal players supply category-scoped, paginated
  catalogues; legacy players retain their existing channel commands.
- Bind media title playback to a runtime/source/catalogue snapshot and stable
  video ID. Reject stale pages and incomplete name searches without retrying
  playback. Browsing preserves the active playback category and explicit queue.
- Validate category requests and receipts in the controller, including scoped
  operator permissions, pagination bounds and read-only inspection access.
- OTT Home 0.2.0 adds a visible, persistent boot-start opt-in for dedicated
  Android tablets whose firmware does not offer a third-party Home picker.
  Existing default-Home behavior is preserved, fresh installs remain opt-out,
  and blocked launches are recorded without claiming playback recovery.
- Document repeatable Plex strict-kiosk setup, local connectivity checks,
  separate boot/wake acceptance and rollback in English and Russian.

### Fixed

- Reject player aliases that conflict with top-level CLI commands before writing
  configuration or provisioning credentials. Preserve recovery of an unfinished
  registration under its original name and journal. Existing mappings remain
  intact; register another alias for the same device UUID to control it normally.
- Guide unbound Local videos profile imports through loading the profile before
  connecting and binding sources, then reloading it after scanning completes.

## [0.1.5] - 2026-10-20

### Added

- Share one hardened USB ADB container across an explicit Android registry,
  with persistent authorized keys, serialized maintenance and per-device boot
  recovery state. Protect shared forwarding ports from cross-device rebinding
  and use verified unique serials so ADB restarts cannot retarget commands.
- Provide Synology/systemd installation, SSH commands and a legacy-path redirect.
  Unknown identities and duplicate serials remain blocked; normal screen sleep
  does not trigger automatic wake or playback recovery.
- Provide a maintained ADB/Python transport image, English/Russian guides and
  architecture diagrams. Bound container memory and swap, disable inherited
  health checks and stream APK staging to avoid a second full in-memory copy.
- Confine USB screenshots to private capture directories, persist recovery
  checkpoints durably and support both legacy and modern Android process lists.

- Local videos (`offline`) profile save/load and portable export/import in the
  CLI, with capability-aware examples in `ott PLAYER`, strict request/reply
  validation and independent operator scopes. Exports use private, atomic new
  files and never include videos, absolute filesystem paths or device access grants.
  Same-installation bindings may be reused; other devices require local binding.

### Changed

- Include capability-advertised Capacitor update commands in bare player help
  and label their RPC receipts as `app_update`. Accept case-insensitive update
  subcommands while preserving APK URLs and hashes. Installer acceptance remains
  separate from confirmed installation.
- Show `standby` and `go` in player controls. `go` wakes the player using the
  existing lifecycle operation; `stop`, `start` and `wake` remain compatible
  aliases. Document acknowledgement, remote connectivity and reserved channel
  names without claiming an accepted request proves completed playback changes.
- Validate and display optional player standby/background activity snapshots,
  distinguishing pending or unsupported cancellation from reported suspension.
  Native Android agent commands accept the same standby/go aliases.
- Remove VPortal from M3U setup entries, profile output and exact settings
  comparisons. Legacy `vportal` strings in named setups are accepted but never
  sent to players. Standalone VPortal profile commands remain available;
  direct VPortal updates require the VPortal provider. Existing device values
  are preserved. Update the controller, CLI and player together.

### Fixed

- Preserve the `.apk` suffix on files staged for Android USB installation,
  including API 22 devices. ADB can recognize the package before transfer;
  unique private staging and cleanup remain unchanged.
- Include the six Android USB runtime scripts in the deployment archive and
  test the unpacked entry points independently of a source checkout.
- Share strict recovery-checkpoint validation between legacy and shared USB
  supervisors; malformed counters and boot IDs fail safely before recovery.
  Unlabelled containers occupying the configured name are refused cleanly.
- Bound USB file pulls to one regular file of at most 128 MiB with a kernel
  file-size limit, and clean up only temporary files owned by that transaction.
- Advertise only supported diagnostic lanes for `debug` and `operation`.

### Upgrade

- The deployment archive includes the Android USB manager source and transport
  image recipe. Install all manager modules from the same release, preserve
  existing authorized ADB keys, and disable old per-tablet recovery services
  before enabling the shared service. Device registration and USB-host setup
  remain explicit local operations; publication does not reconfigure tablets.
- Update the controller, CLI and a matching Local videos-capable player together
  for `profile_save`, `profile_export` and `profile_import`. Existing profile
  selectors and M3U/VPortal settings remain compatible. Import into an inactive
  profile slot; saving or importing configuration does not start playback.
- On another installation, load the imported profile to open local recovery,
  connect and bind its sources locally, then load it again. Publishing this
  controller release does not update or deploy players.

### Security

- Portable profiles accept only bounded, allowlisted configuration fields.
  Filesystem handles, bookmarks, native references and access grants stay on the
  originating installation. A profile's binding identifier does not grant access
  to another device's files.
- Keep exports private: source labels and selected relative filenames may expose
  personal information. Device-scoped authorization and player capabilities
  remain enforced; automated checks do not establish physical-player acceptance.

## [0.1.4] - 2026-10-18

### Changed

- Add the optional OTT Home Android launcher companion and ADB setup guide. Target Android API 24 for modern installation while retaining minimum API 22. Update pinned CI tooling and declare code owners.

### Upgrade

Controller protocols and persistent formats are unchanged. Installing the optional launcher and selecting a Home application remain explicit device operations.

### Security

The companion requests only the normal boot permission; it performs no network or file-sharing operations. Target-device acceptance remains necessary.

## [0.1.3] - 2026-10-17

### Changed

- Use the Go 1.27.2 toolchain consistently for host validation, release binaries,
  Android-agent tests and fuzzing, matching the existing container builder.
  This fixes the upstream fuzz-coordinator deadline race (golang/go#75804)
  while preserving the Go 1.26 language baseline.

- Support explicitly requested debug version 2 attached-engine observations via
  `ott PLAYER debug --engine`, with strict engine enums and request-version
  binding. Preserve version 1 requests and payloads for existing players and CLIs.

- Document project governance, confidential conduct reporting, maintenance continuity limits and reviewed OpenSSF evidence.
- Add an optional stronger Python TLS profile that preserves CA/hostname verification and cannot lower host security settings; test exact weak-key boundaries before credentials are sent.

- Keep response-route authorization and dispatch on the same normalized path;
  trailing-slash variants cannot submit command envelopes with device credentials.
- Round durable journal deadlines upward to retain idempotency through the
  precise queue expiry, including fractional-second boundaries.

- Add exact device/action-scoped ordinary command operators, an optional private
  bounded outcome journal with idempotency keys, and crash recovery that retains
  unknown outcomes without replaying commands.
- Add `load PRESET plan|apply|resume|verify` with private checkpoints and exact
  player-side settings comparison, while preserving plain `load PRESET`.
- Add explicit `handoff SOURCE TARGET` for same-source live/archive playback,
  transferring stable references rather than stream URLs or credentials.
- Add English/Russian reliable-control guides, localized README and wiki index,
  and an accessible SVG explaining command, checkpoint and source ownership.

### Upgrade

Update the controller and CLI together. Reliable preset application and playback
handoff require the matching player capabilities; older players and the previously
published FOSS stable must not be assumed to implement them. Keep plain preset
loading for older players. Configure scoped operators and the optional outcome
journal explicitly, with private storage and a retained backup of configuration.
Controller publication does not deploy the player, maintenance agent or cluster.
Keep the previous binary and configuration for rollback, and inspect unresolved
outcomes before retrying commands after a restart.

### Security

Authorization and dispatch use the same normalized response path. Journal
persistence is bounded and private; unknown outcomes remain unknown after a crash
and are not replayed automatically. Handoff transfers stable references rather
than stream credentials. The optional strict Python TLS profile retains CA and
hostname checks and stronger host settings. Offline and paired synthetic checks
do not establish physical-player, provider or production-cluster acceptance.

## [0.1.2]

### Changes

- Qualify this release with a bounded RC fuzz campaign, offline target execution,
  and deterministic seed replay checks. Bind release evidence to the exact source
  and toolkit revision, validate seed corpora, and retain engine execution counts
  and diagnostic artifacts. Document the fuzzing scope, resource limits, and the
  distinction between synthetic checks and target acceptance.

- Correct kiosk status output for Plex queues and armed strict mode, and retain
  optional media startup-grace and screen-suspension metadata in CLI receipts.
  The Android agent now starts its 120-second watchdog grace when it first
  observes a lock, rather than counting time before the lock was observed, and
  gives recovery a separate deadline after completing a health read.

- Add a local signed-update preparation and HTTPS verification tool for the
  Android maintenance agent. Native status now reports fresh installed agent,
  APK and touch-helper hashes, APK version and enabled state, so delivery can
  be checked independently of an accepted command receipt. Agent-only delivery
  does not require launching an intentionally disabled player.

- Add signed coordinated APK/touch-helper bundles with exact embedded-helper
  matching, bounded archive validation, saved rollback pairs and pending
  transaction recovery. Installation requires an enabled, foreground player;
  it never changes HOME selection or updates the broker/startup supervisor.
  Host tests cover failure paths; a physical rollout is a separate acceptance
  step, and APK data migrations must remain compatible with rollback.
  Standalone APK updates also require an enabled, foreground app and an
  unchanged embedded helper. Failed pair recovery keeps read-only diagnostics
  and signed agent repair available while blocking further app mutations.

### Upgrade

The controller, CLI, hosted player and Android maintenance agent are separate
installations. Updating one does not replace the others. Follow the native
agent's signed-update and rollout instructions; a command acknowledgement is
not proof that the new binary is running or that playback recovered.

Keep the previous immutable release for rollback. Test the exact RC package on
the intended target before accepting a stable promotion.

### Security

Fuzz targets and deterministic preflight commands run offline on synthetic
inputs. Setup and dependency installation may use the runner network. The
campaign rejects missing or invalid seed corpora and incomplete execution
evidence. These checks do not establish untested hardware, integrations,
credentials, or deployment behavior.

## [0.1.1]

### Changes

- Add bounded native Go fuzz qualification for public release candidates, with
  source-bound evidence, synthetic seeds and documented reproduction. PRs, betas
  and nightlies retain ordinary tests without mutation campaigns.

- Add `stop` / `start` aliases for acknowledged standby / wake. Status shows
  these controls when supported and distinguishes full-player `restart` from
  `restart stream`, including explicit and short aliases. Stop keeps remote
  control online; start follows the player's startup settings. Kiosk/settings
  restrictions and uncertain-response handling are unchanged.

- Show provider selection in the status command's available controls, including
  `provider m3u` / `prov m3u` and provider listing. M3U and VPortal status also
  show numbered profile commands, including when the catalog has no channels.
  Kiosk status explains blocked switching while preserving read-only listings.
  These hints add no remote requests and preserve player policy checks.

- Add `fav add [NAME]`, `fav`, `fav play ID_OR_NAME` and `fav remove ID_OR_NAME`
  with long favorite/favourite aliases. The target player captures its current
  item and profile, stores a durable reference and restores that profile before
  resolving playback. The controller validates bounded, runtime-bound metadata;
  the CLI requires the optional `favorites` capability and never repeats an
  uncertain request. Existing provider, kiosk and parental checks still apply.

- Add `plex libraries`, `plex library [--shuffle] [--preview] NAME` and
  `plex shuffle NAME`. The player resolves a complete movie/show library using
  its own saved Plex connection, then reuses the existing queue. Explicit
  `id:NUMBER` selection avoids ambiguous names. Capability checks, bounded
  results and runtime-bound errors preserve older queue commands and prevent
  partial or automatically repeated playback requests.

- Explain when `restart stream` has no current stream to restart, including
  responses from older players. Keep unsupported-player errors distinct, omit
  arbitrary player error text, and never retry or reload automatically.

- Add read-only `debug`/`dbg` player snapshots with bounded runtime, media,
  lifecycle and native metrics, using a separate capability compatible with
  existing inspection clients. Bundles retain two files and verify debug
  observations under `workbench-v3`; legacy reports remain verifiable.
- Add administrator-only `GET /api/debug` and `ott server debug` for independently
  sampled process, stored queue/result and diagnostics-service counters. Reads
  do not expire queued state or expose device IDs and payloads.

- Update strict-kiosk help and examples for local timeline seeking within a
  VPortal/Plex episode on supported players; older players keep a read-only footer.

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

Current-item favorites require a matching CLI, controller and player advertising
`favorites`. Entries remain in the target player's persistent storage and are
not transferred by a controller upgrade or shared between device installations.

Plex library commands require a CLI, controller and player implementing the new
`plex_library` capability. Existing `plex_queue` support alone is insufficient;
an unsupported player receives no library command. Local presets and saved
credentials are unchanged. Existing parental, kiosk and repeat policies apply.

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

Keep all seven CLI Python files from the same revision together. The workbench
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
