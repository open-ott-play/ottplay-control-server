# Android OTA: ordinary players and the separate rooted agent

For **k8a/fire8 and other Capacitor players**, use the normal player's Wi-Fi
updater, not the rooted-agent command:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Omitting `--version` selects the latest published beta. Use `full` instead of
`api22` on Android 7+. The CLI resolves the official release, checks its manifest,
prepares the APK and polls its state before requesting installation once.
Android may require confirmation on the tablet. Disable kiosk and unlock protected
settings explicitly before maintenance; the command never changes those policies.
See [the complete CLI procedure](cli.md#capacitor-apk-updates-over-wi-fi).

This needs a previously installed `AppUpdate`-capable APK, Wi-Fi and a running
player connected to the controller. It does not need USB for ordinary subsequent
updates, but cannot reach an offline tablet or a stopped WebView. A first install,
unavailable updater, broken app or lost network can still require local recovery.
No automatic release scheduler or general silent installer is implied.

The remainder of this guide describes a **different, rooted a1 maintenance agent**,
restricted to the provisioned Matricom/API19 `play.ott.kitkat` installation. It is
not already installed or usable on ordinary Fire/Capacitor devices.

After the one-time root USB bootstrap, the independent Android agent can receive
signed updates over its outbound HTTPS connection. It does not need a working
WebView to update itself. The 0.1.2 agent also supports a coordinated APK and
system touch-helper bundle, with saved recovery files and verification before
commit.

This guide explains the complete path. For exact manifest fields, limits and
hardware requirements, use the [native agent reference](../native/android-agent/README.md#signed-updates-over-wi-fi).

## What gets updated

![OTA architecture: reviewed release bytes are signed, published over direct HTTPS, verified by the independent agent and checked after installation. Web deployment is separate.](images/ota-architecture.svg)

- **Native agent:** `kind: agent` replaces its own ARMv7 binary. This path also
  works when OTT is disabled and another HOME application is in use.
- **APK only:** `kind: apk` replaces the wrapper while retaining its data. OTT
  must be enabled and in the foreground. The candidate's embedded helper must
  match the installed system helper exactly.
- **APK and touch helper:** `kind: bundle` updates both components as a pair.
  An older agent must first receive an agent-only update that supports bundles.
- **Hosted player:** deploy the verified web artifact at the existing origin,
  then reload and inspect an active player. An APK update does not deploy the
  hosted JavaScript. See the [release workflow](release-workflow.md).
- **Controller and CLI:** deploy the controller and install the complete CLI module set
  separately. Updating either does not replace code on the tablet.

Firmware, the kernel, system WebView, root broker and startup scripts are outside
this OTA format. The [native reference](../native/android-agent/README.md#bootstrap)
describes bootstrap and recovery boundaries. No network ADB or arbitrary remote
shell is required for supported OTA operations.

## From reviewed release to confirmed installation

1. **Select verified bytes.** Merge reviewed changes after CI passes. Download
   the intended release artifact and verify its source revision, immutable
   release evidence and checksum. Extract the `agent` binary from the native
   archive; the archive itself is not an installable agent payload.
2. **Prepare and sign.** Use the existing private Ed25519 key whose public key
   was pinned during bootstrap. Keep that key and device credentials private.
3. **Publish and check transport.** Publish only the payload and `signed.json`
   at immutable HTTPS paths. Both must return their bytes directly with HTTP
   200; the updater refuses redirects. Run the packaging tool's `verify` command.
4. **Send one update.** Record the request ID and inspect status after the
   acknowledgement. A lost or uncertain response is not a reason to repeat an
   operation blindly.
5. **Confirm the effect.** Compare fresh installed hashes and versions with the
   prepared release. For an active player, also check the kiosk target, queue
   and advancing playback. Physical picture and sound require observation.

Example for an agent release; substitute the selected version and real paths:

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256` is the packaging tool's `envelope_sha256`, not the payload
hash. `accepted` means admission. It does not mean installation succeeded.
Self-replacement can leave an old operation receipt `unknown`; the new runtime,
expected agent version and matching installed hash provide the installation
evidence. The [CLI reference](cli.md#independent-android-agent)
describes independent native routing and receipt handling.

## How the APK/helper transaction recovers

![APK and helper transaction: preflight, save the previous pair, apply, verify, commit; failures restore the previous pair and incomplete recovery keeps app mutations blocked.](images/ota-transaction.svg)

The bundle contains exactly `app.apk` and `ott-touch-guard`. The packaging tool
can create it directly from the signed wrapper APK using `--kind bundle --apk`.
It extracts the embedded helper, so the two delivered copies match. See the
[bundle command and requirements](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Before changing files, the agent requires a matching installed pair, enough free
space, a responsive foreground OTT player, a read-only system partition and
advertised APK downgrade support. For a locked kiosk it verifies the same TV
channel ID or ordered VPortal IDs; equal queue lengths alone are insufficient.
Unsupported locked queues are refused. An intentionally disabled OTT app is
also refused, preserving the user's current HOME application.

Recovery files and the pending transaction are persisted before stopping the
app. The agent waits for the app and old helper to stop, replaces the helper
atomically, restores `/system` to read-only, installs the APK, and checks the
result. A failed apply or verification triggers restoration of the previous
pair. Startup recovery restores files without launching OTT or taking over HOME.

If recovery cannot finish, `updates.recovery_failed` is true. Diagnostics and
signed agent-only repair remain available; app mutations and the playback
watchdog are blocked. The saved transaction must not be deleted to force an
installation. Binary recovery does not undo app-data migrations, and firmware
can still reject a downgrade. See the [recovery limitations](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Results and acceptance record

![Validation chart: 488 CLI tests, 576 release-tooling tests and 6 OTA-packager tests; the reference tablet runs the verified new agent, while APK/helper physical acceptance remains pending.](images/ota-validation.svg)

The implementation audit on 14 October 2026 passed 488 CLI tests (one skipped),
576 release-tooling tests, six OTA-packager tests, native Go race/static checks
and an ARMv7 build. These counts describe suite size, not coverage or performance.
Failure-injection tests exercise invalid archives, interrupted transactions,
partial restoration, target changes and recovery fencing. Independent review
also caught and corrected an insufficient queue-identity check and mismatched
host/device APK-helper validation.

As checked on 15 October 2026:

- The OTA implementation is merged in [controller PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104)
  and included in [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- The current and previous signed agent packages are published at the
  [OTA delivery host](https://android-updates.ottplay.here.now/). Their payload and
  envelope bytes were downloaded and compared successfully over direct HTTPS.
- The reference tablet updated from `0.1.1-beta.21` to `0.1.2-beta.4` through
  the independent OTA channel. The running agent's version and installed
  SHA-256 matched the selected release; its runtime changed and the OS boot ID
  stayed the same. OTT still had no running app process, and no app launch or
  HOME-selection operation was sent.
- Native status reported the system touch-helper hash, no pending bundle and
  no recovery failure. APK metadata was unavailable, so this observation does
  not establish the installed APK's version or hash.
- The coordinated APK/helper transaction has host-side validation; physical
  installation, downgrade recovery, picture and audio are not yet accepted on
  the reference tablet.

For each subsequent rollout, retain the release source/checksums, prepared
envelope hash, request ID, before/after native status and the separate physical
acceptance result. A successful download or command receipt cannot substitute
for those observations.
