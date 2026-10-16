#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"
JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
export JAVA_HOME PATH="$JAVA_HOME/bin:$PATH"
BT="$SDK/build-tools/36.0.0"
ANDROID_JAR="$SDK/platforms/android-36/android.jar"
umask 077
mkdir -p build/classes build/dex private
chmod 700 private
if { [ -f private/ott-home.p12 ] && [ ! -f private/signing-password ]; } || \
   { [ ! -f private/ott-home.p12 ] && [ -f private/signing-password ]; }; then
    echo "Incomplete signing identity: restore the matching key and password before building." >&2
    exit 1
fi
if [ ! -f private/signing-password ]; then
    python3 -c 'import secrets; print(secrets.token_hex(32))' > private/signing-password
fi
if [ ! -f private/ott-home.p12 ]; then
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore private/ott-home.p12 \
        -storetype PKCS12 -alias ott-home -keyalg RSA -keysize 3072 -validity 10000 \
        -storepass:file private/signing-password \
        -dname 'CN=OTT Home local deployment' >/dev/null
fi
chmod 600 private/signing-password private/ott-home.p12
"$JAVA_HOME/bin/javac" -source 8 -target 8 -bootclasspath "$ANDROID_JAR" \
    -d build/classes src/play/ott/home/*.java
"$BT/d8" --lib "$ANDROID_JAR" --min-api 22 --output build/dex build/classes/play/ott/home/*.class
"$BT/aapt" package -f -M AndroidManifest.xml -I "$ANDROID_JAR" -F build/unsigned.apk
(cd build/dex && "$BT/aapt" add ../unsigned.apk classes.dex >/dev/null)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
"$BT/apksigner" sign --ks private/ott-home.p12 --ks-key-alias ott-home \
    --ks-pass file:private/signing-password --min-sdk-version 22 \
    --out build/ott-home-0.1.0.apk build/aligned.apk
"$BT/apksigner" verify --verbose --print-certs build/ott-home-0.1.0.apk
"$BT/aapt" dump badging build/ott-home-0.1.0.apk
shasum -a 256 build/ott-home-0.1.0.apk
