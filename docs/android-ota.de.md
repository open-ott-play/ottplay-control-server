<div lang="de" dir="ltr">

# Android-OTA: gewöhnliche Player und der separate Root-Agent

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Verwenden Sie für **k8a/fire8 und andere Capacitor-Player** den WLAN-Updater des normalen Players, nicht den Befehl des Root-Agenten:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Ohne `--version` wird die neueste veröffentlichte Beta gewählt. Verwenden Sie ab Android 7+ `full` statt `api22`. Die CLI ermittelt das offizielle Release, prüft sein Manifest, bereitet die APK vor und fragt ihren Zustand ab, bevor sie einmalig die Installation anfordert. Android kann eine Bestätigung am Tablet verlangen. Deaktivieren Sie vor Wartungsarbeiten den Kiosk und entsperren Sie geschützte Einstellungen ausdrücklich; der Befehl verändert diese Richtlinien niemals. Siehe [vollständigen CLI-Ablauf](cli.md#capacitor-apk-updates-over-wi-fi).

Dafür sind eine bereits installierte APK mit `AppUpdate`-Unterstützung, WLAN und ein laufender, mit dem Controller verbundener Player erforderlich. Reguläre spätere Updates benötigen kein USB; ein offline befindliches Tablet oder eine gestoppte WebView lassen sich damit jedoch nicht erreichen. Erstinstallation, fehlender Updater, defekte App oder verlorene Netzwerkverbindung können weiterhin eine lokale Wiederherstellung erfordern. Ein automatischer Release-Zeitplaner oder allgemeiner unbeaufsichtigter Installer ist damit nicht zugesagt.

Der Rest dieses Handbuchs beschreibt einen **anderen Wartungsagenten für a1 mit Root-Zugriff**, beschränkt auf die vorbereitete Matricom/API19-Installation `play.ott.kitkat`. Dieser ist auf gewöhnlichen Fire-/Capacitor-Geräten weder bereits installiert noch nutzbar.

Nach der einmaligen USB-Ersteinrichtung mit Root kann der unabhängige Android-Agent signierte Updates über seine ausgehende HTTPS-Verbindung empfangen. Er benötigt keine funktionierende WebView, um sich selbst zu aktualisieren. Agent 0.1.2 unterstützt außerdem ein koordiniertes Paket aus APK und System-Touch-Helper, mit gespeicherten Wiederherstellungsdateien und Prüfung vor dem endgültigen Abschluss.

Dieses Handbuch erklärt den vollständigen Weg. Genaue Manifestfelder, Grenzen und Hardwareanforderungen finden Sie in der [Referenz des nativen Agenten](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Was aktualisiert wird

![OTA-Architektur: geprüfte Release-Bytes werden signiert, über direktes HTTPS veröffentlicht, vom unabhängigen Agenten validiert und nach der Installation geprüft. Das Web-Deployment ist separat.](images/ota-architecture.svg)

- **Nativer Agent:** `kind: agent` ersetzt seine eigene ARMv7-Binärdatei. Dieser Weg funktioniert auch bei deaktiviertem OTT und einer anderen verwendeten HOME-App.
- **Nur APK:** `kind: apk` ersetzt den Wrapper unter Beibehaltung seiner Daten. OTT muss aktiviert und im Vordergrund sein. Der eingebettete Helper des Kandidaten muss exakt zum installierten System-Helper passen.
- **APK und Touch-Helper:** `kind: bundle` aktualisiert beide Komponenten als Paar. Ein älterer Agent muss zunächst ein Agenten-Update erhalten, das Bundles unterstützt.
- **Gehosteter Player:** Stellen Sie das geprüfte Web-Artefakt am bestehenden Ursprung bereit, laden Sie dann einen aktiven Player neu und prüfen Sie ihn. Ein APK-Update stellt das gehostete JavaScript nicht bereit. Siehe [Release-Ablauf](release-workflow.md).
- **Controller und CLI:** Stellen Sie den Controller bereit und installieren Sie den vollständigen CLI-Modulsatz separat. Keines dieser Updates ersetzt Code auf dem Tablet.

Firmware, Kernel, System-WebView, Root-Broker und Startskripte liegen außerhalb dieses OTA-Formats. Die [native Referenz](../native/android-agent/README.md#bootstrap) beschreibt Grenzen von Ersteinrichtung und Wiederherstellung. Unterstützte OTA-Operationen benötigen weder Netzwerk-ADB noch eine beliebige Remote-Shell.

## Vom geprüften Release zur bestätigten Installation

1. **Geprüfte Bytes auswählen.** Führen Sie geprüfte Änderungen nach erfolgreichem CI zusammen. Laden Sie das gewünschte Release-Artefakt herunter und prüfen Sie Quellrevision, unveränderliche Release-Nachweise und Prüfsumme. Extrahieren Sie die Binärdatei `agent` aus dem nativen Archiv; das Archiv selbst ist keine installierbare Agenten-Nutzlast.
2. **Vorbereiten und signieren.** Verwenden Sie den vorhandenen privaten Ed25519-Schlüssel, dessen öffentlicher Schlüssel bei der Ersteinrichtung fest hinterlegt wurde. Halten Sie ihn und die Gerätezugangsdaten privat.
3. **Veröffentlichen und Transport prüfen.** Veröffentlichen Sie nur die Nutzlast und `signed.json` unter unveränderlichen HTTPS-Pfaden. Beide müssen ihre Bytes direkt mit HTTP 200 zurückgeben; der Updater lehnt Umleitungen ab. Führen Sie den Befehl `verify` des Paketwerkzeugs aus.
4. **Ein Update senden.** Notieren Sie die Anfrage-ID und prüfen Sie nach der Bestätigung den Status. Eine verlorene oder ungewisse Antwort ist kein Grund, eine Operation blind zu wiederholen.
5. **Wirkung bestätigen.** Vergleichen Sie frisch ermittelte installierte Hashes und Versionen mit dem vorbereiteten Release. Prüfen Sie bei einem aktiven Player außerdem Kiosk-Ziel, Warteschlange und fortschreitende Wiedergabe. Physisches Bild und Ton erfordern Beobachtung.

Beispiel für ein Agenten-Release; ersetzen Sie Version und Pfade durch die tatsächlich gewählten Werte:

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

`ENVELOPE_SHA256` ist das `envelope_sha256` des Paketwerkzeugs, nicht der Nutzlast-Hash. `accepted` bedeutet Annahme, nicht erfolgreiche Installation. Nach der Selbstersetzung kann ein alter Operationsbeleg `unknown` bleiben; die neue Laufzeit, die erwartete Agentenversion und der passende installierte Hash liefern den Installationsnachweis. Die [CLI-Referenz](cli.md#independent-android-agent) beschreibt unabhängiges natives Routing und die Behandlung von Belegen.

## Wiederherstellung der APK-/Helper-Transaktion

![APK-/Helper-Transaktion: Vorprüfung, bisheriges Paar sichern, anwenden, prüfen, abschließen; Fehler stellen das frühere Paar wieder her, unvollständige Wiederherstellung blockiert App-Änderungen weiter.](images/ota-transaction.svg)

Das Bundle enthält genau `app.apk` und `ott-touch-guard`. Das Paketwerkzeug kann es mit `--kind bundle --apk` direkt aus der signierten Wrapper-APK erstellen. Es extrahiert den eingebetteten Helper, sodass die beiden ausgelieferten Kopien übereinstimmen. Siehe [Bundle-Befehl und Anforderungen](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Vor Dateiänderungen verlangt der Agent ein passendes installiertes Paar, ausreichend freien Platz, einen reagierenden OTT-Player im Vordergrund, eine schreibgeschützte Systempartition und gemeldete APK-Downgrade-Unterstützung. Bei gesperrtem Kiosk prüft er dieselbe TV-Kanal-ID oder die geordneten VPortal-IDs; gleiche Warteschlangenlängen allein reichen nicht. Nicht unterstützte gesperrte Warteschlangen werden abgelehnt. Eine absichtlich deaktivierte OTT-App wird ebenfalls abgelehnt, wodurch die derzeitige HOME-App des Benutzers erhalten bleibt.

Wiederherstellungsdateien und die ausstehende Transaktion werden vor dem Stoppen der App dauerhaft gespeichert. Der Agent wartet auf das Ende der App und des alten Helpers, ersetzt den Helper atomar, versetzt `/system` wieder in den Nur-Lese-Modus, installiert die APK und prüft das Ergebnis. Fehler beim Anwenden oder Prüfen lösen die Wiederherstellung des bisherigen Paars aus. Die Wiederherstellung beim Start stellt Dateien wieder her, ohne OTT zu starten oder HOME zu übernehmen.

Kann die Wiederherstellung nicht abgeschlossen werden, ist `updates.recovery_failed` wahr. Diagnose und signierte Reparaturen nur des Agenten bleiben verfügbar; App-Änderungen und der Wiedergabe-Watchdog werden blockiert. Die gespeicherte Transaktion darf nicht gelöscht werden, um eine Installation zu erzwingen. Die Wiederherstellung von Binärdateien macht App-Datenmigrationen nicht rückgängig, und die Firmware kann ein Downgrade weiterhin ablehnen. Siehe [Wiederherstellungsgrenzen](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Ergebnisse und Abnahmeprotokoll

![Prüfdiagramm: 488 CLI-Tests, 576 Release-Werkzeugtests und 6 OTA-Pakettests; das Referenztablet verwendet den geprüften neuen Agenten, die physische APK-/Helper-Abnahme steht noch aus.](images/ota-validation.svg)

Das Implementierungsaudit am 14. Oktober 2026 bestand 488 CLI-Tests (einer übersprungen), 576 Release-Werkzeugtests, sechs OTA-Pakettests, native Go-Race-/statische Prüfungen und einen ARMv7-Build. Diese Zahlen beschreiben den Umfang der Testsuiten, nicht Abdeckung oder Leistung. Tests mit gezielten Fehlern prüfen ungültige Archive, unterbrochene Transaktionen, teilweise Wiederherstellung, Zieländerungen und Wiederherstellungssperren. Die unabhängige Prüfung fand und korrigierte außerdem eine unzureichende Prüfung der Warteschlangenidentität und unterschiedliche APK-/Helper-Prüfungen auf Host und Gerät.

Stand der Prüfung am 15. Oktober 2026:

- Die OTA-Implementierung wurde mit [Controller-PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) zusammengeführt und ist in [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4) enthalten.
- Das aktuelle und das vorherige signierte Agentenpaket sind auf dem [OTA-Auslieferungshost](https://android-updates.ottplay.here.now/) veröffentlicht. Ihre Nutzlast- und Envelope-Bytes wurden über direktes HTTPS erfolgreich heruntergeladen und verglichen.
- Das Referenztablet wurde über den unabhängigen OTA-Kanal von `0.1.1-beta.21` auf `0.1.2-beta.4` aktualisiert. Version des laufenden Agenten und installierter SHA-256 entsprachen dem gewählten Release; seine Laufzeit änderte sich, die OS-Boot-ID blieb gleich. OTT hatte weiterhin keinen laufenden App-Prozess, und es wurde weder ein App-Start noch eine HOME-Auswahl gesendet.
- Der native Status meldete den Hash des System-Touch-Helpers, kein ausstehendes Bundle und keinen Wiederherstellungsfehler. APK-Metadaten waren nicht verfügbar; diese Beobachtung belegt daher weder Version noch Hash der installierten APK.
- Die koordinierte APK-/Helper-Transaktion ist auf Hostseite validiert; physische Installation, Downgrade-Wiederherstellung, Bild und Ton sind auf dem Referenztablet noch nicht abgenommen.

Bewahren Sie für jede spätere Ausrollung Release-Quelle und Prüfsummen, den vorbereiteten Envelope-Hash, Anfrage-ID, nativen Status vor und nach dem Update sowie das separate physische Abnahmeergebnis auf. Ein erfolgreicher Download oder Befehlsbeleg ersetzt diese Beobachtungen nicht.

</div>
