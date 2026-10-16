# Android maintenance agent (experimental)

An independent, root-owned agent for the Matricom G-TAB IOTA/Q1001L4B2,
Android 4.4.2/API 19, with the existing `play.ott.kitkat` wrapper and OTT touch
supervisor. It polls HTTPS without depending on the WebView. No inbound port,
network ADB, arbitrary shell command, or caller-supplied JavaScript is exposed.

**Hardware acceptance is still required.** Host tests and ARMv7 compilation do
not establish that this binary runs under the tablet's kernel/SELinux policy.
Do not replace the installed wrapper or remove USB until native status, capture,
restart, queue transitions and touch lock have been verified on the device.
A frozen kernel, disconnected Wi-Fi, or loss of power still requires local access.

## Bootstrap

Provision a **separate** controller device/token, e.g. `a1-native` using
`ott add a1-native UNIQUE_NATIVE_DEVICE_ID`. Protocol 1 has one queue per token;
sharing the WebView token races delivery and is explicitly unsupported.

1. From this directory, `go run ./cmd/sign-update generate /PRIVATE/update.key`.
   Save its public output in `update_public_key`. Keep the signing key offline.
2. Copy `config.example.json` to a private file (`chmod 600`), filling the
   native token, separate native/WebView identities, exact hardware serial,
   HTTPS controller and trusted player origin. Never publish that file.
3. From the repository root, `python3 native/android-agent/build.py`.
   Extract `dist/ottplay-control-server-android-agent.tar.gz` privately.
4. With root USB ADB available for the exact device, run
   `python3 install.py --serial USB_SERIAL --config /PRIVATE/config.json --binary ./agent`.
   The installer refuses unknown startup scripts and preserves the touch service.
5. `ott a1 android bind a1-native`, then `ott a1 android status`.

The agent and private state live in `/data/local/ott-remote` (root, mode 0700).
The startup supervisor runs it separately from the existing touch broker.
The installer restores `/system` to read-only after replacement. Rollback:
stop `flash_recovery`, restore the saved `boot.previous` to
`/system/etc/install-recovery.sh` with mode 0750, remount `/system` read-only,
start `flash_recovery`. Remove the native directory only after stopping it.

## Commands

```
ott a1 android status
ott a1 android logs
ott a1 android operation 0123456789abcdef0123456789abcdef
ott a1 operation 0123456789abcdef0123456789abcdef --lane native --json
ott a1 android screenshot -o a1.png
ott a1 android recover
ott a1 android restart
ott a1 android reload
ott a1 android reboot
ott a1 android queue play 47677 44819 47674
ott a1 android queue status
ott a1 android queue next
ott a1 android queue prev
ott a1 android queue restart
ott a1 android queue stop
```

Queue play uses the configured VPortal profile and its allowlisted hosted route.
It preserves the requested order, resolves all item metadata before changing the
current queue, enters looping kiosk mode and preserves the prior strict setting.
Restart starts item zero at position zero; next/prev wrap within the current queue.
Stop explicitly exits kiosk and stops playback. Provider/profile setup continues
through the existing `ott` WebView commands. No `Local` profile is overwritten.
Queue operations require a responsive page at the configured trusted origin.
They cannot manipulate a dead WebView: use native restart first.

`android pause`, `resume`, `seek SECONDS` use the active video element. **Pause
requires kiosk off:** the installed web kiosk intentionally restarts paused media.
`wake`/`standby` send Android's named wake/sleep key codes; support depends on the
firmware and needs hardware acceptance. These do not mean power-on from shutdown.

Automatic recovery operates only after observing a locked kiosk. It waits for
120 seconds without playback progress after that observation, first cycles the
video surface and then restarts the app. Checks run approximately every 15 seconds;
unresponsive playback can go directly to an app restart. Increasing cooldown
limits each stalled period to three attempts. Changing episodes alone does not
reset that limit: it is re-armed by more than five minutes of sustained playback
progress or by observing kiosk disabled before a new lock.
It never reboots the device automatically. An explicit native pause/standby
suspends this watchdog. Continuous clock progress with a frozen hardware surface
cannot be detected reliably; `android recover` also handles that case manually.

Lifecycle/update receipts say `accepted`, with `completion: inspect_status`.
They confirm admission, not successful reboot/update. Inspect status afterward.
A private bounded journal prevents re-execution after duplicate delivery or crash;
uncertain operations must not be blindly retried. Screenshots are not retained on
disk and a lost screenshot reply requires a new explicit capture request.
Logs are a bounded ring of agent event codes, not unrestricted Android logcat.

