# Android USB maintenance with ADB

Use ADB for device identification, app installation and Android-level diagnosis.
For playback, profiles, channels and kiosk policy, prefer the authenticated
[`ott` CLI](cli.md): it understands the player's state and returns structured
results. A successful ADB command only means Android accepted the command; it
does not prove that a channel started, a profile loaded or a picture appeared.

This guide uses placeholders. `SERIAL` is the USB serial from `adb devices -l`,
not the controller alias `a1` or `a2`. `PACKAGE` is the installed Android package
ID, `HOST` is the SSH host with the USB cable, and `CONTAINER` is its dedicated
ADB container. Replace these values before running commands. Run one step at a
time and inspect its result before a dependent action.

## Connect and identify the correct tablet

On the tablet, enable Developer options and **USB debugging**, connect a data
cable, unlock the screen and accept the computer's RSA authorization prompt.
Select **Always allow from this computer** only for a trusted maintenance host.
Enabling **OEM unlocking** does not itself unlock the bootloader, provide root,
authorize ADB or make a firmware image compatible with the tablet.

Install current Android SDK Platform Tools on the USB host. Start with:

```sh
adb version
adb devices -l
```

- `device`: the transport is authorized; Android may still be booting or locked.
- `unauthorized`: unlock the tablet and accept the RSA dialog. Reconnecting the
  cable can reveal a hidden prompt. Do not copy another host's private ADB key.
- `offline`: check cable, power and USB mode, then reconnect. A server restart
  interrupts every client using that server; reserve it for a confirmed transport
  problem rather than a slow app.
- No entry: check that the cable supports data, USB debugging is enabled and the
  host/container can access the actual USB device. Seeing a USB storage device
  alone does not establish an ADB connection.

Keep the selected serial explicit on **every device command**, even when there
is currently only one tablet attached:

```sh
adb -s SERIAL get-state
adb -s SERIAL shell getprop ro.product.manufacturer
adb -s SERIAL shell getprop ro.product.model
adb -s SERIAL shell getprop ro.product.device
adb -s SERIAL shell getprop ro.board.platform
adb -s SERIAL shell getprop ro.build.version.release
adb -s SERIAL shell getprop ro.build.version.sdk
adb -s SERIAL shell getprop ro.product.cpu.abi
adb -s SERIAL shell getprop sys.boot_completed
```

`sys.boot_completed` should become `1` before app-level checks. Recheck the serial
after reconnecting or rebooting; recovery and bootloader modes may expose a
different transport. Do not publish serials or complete property dumps without
reviewing them for identifiers.

## A tablet connected to Synology

ADB must run where the USB cable terminates. Use SSH to the Synology and a
dedicated, reviewed ADB container if the NAS does not have compatible Platform
Tools. First inspect an existing container; do not start a competing ADB server
against the same USB device. Match the host USB bus/device entry to the intended
tablet before granting it to a container.

```sh
ssh USER@HOST 'sudo docker ps --format "{{.Names}}\t{{.Image}}"'
ssh USER@HOST 'sudo docker exec CONTAINER adb version'
ssh USER@HOST 'sudo docker exec CONTAINER adb devices -l'
ssh USER@HOST 'sudo docker exec CONTAINER adb -s SERIAL shell getprop ro.product.model'
```

An operator creating a temporary bridge can use this shape on the NAS itself:

```sh
sudo docker run -d --name ott-adb-session --network none \
  --cap-drop ALL --security-opt no-new-privileges \
  --device /dev/bus/usb/BUS/DEVICE:/dev/bus/usb/BUS/DEVICE \
  --mount type=bind,src=/PRIVATE/adb-keys,dst=/root/.android \
  --mount type=bind,src=/PRIVATE/adb-transfer,dst=/transfer \
  TRUSTED_ADB_IMAGE@sha256:VERIFIED_DIGEST \
  sh -c 'adb start-server && exec sleep 86400'
```

