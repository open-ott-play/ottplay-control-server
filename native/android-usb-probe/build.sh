#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${ANDROID_HOME:=/opt/homebrew/share/android-commandlinetools}"
: "${JAVA_HOME:=/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
: "${SIGNING_KEY:?Set SIGNING_KEY to a private PKCS12 keystore}"
: "${SIGNING_PASSWORD_FILE:?Set SIGNING_PASSWORD_FILE to a private password file}"
: "${SIGNING_ALIAS:=ott-home}"
BT="$ANDROID_HOME/build-tools/36.0.0"
JAR="$ANDROID_HOME/platforms/android-36/android.jar"
umask 077
mkdir -p build/classes build/dex
"$JAVA_HOME/bin/javac" -source 8 -target 8 -bootclasspath "$JAR" -d build/classes src/play/ott/usbprobe/*.java
"$BT/d8" --lib "$JAR" --min-api 16 --output build/dex build/classes/play/ott/usbprobe/*.class
"$BT/aapt" package -f -M AndroidManifest.xml -I "$JAR" -F build/unsigned.apk
(cd build/dex && "$BT/aapt" add ../unsigned.apk classes.dex >/dev/null)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
"$BT/apksigner" sign --ks "$SIGNING_KEY" --ks-key-alias "$SIGNING_ALIAS" --ks-pass "file:$SIGNING_PASSWORD_FILE" --min-sdk-version 16 --out build/ott-usb-probe.apk build/aligned.apk
"$BT/apksigner" verify --verbose build/ott-usb-probe.apk