## Signed updates over Wi-Fi

After the one-time root USB bootstrap, the independent agent can install its
own binary and the `play.ott.kitkat` APK over its existing outbound HTTPS
connection. USB and a responsive WebView are not needed for downloading or
installing an agent-only update. In the 0.1.2 agent, APK-only updates require
the wrapper to be enabled and in the foreground; coordinated bundles also
require a responsive player. Wi-Fi, the controller, the update host and the native
agent must remain reachable. This updates application components, not Android
firmware, the kernel or WebView.

The native binding and pinned public key must already exist. Do not generate a
new signing key for each release: it will not match the key installed on the
tablet. Keep the existing private key and a recovery copy outside the repository
and public hosting. Never upload the device configuration, tokens or signing key.

### Prepare and verify a delivery

From a reviewed source checkout containing the 0.1.2 tooling, prepare a new
private output directory. This example assumes that the actual verified release
version is `0.1.2-beta.1`; substitute the selected release's version and paths:

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.1 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.1/agent \
  --manifest-url https://updates.example/0.1.2-beta.1/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.1
```

The tool validates the payload type and prepares the payload, `manifest.json`,
`signed.json` and a private `release.json` receipt. The output directory must not
already exist. It uses the existing Go signer and requires its toolchain. Publish
only the prepared payload and `signed.json` to their exact direct HTTPS URLs,
then run:

```sh
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.1
```

Verification downloads both files without redirects and compares their exact
bytes to the prepared files. Both commands print bounded hashes and metadata;
neither command publishes a file, changes the device or sends an update request.
The successful receipt's `envelope_sha256` is the value for `android update`.
Use `--kind apk` for a compatible APK-only update or `--kind bundle` for the
coordinated format described below. This preparation tool does not replace
release-provenance checks or Android's signing-certificate verification.

### Signed wire format

Create a UTF-8 manifest with exactly these fields:

```
{"schema":1,"kind":"agent","version":"0.1.2-beta.1","package":"play.ott.kitkat","url":"https://HOST/immutable-release/agent","sha256":"PAYLOAD_SHA256","size":123456}
```

Use `kind: apk` for a wrapper signed with its existing Android signing key.
The offline signing key authorizes code execution as root; sign only audited,
accepted builds. Android additionally enforces the installed APK signing identity.
No downgrade policy is imposed: an operator may sign a deliberate rollback.

The version above is illustrative: use the exact version of the verified
payload. For an agent update, the payload is the extracted ARMv7 `agent` binary,
not the `.tar.gz` release archive. For an APK update, it is the actual APK, not
its containing directory. Record the byte size and lowercase SHA-256 of these
exact bytes. Agent and outer bundle payloads are limited to 64 MiB, standalone
APKs to 60 MiB. The `version` label is signed
metadata; it is not by itself proof of the payload's installed version.

From `native/android-agent`,
`go run ./cmd/sign-update sign /PRIVATE/update.key manifest.json signed.json`
prints the envelope SHA-256. The signer signs the supplied JSON; the operator
must check its fields against the verified payload before signing. The output
file must not already exist. Do not edit, format or append a newline to the
envelope after recording its digest.

Publish the payload and signed envelope at immutable HTTPS URLs that return
their bytes directly with status 200. Redirects are refused, including ordinary
GitHub release asset download redirects. Mirror verified release bytes to the
direct HTTPS host rather than substituting an archive, HTML download page or
redirect URL. After publishing, download both URLs without following redirects
and compare their sizes and hashes with the local files. Then:

```
ott a1 android status
ott --receipt a1 android update https://HOST/immutable-release/signed.json ENVELOPE_SHA256
ott a1 android status
```

The agent verifies the pinned Ed25519 key, envelope digest, payload size/digest,
kind and package before acknowledging. Agent updates keep a previous binary;
the startup supervisor rolls back if the replacement exits before its first
successful controller poll. This cannot recover a hung kernel or a replacement
that hangs without exiting. APK updates preserve app data using `pm install -r`.
An APK-only update does not replace `/system/bin/ott-touch-guard` or the touch
broker. The 0.1.2 agent verifies that the candidate APK's embedded helper exactly
matches the installed system helper, and requires an enabled, foreground OTT
app both before admission and before installation. A changed helper requires a
coordinated bundle. APK-only installation does not provide the bundle's saved
pair or automatic rollback.

An `accepted` response means the validated update was admitted; it does not
mean installation or restart succeeded. Save the request ID from `--receipt`,
then use `ott a1 android operation REQUEST_ID` when that agent supports durable
operation lookup. An agent replacing itself can leave the old operation
`unknown` even after successful installation. Do not resend on that basis.
Compare fresh native status with the expected agent version, runtime and
installed artifact evidence. If the player was in use, verify that it reconnects
with its kiosk policy and queue intact, then collect playback progress; physical
video and audible sound still require observation on the device. If the APK was
intentionally disabled or another HOME app was selected, preserve that state.
An agent-only update must not enable the wrapper, select a different HOME app or
start playback just to satisfy an acceptance check.

For an intentional rollback, sign the previously verified compatible payload
with the same trusted key and deliver it as a new explicit update. Agent
rollback does not roll back the APK, hosted player or controller. APK install
can be rejected by Android's package, signing or version rules. A previous
binary on disk is not a general recovery guarantee: loss of network, storage
failure, a hung replacement or a failed startup supervisor may require USB.

### Installed component evidence

The 0.1.2 agent adds `updates` to `android status` with schema `version: 1`:

- `agent`: `state`, the running agent's `version`, and the installed binary's
  `sha256` when readable.
- `apk`: `state`, `package: play.ott.kitkat`, and, when available, `version_name`,
  `version_code`, `sha256` and `app_state` (`enabled`, `disabled` or `unavailable`).
- `touch_guard`: `state` and the installed system helper's `sha256` when readable.

Each component uses `state: ready` or `state: unavailable`; unavailable evidence
is not evidence that the component is absent or unchanged. A bounded `reason`
explains missing file/package evidence or a package that changed during the
read. Older agents may omit `updates` entirely.

`updates.bundle_state` is `absent`, `pending`, `committed`, `rolled_back` or
`unavailable`. It describes the durable pair transaction, not current playback.
`updates.recovery_failed: true` means automatic pair recovery could not finish:
the native agent keeps health, logs and capabilities available, permits a
signed agent-only repair update, and blocks app mutations and the playback
watchdog. APK and bundle updates are refused in that state. Preserve the
transaction evidence and repair the agent or use local recovery; do not clear
the flag or delete the saved pair to force an installation.

Hashes are read afresh, with limits of 64 MiB for the agent and APK and 4 MiB
for the helper. Collection has a four-second deadline and validates package
metadata before and after hashing. Status never returns the binary bytes,
private paths or raw PackageManager output. Compare these hashes to the reviewed
payloads; a version label or `accepted` receipt alone is insufficient. This
proves installed bytes at observation time, not that the screen or audio works.

### Coordinated wrapper and helper updates

The 0.1.2 agent adds `kind: bundle` to the same seven-field signed manifest.
First update an older agent using `kind: agent` and verify its new version and
hash. Older agents reject a bundle; updating the controller or CLI alone does
not add support on the tablet.

Set `WRAPPER_VERSION` to the version of the verified signed wrapper APK, then
prepare a new private delivery directory from the repository root:

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind bundle --apk /private/release/ottplay-kitkat.apk \
  --version "$WRAPPER_VERSION" \
  --url "https://updates.example/kitkat-$WRAPPER_VERSION/bundle.zip" \
  --manifest-url "https://updates.example/kitkat-$WRAPPER_VERSION/signed.json" \
  --key /private/update.key --output /private/ota-wrapper
```

