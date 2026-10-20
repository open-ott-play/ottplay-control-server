# OTT Home companion

Small dependency-free Android 5.1+ (minimum API 22, target API 24) companion, package `play.ott.home`,
version `0.2.0`. It offers **OTT Home** in Android's Home application picker and
opens the independently installed official `play.ott.foss/.MainActivity`.
The official player's binary, signature, data and update path remain untouched.

Target API 24 permits normal installation on Android 14 and 15 while retaining
minimum API 22 for Android 5.1. The companion requests only the normal
`RECEIVE_BOOT_COMPLETED` permission, shares no files or file URIs, uses no native
libraries or private APIs, and performs no network I/O. The API 23 runtime
permission and API 24 file-sharing/network changes therefore require no extra
permissions or compatibility exceptions. See the [Android 15 installation
requirement](https://developer.android.com/about/versions/15/behavior-changes-all#minimum-target-api-level)
and [Android 7.0 behavior changes](https://developer.android.com/about/versions/nougat/android-7.0-changes).
Targeting API 24 does not establish device acceptance; repeat the Home, missing
player, Settings recovery and reboot checks on each supported Android version.

The build requires Python 3, Java 8-compatible compilation, Android SDK platform
36 and build-tools 36.0.0. `ANDROID_HOME` selects the SDK; `JAVA_HOME` selects the
JDK. The script's defaults match the original macOS build host and can be
overridden through these variables.

Run `bash build.sh` from this directory. The output is
`build/ott-home-0.2.0.apk`. A new local signing
key and random password are generated once under `private/`, with permissions
0600 inside a private directory. Preserve them privately for future updates;
do not commit them. An incomplete key/password pair stops the build, so restore
the matching pair instead of generating a different signing identity. The
script prints signature verification and a SHA-256 digest; review both before
installation. This companion has its own signing key, independent of OTT's. It is not yet
included in the native agent OTA bundle or the official player release assets;
update it by installing a verified APK signed with this same companion key.

Install the verified output on the explicitly selected device:

```sh
adb -s SERIAL install -r build/ott-home-0.2.0.apk
adb -s SERIAL shell am start -n play.ott.home/.StartupActivity
```

After installation, open **OTT Startup** and enable **Start OTT after reboot**
for a dedicated tablet. This explicit, persistent opt-in also works on older Fire
firmware that does not expose a third-party Home picker. Installing or updating
the APK alone does not opt in. The switch is off on a new installation.

Where Android offers a Home picker, press Home and select **OTT Home → Always**
to make Home return to OTT too. Existing installations that selected OTT Home
as default continue to start on boot without enabling the new switch. These are
two independent options: disabling the switch does not clear a default Home.

The boot receiver starts after `BOOT_COMPLETED` if either option is active.
It records `requested` or `blocked` in **OTT Startup**; `requested` only means
Android accepted the launch call. Verify the foreground player, controller
connection, queue, strict kiosk and actual frame/position progress after a real
reboot. A process reload or wake from sleep does not test startup.
If OTT is missing or disabled, a fallback screen explains how to recover and
offers a retry button. It does not continuously retry or seize the foreground.

This is a Home application, **not** device-owner enrollment or lock task. Overview,
notification shade and Settings are not blocked. It does not dismiss a secure
lockscreen. OTT's own strict kiosk mode must separately restrict its player UI.
On Android 5.1, strong system confinement needs a managed-device design and native
player cooperation for entering and leaving lock task.

Rollback: turn off **Start OTT after reboot** in **OTT Startup**, then open
Android Settings → Home and select the original launcher if it was changed. Keep the
original launcher installed and enabled. Over authorized ADB, uninstalling only
`play.ott.home` restores the Home chooser without deleting `play.ott.foss` data.
No device installation or default change is performed by the build script.

See the [ADB guide](../../docs/android-adb.md#use-ott-as-the-tablets-home-screen)
for acceptance checks and the distinction between Home, autostart and managed
kiosk. Source/build availability does not establish that these checks have
passed on a particular tablet. The linked guide includes the dated K960
acceptance record and the tested streaming-engine workaround.

For the complete Plex/strict-kiosk procedure, see the [tablet setup runbook](../../docs/android-kiosk-setup.md) ([Русский](../../docs/android-kiosk-setup.ru.md)).

Test the actual receiver against Android boundary fixtures with
`python3 -m unittest discover -s tests -p test_android_home.py -v` from the
repository root (JDK required). This covers opt-in, unchanged default-Home behavior,
opt-out, irrelevant intents and a blocked launch. Build the real APK and perform
device acceptance separately.
