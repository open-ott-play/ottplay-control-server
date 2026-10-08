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
120 seconds without playback progress, first cycles the video surface and then
restarts the app, with increasing cooldown and at most three attempts per episode.
It never reboots the device automatically. An explicit native pause/standby
suspends this watchdog. Continuous clock progress with a frozen hardware surface
cannot be detected reliably; `android recover` also handles that case manually.

Lifecycle/update receipts say `accepted`, with `completion: inspect_status`.
They confirm admission, not successful reboot/update. Inspect status afterward.
A private bounded journal prevents re-execution after duplicate delivery or crash;
uncertain operations must not be blindly retried. Screenshots are not retained on
disk and a lost screenshot reply requires a new explicit capture request.
Logs are a bounded ring of agent event codes, not unrestricted Android logcat.

## Signed updates

Create a UTF-8 manifest with exactly these fields:

```
{"schema":1,"kind":"agent","version":"0.1.1","package":"play.ott.kitkat","url":"https://HOST/agent","sha256":"PAYLOAD_SHA256","size":123456}
```

Use `kind: apk` for a wrapper signed with its existing Android signing key.
The offline signing key authorizes code execution as root; sign only audited,
accepted builds. Android additionally enforces the installed APK signing identity.
No downgrade policy is imposed: an operator may sign a deliberate rollback.

`go run ./cmd/sign-update sign /PRIVATE/update.key manifest.json signed.json`
prints the envelope SHA-256. Publish the payload and signed envelope at HTTPS
URLs that respond directly (redirects are refused), then:

```
ott a1 android update https://HOST/signed.json ENVELOPE_SHA256
```

The agent verifies the pinned Ed25519 key, envelope digest, payload size/digest,
kind and package before acknowledging. Agent updates keep a previous binary;
the startup supervisor rolls back if the replacement exits before its first
successful controller poll. This cannot recover a hung kernel or a replacement
that hangs without exiting. APK updates preserve app data using `pm install -r`.

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