The tool extracts the APK's embedded helper and constructs a deterministic
`bundle.zip` with the required file names. Publish only `bundle.zip` and
`signed.json` to those URLs, then run
`python3 native/android-agent/prepare_update.py verify --directory /private/ota-wrapper`.
After successful host verification, use the returned envelope hash in the
ordinary `ott --receipt a1 android update HTTPS_SIGNED_JSON ENVELOPE_SHA256`
command. `--payload bundle.zip` also accepts a previously built bundle;
`--apk` and `--payload` are mutually exclusive.

The bundle is a ZIP containing exactly two regular files at its root:
`app.apk` and `ott-touch-guard`. No extra paths, symlinks, encrypted entries or
duplicate names are accepted. The APK must declare `play.ott.kitkat` and its
`assets/ott-touch-guard` bytes must exactly match the second file. The helper
must be a 32-bit little-endian ARM executable. Limits are 60 MiB for the APK,
4 MiB for the helper and 64 MiB for the complete ZIP. Sign its exact ZIP bytes
with `prepare_update.py prepare --kind bundle`, then publish and verify both HTTPS
files before sending the same `android update` command.

This operation replaces only the APK and the fixed
`/system/bin/ott-touch-guard` file. It does not replace the root broker, startup
supervisor, device configuration, Android firmware or selected HOME app. Those
components still require their own reviewed maintenance procedure.

