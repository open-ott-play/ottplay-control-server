# Read-only keyguard probe

This tiny API 16+ APK reports `KeyguardManager.isKeyguardSecure()` and
`isKeyguardLocked()` to the explicit ordered ADB broadcast used by
`tools/android_usb_supervisor.py`. It does not unlock, send input, access the
network or install a service. The exported receiver requires
`android.permission.DUMP`, held by the Android shell, so ordinary applications
cannot invoke it. A PIN/password/pattern is never entered or bypassed.

Build with an Android SDK containing platform 36 and build-tools 36.0.0, a JDK,
and a privately managed PKCS12 key:

```sh
SIGNING_KEY=/private/usb-probe.p12 \
SIGNING_PASSWORD_FILE=/private/password \
SIGNING_ALIAS=usb-probe ./build.sh
adb -s SERIAL install -r build/ott-usb-probe.apk
adb -s SERIAL shell am broadcast -a play.ott.usbprobe.KEYGUARD \
  -n play.ott.usbprobe/.KeyguardReceiver
```

Keep the signing key for updates. Build output is ignored. The supervisor refuses
missing/malformed or secure results. Uninstall with
`adb -s SERIAL uninstall play.ott.usbprobe`; this disables automatic dismissal
on devices lacking another explicit source of non-secure keyguard state.
