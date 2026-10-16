# OTT Home companion

Small dependency-free Android 5.1 (API 22) companion, package `play.ott.home`,
version `0.1.0`. It offers **OTT Home** in Android's Home application picker and
opens the independently installed official `play.ott.foss/.MainActivity`.
The official player's binary, signature, data and update path remain untouched.

The build requires Python 3, Java 8-compatible compilation, Android SDK platform
36 and build-tools 36.0.0. `ANDROID_HOME` selects the SDK; `JAVA_HOME` selects the
JDK. The script's defaults match the original macOS build host and can be
overridden through these variables.

Run `bash build.sh` from this directory. The output is
`build/ott-home-0.1.0.apk`. A new local signing
key and random password are generated once under `private/`, with permissions
0600 inside a private directory. Preserve them privately for future updates;
do not commit them. An incomplete key/password pair stops the build, so restore
the matching pair instead of generating a different signing identity. The
script prints signature verification and a SHA-256 digest; review both before
installation. This companion has its own signing key, independent of OTT's.

Install the verified output on the explicitly selected device:

```sh
adb -s SERIAL install -r build/ott-home-0.1.0.apk
adb -s SERIAL shell am start -n play.ott.home/.HomeActivity
adb -s SERIAL shell input keyevent 3
```

After installation, launch OTT Home once, press Android Home and select
**OTT Home → Always**. Verify another Home press and a normal reboot. The boot
receiver launches the companion only if it resolves as the selected default Home.
If OTT is missing or disabled, a fallback screen explains how to recover and
offers a retry button. It does not continuously retry or seize the foreground.

This is a Home application, **not** device-owner enrollment or lock task. Overview,
notification shade and Settings are not blocked. It does not dismiss a secure
lockscreen. OTT's own strict kiosk mode must separately restrict its player UI.
On Android 5.1, strong system confinement needs a managed-device design and native
player cooperation for entering and leaving lock task.

Rollback: open Android Settings → Home and select the original launcher. Keep the
original launcher installed and enabled. Over authorized ADB, uninstalling only
`play.ott.home` restores the Home chooser without deleting `play.ott.foss` data.
No device installation or default change is performed by the build script.

See the [ADB guide](../../docs/android-adb.md#use-ott-as-the-tablets-home-screen)
for acceptance checks and the distinction between Home, autostart and managed
kiosk. Source/build availability does not establish that these checks have
passed on a particular tablet.