This is a template, not an image recommendation. The selected image must contain
`adb`, `sh` and `sleep`; adapt its user/home paths if it does not run as root.
Create the private host directories first, restrict access to the key directory,
and verify the image digest independently. Mount only the selected USB node and
private transfer directory. USB re-enumeration can change `BUS/DEVICE`; recreate
the temporary container for the verified new node when needed. Do not broaden
it to `--privileged`, expose the ADB server port or mount all NAS storage to avoid
diagnosing a missing device. Some NAS USB permission policies require a reviewed
host-specific setup instead of this template.

The key directory preserves the authorized ADB host identity between container
sessions. Its private keys must not enter Git, screenshots, logs or public file
hosting. End a temporary session with `sudo docker stop ott-adb-session` and
`sudo docker rm ott-adb-session` after confirming no other task owns it.

## Inspect apps and launch OTT

Package IDs differ between the standard Android app and the legacy KitKat
wrapper. Discover the installed ID before acting; do not infer it from an alias:

```sh
adb -s SERIAL shell pm list packages
adb -s SERIAL shell pm path PACKAGE
adb -s SERIAL shell dumpsys package PACKAGE
adb -s SERIAL shell dumpsys activity activities
```

Filter these outputs locally when sharing evidence; package dumps and activity
intents may contain URLs or other private values. To launch the selected app's
launcher activity without assuming its component name:

```sh
adb -s SERIAL shell monkey -p PACKAGE -c android.intent.category.LAUNCHER 1
```

Use the screenshot and foreground-activity checks below to confirm the result.
For an app restart only when needed, stop the exact package, then launch it:

```sh
adb -s SERIAL shell am force-stop PACKAGE
adb -s SERIAL shell monkey -p PACKAGE -c android.intent.category.LAUNCHER 1
```

Stopping OTT interrupts playback. Neither launch command bypasses a secure
lockscreen. Open and unlock the tablet normally when authentication is required.

## Wake, navigate and enter text

Inspect the screen before sending input. Coordinates refer to the tablet's
current orientation and pixel dimensions, not a scaled desktop preview:

```sh
adb -s SERIAL shell wm size
adb -s SERIAL shell dumpsys power
adb -s SERIAL shell input keyevent 224
```

`224` is WAKEUP and `223` is SLEEP on firmware that implements those key codes.
The power key (`26`) **toggles** state; do not blindly send it to an unknown
screen. Wake does not unlock a PIN/password or power on a shut-down tablet.

Common navigation and key inputs:

```sh
adb -s SERIAL shell input keyevent 3     # HOME: leaves the current app
adb -s SERIAL shell input keyevent 4     # BACK: may leave a page or the app
adb -s SERIAL shell input keyevent 82    # MENU: app/firmware dependent
adb -s SERIAL shell input keyevent 19    # D-pad up
adb -s SERIAL shell input keyevent 20    # D-pad down
adb -s SERIAL shell input keyevent 21    # D-pad left
adb -s SERIAL shell input keyevent 22    # D-pad right
adb -s SERIAL shell input keyevent 23    # D-pad center/select
adb -s SERIAL shell input keyevent 61    # TAB
adb -s SERIAL shell input keyevent 66    # ENTER
adb -s SERIAL shell input keyevent 67    # DEL/backspace, once
adb -s SERIAL shell input keyevent 24    # Volume up
adb -s SERIAL shell input keyevent 25    # Volume down
```

Tap and swipe examples use illustrative coordinates. Take a fresh screenshot,
choose the actual target, then issue one gesture and inspect again:

```sh
adb -s SERIAL shell input tap 300 400
adb -s SERIAL shell input swipe 300 900 300 300 500
adb -s SERIAL shell input swipe 300 400 300 400 800
```

The last example holds at one position for 800 ms; whether it is interpreted as
a long press depends on the app. Strict kiosk and the system touch guard can
reject navigation or gestures. Do not use injected input as a test of whether
physical touch is protected, and do not disable kiosk merely to make input work.

Focus a normal text field first. For a simple ASCII value with spaces:

```sh
adb -s SERIAL shell input text 'Tom%sand%sJerry'
```

