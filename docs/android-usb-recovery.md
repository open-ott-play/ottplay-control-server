# Legacy Android recovery over USB

`tools/android_usb_supervisor.py` provides an explicit ADB backend for a legacy
Android tablet connected to a Linux host such as Synology. It is separate from
ARTEMIS and from `ott NAME android ...`: it does not require an ARTEMIS helper,
an Android maintenance-agent installation, or a responsive OTT WebView.

Its purpose is narrow: after a new tablet boot, dismiss a **non-secure** swipe
lockscreen and verify that OTT is in the foreground. It never enters a PIN,
password or pattern. Unknown or protected keyguard state stops recovery and
requires local attention.

## Read-only keyguard probe on older firmware

The k8a Fire firmware does not expose an explicit secure-lock flag in its
`dumpsys` output. Install the small probe from `native/android-usb-probe` before
using recovery on that device. It supports API 16 and later (target API 22) and
only reads `KeyguardManager.isKeyguardSecure()` and `isKeyguardLocked()`.
The exported receiver requires `android.permission.DUMP`, which permits the
authorized ADB shell to query it and prevents ordinary apps from invoking it.
The probe does not unlock, enter text, change settings or bypass authentication.

Build using the Android SDK/build-tools paths in `build.sh` and private signing
material. `SIGNING_KEY` names a PKCS12 keystore and `SIGNING_PASSWORD_FILE` names
a private password file; `SIGNING_ALIAS` defaults to `ott-home`. Set `ANDROID_HOME`
and `JAVA_HOME` if the defaults do not match the build machine.

```sh
SIGNING_KEY=/private/path/signing.p12 \
SIGNING_PASSWORD_FILE=/private/path/signing-password.txt \
  bash native/android-usb-probe/build.sh
```

The verified output is `native/android-usb-probe/build/ott-usb-probe.apk`.
Install it using the exact authorized device's isolated ADB transport:

```sh
adb -s EXACT_USB_SERIAL install -r native/android-usb-probe/build/ott-usb-probe.apk
```

Run this inside the provisioned transport where that serial was verified, not
against an arbitrary host's default ADB server. Keep the same signing identity
for updates. A missing/invalid probe cannot authorize a swipe: recovery requires
explicit non-secure evidence; on k8a the probe is required because the fallback
diagnostics lack it. A secure PIN/password/pattern always requires local input.

## Provision one exact device

Grant USB debugging on the tablet once and preserve the authorized ADB keys in a
private host directory containing `adbkey`. Do not generate replacement keys on
every container start: doing so requires another on-screen authorization.
Keep the key directory and configuration private; do not commit keys, Android
identifiers, screenshots or device settings to the repository.

The configuration identifies a single tablet using its USB serial and product,
Android model, API level and Android ID. Obtain these values from the intended
device during attended setup. Never select the first entry in `adb devices`.
For the k8a installation the model is `KFGIWI` and API level is `22`; use the
actual private device identifiers in the deployed configuration.

Example configuration, with paths and identity placeholders to replace:

```json
{
  "version": 1,
  "target": {
    "serial": "EXACT_USB_SERIAL",
    "product": "Fire",
    "model": "KFGIWI",
    "sdk": 22,
    "android_id": "0123456789abcdef"
  },
  "docker_path": "/var/packages/ContainerManager/target/usr/bin/docker",
  "image": "YOUR_VERIFIED_ADB_IMAGE",
  "adb_key_dir": "/volume1/ott-usb/k8a/adb-keys",
  "container_prefix": "ott-k8a-usb",
  "state_dir": "/volume1/ott-usb/k8a/state",
  "package": "play.ott.foss",
  "poll_seconds": 5,
  "max_attempts": 3
}
```

The host needs Python 3, Docker privileges and access to USB sysfs/device nodes.
The image must provide `adb` and `sleep`. The supervisor resolves the current USB
node again after re-enumeration. Its container receives only that node and the
persistent ADB-key directory, uses `--network none`, drops capabilities and sets
`no-new-privileges`. It neither exposes port 5037 nor enables network ADB.
Only a container with its exact ownership label may be reused or replaced; an
unrelated container with the same name causes an error.

## Run after host startup

Deployment templates live in `deploy/android-usb-recovery`. The supplied
`ott-k8a-recovery.service` runs the installed script and configuration under
`/usr/local/etc/ott-k8a-recovery/`. Copy those files plus the service template
there with root ownership and private configuration permissions. Keep state
and authorized ADB keys in persistent storage.

On Synology DSM, install `S99ott-k8a-recovery.sh` under
`/usr/local/etc/rc.d/` as an executable root-owned script. Its `start` action
installs the supplied systemd unit, reloads systemd, enables it and starts it.
The unit restarts a failed supervisor after ten seconds. Review host paths and
Container Manager availability before installation; these are deployment
templates, not an automatic installer.

For a foreground run using the supplied installation paths:

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py \
  --config /usr/local/etc/ott-k8a-recovery/config.json run