Bundle preflight requires the existing APK/helper to be a matching pair,
the OTT app enabled and in the foreground, a responsive trusted player page,
a read-only `/system` partition, and PackageManager help advertising downgrade
support. There must be free space for saved/candidate components and installation.
A locked kiosk must report the system touch guard active and an identifiable
TV channel or ordered VPortal queue. Other locked queues, including a Plex queue
without a supported identity snapshot, are refused. The checks
run again before applying effects. An intentionally disabled player or another
foreground HOME app causes refusal; the updater does not enable or select OTT.
Use an agent-only update while such a device remains in that state.

The updater privately saves the previous pair and a durable pending transaction,
stops the app, waits for its processes and old touch helper to stop, atomically
replaces the helper, returns `/system` to read-only,
installs the APK with `pm install -r`, and restarts the app. Before committing,
it compares the installed APK/helper bytes, kiosk lock/strict flags, provider,
queue length, and the same TV channel ID or ordered VPortal item IDs when locked.
If playback was active, it also requires advancing video time. These checks do
not prove physical picture, sound or preservation of a deliberately paused
state; inspect those separately when they matter.

On failure it attempts to restore the saved APK/helper pair, using
`pm install -r -d` for the prior APK. On agent startup, a pending transaction
triggers file recovery without launching the app or changing HOME selection.
If rollback cannot finish, the transaction remains pending for inspection.
App data is preserved by replacement and is not rolled back: APK releases
must keep data migrations compatible with the previous version. Firmware may
still reject an APK downgrade even when its help advertises support. A broken
filesystem, lost power or an unstartable native agent can require USB recovery.

The transaction and failure paths have host-side tests. A successful test run
is not physical acceptance of the APK/helper replacement on a particular
tablet; record installed hashes, runtime state and device behavior after a
supervised first rollout before relying on unattended updates of that pair.

## Release and rollout

The public source and release instructions live in
[the release runbook](../../docs/release-workflow.md). Keep these deliveries
separate and verify each before moving to the next:

1. Review and merge the source changes through a PR with a successful CI gate.
   Keep the committed base version unreleased: once `v0.1.1` is stable, new
   controller/agent candidates use base `0.1.2`. From a clean checkout at the
   default-branch HEAD, run `python3 scripts/release.py beta --version 0.1.2`
   only if the automatic push beta has not already published that source.
   `python3 scripts/release.py status` identifies the release run. Do not build
   a published update from a dirty checkout or invent a candidate number.
2. Require the completed Release gate and verify `release-manifest.json`, its
   source SHA, immutable evidence and every selected asset digest. The Android
   payload comes from `ottplay-control-server-android-agent.tar.gz`; its
   `build.json` records source, version and binary SHA-256. The normal release
   matrix builds this companion alongside the server's Linux ARMv7 target.