Android's legacy `input text` converts `%s` to a space and synthesizes keyboard
events; it is not a general clipboard or Unicode transport. Non-Latin characters
can fail, and literal `%s` cannot be treated as ordinary text with this method.
Avoid shell metacharacters, embedded quotes, multiline text and arbitrary values
in a one-liner: the local shell, SSH shell and Android shell each add quoting
boundaries. Local quotes alone do not guarantee remote-shell escaping. For a
field containing a URL, credentials or non-ASCII text, use the app's supported
setup/profile import or a physical keyboard instead of improvising shell escapes.
Do not enter passwords or tokens in command arguments or recorded shell history.

For a named OTT setup such as `Home`, use the named-setup commands in the
[`ott` CLI guide](cli.md#named-setups). Confirm the target alias and setup contents
before applying it; ADB typing does not provision a controller binding.

## Capture the screen without corrupting PNG bytes

With modern host Platform Tools, write the binary stream directly to a file:

```sh
adb -s SERIAL exec-out screencap -p > tablet.png
file tablet.png
```

For a Synology-attached tablet, keep both SSH and Docker free of pseudo-terminals:

```sh
ssh -T USER@HOST 'sudo -n docker exec CONTAINER adb -s SERIAL exec-out screencap -p' > tablet.png
file tablet.png
```

Do not add `ssh -t` or `docker exec -t`, merge stderr with stdout, or decode this
stream as text. A terminal or banner can corrupt the PNG. On a legacy adbd that
does not support `exec-out`, use a temporary device file and pull it:

```sh
adb -s SERIAL shell screencap -p /sdcard/ott-maintenance.png
adb -s SERIAL pull /sdcard/ott-maintenance.png ./tablet.png
file tablet.png
adb -s SERIAL shell rm /sdcard/ott-maintenance.png
```

When ADB runs in Docker, `pull` destinations are inside the container. Pull to
`/transfer/tablet.png`, then copy that specific file from the private NAS transfer
directory over SSH/SCP. Remove temporary captures after use. Screenshots may
contain account details, channel titles or notifications; inspect before sharing.
Secure/protected surfaces can be black. A PNG proves only one captured frame,
not continuous playback or audible sound.

## Optional recording and UI inspection

Check the device tool's help before recording; encoder support varies by vendor:

```sh
adb -s SERIAL shell screenrecord --help
adb -s SERIAL shell screenrecord --time-limit 15 /sdcard/ott-maintenance.mp4
adb -s SERIAL pull /sdcard/ott-maintenance.mp4 ./tablet.mp4
adb -s SERIAL shell rm /sdcard/ott-maintenance.mp4
```

Use a short limit and stop if the device reports an encoder/display error.
Legacy `screenrecord` does not establish audio capture; successful file creation
also does not prove protected video content is visible. Check storage with
`adb -s SERIAL shell df /sdcard` first if space is limited.

`uiautomator dump /sdcard/ott-window.xml` can expose native widget bounds on
compatible firmware, but it often omits WebView content. On the K960/API 22
maintenance session it stalled in a `patchoat` path, so it is not a prerequisite
for control. Prefer screenshots and activity dumps on that device. If trying UI
inspection elsewhere, use an operator-side timeout and interrupt a hung request;
do not leave repeated dump processes running or assume empty XML means no UI.

## Collect bounded diagnostics

Use a short, app-specific snapshot rather than streaming every device log:

```sh
adb -s SERIAL shell ps
adb -s SERIAL logcat -d -v threadtime -t 500 > tablet-logcat.txt
```

Identify the app PID from `ps` first. On older Android, `pidof`, `ps -A` and
`logcat --pid` may be unavailable. For standard `threadtime` lines, filter the
captured snapshot locally after substituting the verified numeric PID:

```sh
awk '$3 == 12345' tablet-logcat.txt > tablet-app-logcat.txt
```

This retains lines from that PID only; a restarted app or subprocess needs its
own PID, and process IDs can be reused. Inspect the actual log format before
using the filter. Review both files for stream URLs, headers, tokens and personal
data before sharing. Delete the unneeded full snapshot. Avoid `logcat -c` during
diagnosis: it destroys useful evidence for other tasks.

Compare host and tablet clocks when TLS certificates or schedules appear wrong:

```sh
date -u
adb -s SERIAL shell date
adb -s SERIAL shell settings get global auto_time
adb -s SERIAL shell settings get global auto_time_zone
```

If wrong, open Android **Date & time** and enable network-provided time/time zone,
then verify the clock again. A broken clock is not a reason to bypass TLS
verification. ADB shell permission to set time is firmware-dependent; root is
not assumed by this guide.

## Install an update while retaining app data

Verify the release artifact, its SHA-256, supported Android/API level, CPU ABI,
package ID and signing certificate before installation. Use the same signing
identity as the installed application:

```sh
adb -s SERIAL install -r /PRIVATE/verified-release.apk
adb -s SERIAL shell dumpsys package PACKAGE
adb -s SERIAL shell pm path PACKAGE
```

Wait for `Success`, then independently verify the installed package/version,
launch it and inspect the screen and the `ott` status. `-r` requests replacement
with data retained. It does not make an incompatible APK installable or guarantee
that an app migration preserves every setting. Back up needed app configuration
through its supported mechanism first. On older devices, current host ADB may
need `install --no-streaming -r` if the default transfer path is unsupported;
confirm the option in host `adb help` before retrying a failed transfer.

For Docker, stage the verified APK in the private mounted transfer directory and
use `/transfer/verified-release.apk` as the container-side install path. Do not
attempt installation from a path that exists only on the workstation.

If installation reports a signature mismatch or version downgrade, stop and
inspect the selected artifact. Do not solve it with uninstall, `pm clear`, a
different signing key or a factory reset: these can remove configuration and
controller credentials. Never retry an uncertain write until package inspection
shows what is currently installed.

## Reboot and recovery boundaries

For an intentional normal OS reboot:

```sh
adb -s SERIAL reboot
adb devices -l
adb -s SERIAL get-state
adb -s SERIAL shell getprop sys.boot_completed
```

Repeat the read-only checks as the tablet reconnects; use a bounded wait in
automation. `adb wait-for-device` can wait indefinitely and does not mean Android
has completed booting. After `sys.boot_completed` becomes `1`, check the clock,
foreground app, controller connection and playback separately.

`reboot bootloader` and `reboot recovery` deliberately leave normal Android and
can remove ADB access until local intervention. Bootloader unlock, flash, erase,
factory reset, `pm clear` and uninstall are separate, potentially destructive
operations; they are not normal app-restart or USB-repair steps. An **OEM
unlocking** setting alone is no proof that an unlock occurred. This runbook does
not flash firmware or bypass the lockscreen.

## Compatibility and choosing the management path

The legacy reference tablet uses Android 4.4.2/API 19 and the `play.ott.kitkat`
wrapper. The K960 maintenance target uses Android 5.1/API 22 and a standard OTT
APK. Its observed WebView version is a property of that installation, not of all
K960 devices or all Android 5.1 firmware. Detect versions on each tablet; newer
WebView does not grant newer Android shell commands or system permissions.

The current independent native agent has specific hardware/bootstrap checks for
the legacy reference tablet. Do not copy its root installer or configuration to
the K960 merely because both CPUs can execute ARM code. A regular APK and a web
controller connection do not establish root-agent support, system touch capture
or LockTask/device-owner provisioning.

Use [native Android management](../native/android-agent/README.md) only after its
documented bootstrap and hardware requirements are met. Supported installations
can then use [signed OTA updates](android-ota.md) over outbound HTTPS. For the
normal player, use [`ott` remote control](cli.md) for profile and playback work.
Keep maintenance ADB on USB through a trusted host; this workflow needs neither
`adb tcpip` nor an exposed LAN/Internet ADB port.

References: [Android's ADB guide](https://developer.android.com/tools/adb),
[AOSP Android 5.1 input implementation](https://github.com/aosp-mirror/platform_frameworks_base/blob/android-5.1.1_r1/cmds/input/src/com/android/commands/input/Input.java).