```

Keep one supervisor process per configuration. Configure host-side restart on
unexpected process exit. Avoid creating a second instance from every scheduled
poll: `run` already polls. It locks `state_dir` during each recovery/check
cycle and releases that lock between polls.

On first observation it records the existing boot without waking or unlocking
it. After a **new** boot it waits for `sys.boot_completed=1`, checks the exact
device and keyguard, sends a wake event and an upward swipe when needed, starts
OTT and verifies the foreground activity. Only then does it record success.
Failures are bounded by `max_attempts` for that boot. Durable `state.json`
prevents a supervisor restart from treating an already completed boot as new.
Do not delete this state as a routine recovery step.

Ordinary screen-off and screen-on events do not trigger automatic recovery.
This preserves the user's sleep choice and OTT's suspension of network video
while asleep. This service does not keep the display awake.

## Manual operation

Manual commands use the same exclusive configuration lock as each supervisor
cycle. A command racing an active cycle waits up to 30 seconds for the lock. If it
still reports the device busy, no device action was sent; inspect state before
retrying input. For an uninterrupted maintenance session, stop the
supervisor and resume it afterwards. The paths below are examples matching the
installation above.

Inspect connection, boot completion, keyguard and durable recovery state:

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json status
```

Wake intentionally without toggling an unknown power state:

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json wake
```

Dismiss a verified non-secure lockscreen and bring OTT to the foreground now,
including on the already observed boot:

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json recover
```

For a manual gesture, inspect a fresh capture and the current display dimensions
first. These coordinates are only an example for a 1280 × 800 display, not a
universal unlock gesture. Coordinates must fit the current device dimensions.

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json swipe 640 640 640 160 --duration 500
```

Save a private display capture using a bare PNG filename. The script saves it
under `<state_dir>/captures/` with mode `0600` and validates its PNG signature
and size. Names must match `[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.png`; paths, extra
dots and implicit extensions are not accepted. Symlinks and existing filenames
are refused before capture.

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json screenshot display.png
```

A deliberate reboot is also available:

```sh
python3 /usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py --config /usr/local/etc/ott-k8a-recovery/config.json reboot
```

If you stopped the supervisor for maintenance, restart it after sending reboot
so that it observes the new boot.
A successful `recover` verifies the foreground package; it does not prove that a
channel decoded video. Check OTT status and advancing decoded frames separately.

## Remote commands from the operator machine

`tools/android_usb_remote.py` forwards the same restricted commands through an
existing SSH host alias. For the configured Synology alias `s`:

```sh
python3 tools/android_usb_remote.py --host s status
python3 tools/android_usb_remote.py --host s wake
python3 tools/android_usb_remote.py --host s recover
python3 tools/android_usb_remote.py --host s swipe 400 1024 400 256
python3 tools/android_usb_remote.py --host s screenshot check.png
```

The swipe example assumes an 800 × 1280 current display. Inspect first and use
coordinates for the actual orientation. The screenshot is saved on Synology,
under the configured `<state_dir>/captures/`, not on the operator machine.
Supply a new bare PNG filename. When the legacy redirect is installed, the
shared manager's state directory is used. The wrapper uses
non-interactive SSH and `sudo -n`; the host must already authorize the operator.
It does not provide a general remote-shell command or transfer screenshots.

`--remote-script` and `--remote-config` can override the installed paths; their
defaults match the supplied service. Reboot is explicit:

```sh
python3 tools/android_usb_remote.py --host s reboot
```

Read service output on Synology with
`journalctl -u ott-k8a-recovery.service`; keep diagnostic captures private.

## Limits and troubleshooting

- The host must be running, the USB cable connected, and ADB authorized. This is
  not an OTA or Wi-Fi recovery channel and cannot switch on a powered-off tablet.
- A PIN, password, pattern, authorization prompt or ambiguous keyguard evidence
  requires attended recovery. Do not replace the checks with unconditional
  swipes or enter credentials into a retry loop.
- An absent or ambiguous serial/product match causes no device input. Check the
  cable and the intended tablet; do not broaden selection to all USB devices.
- USB device numbers change on reboot. Let the supervisor reconcile its owned
  container instead of hard-coding yesterday's `/dev/bus/usb` node.
- The first boot observed after initial installation is only armed. Use explicit
  `recover` for an already locked tablet, then start the supervisor for later boots.
- Reaching the per-boot attempt limit requires investigation and explicit manual
  recovery; the service does not retry indefinitely.
- Keep the original system launcher and an attended recovery path. Foreground
  restoration is not Android device-owner or lock-task confinement.

Acceptance should include a real tablet reboot with USB re-enumeration, OTT
foreground verification, and a separate ordinary sleep test confirming that the
supervisor leaves the screen asleep. Record the observed result and device/API
level; do not infer compatibility with every Fire OS version from one tablet.

## Deployment validation: k8a, 2026-10-19

Installed the read-only probe and persistent Synology service. A real tablet
reboot changed the boot identity and USB node; the supervisor recreated its
owned transport, dismissed the non-secure swipe lock and verified OTT in the
foreground. Cold boot initially exceeded the 30-second command timeout; the
bounded default is now 60 seconds, configurable from 1 to 120 seconds. Probe
broadcasts explicitly target the current Android user.

A subsequent explicit screen-off test reported `mWakefulness=Asleep` and
`Display Power: state=OFF`; the running supervisor reported
`same_boot_no_action`. Ordinary sleep therefore did not trigger recovery in
this test. The Synology host itself was not rebooted during validation.

The service requires the tablet to remain connected to the provisioned USB
host. This deployment verifies foreground recovery, not video decoding or an
offline media library.