3. Update the CLI from all of the `cli/` modules at that exact reviewed source
   revision, retaining the old checkout and private configuration. See
   [CLI selection and rollback](../../docs/cli.md#update-select-or-roll-back-the-cli).
   The server's release archive does not contain the CLI.
4. Deploy the controller separately, preserving credentials, origins and the
   single-replica configuration. Its verified GHCR publication runs after the
   release pipeline and copies the release OCI bytes without rebuilding. Use
   the exact version and digest with the existing process manager or
   [deployment procedure](../../docs/deployment.md); retain the previous image
   for rollback. Check readiness and the native connection; also check the
   WebView connection when that player is intentionally running.
5. Sign and publish the verified native payload to direct HTTPS hosting, then
   deliver it with `android update` and verify the result as above. Changes to
   the system touch helper need the coordinated bundle procedure above; an
   APK-only update cannot install them.
6. Publish the verified `ottplay-foss` web release to the exact hosted site and
   origin the wrapper opens, preserving required hosting/proxy configuration.
   The release's `ottplay-foss-modea.tar.gz` contains the public web root.
   Verify the served `build-info.json` and assets before
   `ott a1 android reload` for a player that is intentionally running. Check
   the new web version and kiosk/queue state. Leave an intentionally disabled
   wrapper disabled; publication alone does not require launching it.
   Updating here.now does not update the APK or native agent, and updating an
   embedded Capacitor/Tauri app requires its own package delivery.

Required controller checks include `go vet ./...`, `go test -race ./...`,
the CLI Python suite, native-agent Go/JavaScript checks below, release-tooling
contracts and deployment checks. Player changes require the player repository's
CI gate, production build and relevant browser/playback tests. An ARMv7 build
and a signed envelope do not substitute for the installed-device checks.

## Checks

```
go vet ./...
go test -race ./...
npm ci --ignore-scripts --no-audit --no-fund
node --test player.test.cjs
```

The local CDP transport validates the configured origin and top frame, uses only
fixed embedded operations and closes the WebSocket gracefully for old Chromium.
The HTTP transport reads KitKat's current `net.dns1`–`net.dns4` properties for each
new connection because the firmware has no `/etc/resolv.conf`. It preserves TLS
hostname and certificate verification and does not change system DNS settings.
The player adapter uses strict ES5 syntax, including Chromium 30's rejection of
function declarations inside statement blocks. Source URLs, tokens, and profile contents are
excluded from health/log responses; screenshots may contain visible private data.

## Correlation and evidence

The [workbench quick start](../../docs/workbench.md#quick-start) collects this
agent's observations with `inspect --lane native`, `bundle`, `test run health`
and `test run media-progress --lane native`. It requires the
[reviewed CLI source](../../docs/cli.md#choose-the-cli-source-revision) plus this
separate native binding; updating the CLI/controller does not install or update
the agent. Two compatible native health samples can establish decoder progress,
not physical presentation or audible output. Saved bundles can be
[verified offline](../../docs/workbench.md#verify-saved-evidence-offline) without
agent access. Use [operation lookup](../../docs/workbench.md#request-receipts-and-operation-lookup)
to read a prior request's receipt without replaying it.

Health optionally includes `player.identity`: the web runtime, playback
`generation`, main backend `handle_id`, and media kind from the installed
player's existing read-only inspection hook. `available: true` requires a
consistent snapshot with all three identity fields. Older web builds and absent
handles remain unavailable; no URL hash is substituted for shared identity.
A native process runtime and the kernel's `/proc/sys/kernel/random/boot_id` are
reported separately. Event rows carry their native runtime and OS boot marker;
the workbench compares log runtime with the preceding health response.

`player.decoder` reports supported HTML video decoded-frame, dropped-frame and
audio-byte counters, volume and mute. Missing counters remain null. The agent
also reads bounded, fixed `dumpsys` services without changing their state:

- `system_evidence.surface`: completed presentation fences only for the exact
  package Activity layer. This is **app-surface** evidence; a hardware video
  overlay may be separate. It never proves the video or physical screen.
- `system_evidence.audio`: active AudioFlinger tracks whose client PID matches
  this app, with session IDs, server-frame and underrun counters. Media played
  through a different process, unrecognized vendor formats, and missing tracks
  remain unavailable. Tracks and byte counters never prove audible output.

Parsers follow AOSP Android 4.4.2 [FrameTracker](https://android.googlesource.com/platform/frameworks/native/+/android-4.4.2_r1/services/surfaceflinger/FrameTracker.cpp)
and [AudioFlinger track dumps](https://android.googlesource.com/platform/frameworks/av/+/android-4.4.2_r1/services/audioflinger/Tracks.cpp).
Each service read has a 1.2-second deadline and 256-KiB output limit; the whole
system collection is capped at 1.8 seconds and reserves response time. No raw dump,
other app name, URL, or arbitrary diagnostic text is returned. A process change
during collection invalidates system evidence. These observations do not change
watchdog decisions: advancing presentation counters alone cannot identify video.
The target firmware still requires live acceptance of these optional adapters.

## Durable operation history

Alongside the short-lived duplicate-delivery journal, `operations.json` retains
up to 64 metadata-only mutation receipts for 24 hours. It is atomically written
and synced before invoking effects. Receipt lookup only reads health; it never
requeues an operation. The existing separate native binding and V1 request
contract are unchanged, so no controller rollout is needed.

`accepted` means an effect was prepared. `handler_completed` means its handler
returned successfully, **not** that playback recovered or a reboot completed.
A damaged diagnostic history is moved aside to one private `.invalid` file;
maintenance stays available, the separate execution journal is preserved, and
`operation_history_reset` reports the loss.
An unfinished claim after an agent restart is `unknown`, with its original
executor runtime retained. A reboot or agent exec may therefore be unknown even
if it succeeded; use the independent OS boot marker, agent version, web runtime
and playback evidence to establish the outcome. A page reload does not remove
native receipts. Old agents without this history return `history_available:
false` and an unknown result. The existing web-only operation history remains
independent and may still be lost on web reload.
