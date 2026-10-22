# Android-Tablet: strenger Plex-Kiosk und Start

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB-Anleitung](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB-Manager](android-usb-manager.md)

Ein dediziertes Tablet benötigt drei getrennt geprüfte Einstellungen: seine Inhaltswarteschlange, die strenge OTT-Kioskrichtlinie und den Android-Start. Eine APK-Installation oder ein Test von Bildschirm-Aus/Fortsetzen belegt keinen Start nach einem Betriebssystem-Neustart.

## Identifizieren und installieren

1. Erfassen Sie das physische Tablet, OTT-Alias/UUID, USB-Manager-Alias, Seriennummer, Modell, Android-ID und SDK. OTT- und USB-Aliase können abweichen; leiten Sie die Identität nicht aus einem Alias ab und verwenden Sie keinen Token eines anderen Tablets.
2. Verwenden Sie die offizielle APK-Variante für dieses SDK, prüfen Sie Release-SHA-256 sowie Paket-/Signaturidentität und installieren Sie mit `install -r`. Erhalten Sie die Playerdaten. Verwaltet der [gemeinsame Manager](android-usb-manager.md) USB, nutzen Sie ihn; starten Sie keinen konkurrierenden ADB-Server und keine `adb`-Befehle ohne Gerätezuordnung.
3. Koppeln Sie den Player mit seinen eigenen Controller-Zugangsdaten und prüfen Sie `ott TABLET`. Laden Sie das gewünschte benannte Preset und führen Sie `ott TABLET load PRESET verify` aus. Heimnetz-Presets und das localhost-Preset eines Entwicklungsrechners sind nicht austauschbar.

## Plex vom Tablet aus prüfen

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Prüfen Sie Bibliotheks-ID, Titel und Elementzahl. Eine erfolgreiche Anfrage vom Administratorrechner belegt keine Verbindung des Tablets. Prüfen Sie bei Fehlern die gespeicherte Adresse, Routing, TLS-Unterstützung und Zugangsdaten auf dem Tablet. Bevorzugen Sie den gültigen HTTPS-Endpunkt des Servers. Benötigt alte Firmware einen privaten LAN-HTTP-Endpunkt, begrenzen Sie dies auf diese Installation, dokumentieren Sie die unverschlüsselte LAN-Übertragung von Tokens und Medien und ändern Sie weder ein gemeinsames Preset noch die globale Zertifikatsprüfung. Veröffentlichen Sie nie Tokens oder private Medien-URLs in Diagnose oder Dokumentation.

Eine Änderung der Plex-Serveradresse löscht gewählte Bibliothekswurzeln. Wählen Sie den Anbieter **Plex**, öffnen Sie **Plex settings → Folders**, markieren Sie die gewünschte Bibliothekszeile (nicht nur **Browse folders**), wählen Sie **Ok**, dann **Save and open library**. Im Player `1.1.58-beta.19` können Serverzugangsdaten allein die Ordnerauswahl öffnen, während `ready` false bleibt; schließen Sie die Auswahl vor Warteschlangenbefehlen ab. Wechseln Sie nicht nur für `ready: true` zum M3U-Anbieter: Eine Plex-Warteschlange kann dort spielen, aber `kiosk on --strict` kann eine wartende M3U-Richtlinie ohne gebundene Medien speichern. Diese sperrt oder stellt die Plex-Warteschlange nicht wieder her.

## Warteschlange starten und sperren

```sh
ott TABLET kiosk off
ott TABLET plex shuffle 'LIBRARY NAME'
ott TABLET plex status
ott TABLET debug --engine
# Continue only after the intended queue is playing.
ott TABLET kiosk on --strict
ott TABLET plex status
ott TABLET kiosk status
ott TABLET debug --engine
```

Für eine geordnete Warteschlange nutzen Sie `plex library 'LIBRARY NAME'` statt `plex shuffle`. Shuffle legt eine Reihenfolge fest; der Kiosk wiederholt sie. Bestätigen Sie die gewünschten Elemente, `enabled: true`, `strict: true` und eine an die richtige Plex-Medienwarteschlange gebundene Richtlinie. Eine `waiting`-Richtlinie ohne Kanal oder Medien ist kein abgenommener Kiosk. Erfassen Sie mindestens zwei Medienmessungen mit steigender Position/Bildzahl und prüfen Sie das Display. Eine Bestätigung oder Anzeige `playing` ohne Fortschritt reicht nicht. Testen Sie den natürlichen Übergang am Elementende; ist ein vollständiger Schleifentest unpraktikabel, dokumentieren Sie diese Grenze ausdrücklich.

Strenger Kiosk steuert die Playeroberfläche. Er ist keine Android-Device-Owner-Registrierung und kein Beweis, dass Home, Recents, Benachrichtigungen und Settings blockiert sind. Erhalten Sie den Systemlauncher und einen beaufsichtigten Wartungsweg.

## Start separat aktivieren

Bauen und prüfen Sie [OTT Home 0.2.0](../native/android-home/README.md) und behalten Sie den bestehenden Signaturschlüssel für Updates. Installieren Sie `play.ott.home` neben `play.ott.foss`. Öffnen Sie `play.ott.home/.StartupActivity` und aktivieren Sie **Start OTT after reboot**. Die Einstellung ist standardmäßig aus. Sie ändert weder die offizielle Playerbinärdatei noch OTA-Signatur, Profildaten oder Belegung der Home-Taste.

Auf Firmware mit Home-Auswahl können Sie zusätzlich **OTT Home → Always** wählen. Ohne Auswahldialog (auch bei getesteter älterer Fire-Firmware) ermöglicht die Startfreigabe einen unabhängigen Start. Zuvor konfigurierte Standard-Home-Installationen behalten ihr Verhalten. Für vollständige Rücknahme beide Mechanismen deaktivieren.

`recover_on_boot` des gemeinsamen USB-Managers ist ein dritter, unabhängiger Mechanismus: Er benötigt NAS und USB-Kabel, behandelt nur einen neuen Boot und startet abgestürzte Apps nicht fortlaufend neu. Er ersetzt keinen autonomen Start. Lassen Sie die Wiederherstellungsrichtlinie beim Test des autonomen Starts aus, damit sie einen fehlenden Start durch die Begleit-App nicht verdeckt. Beachten Sie sichere Sperrbildschirme; entsperren Sie bei Aufforderung regulär.

## Abnahme und Übergabe

Speichern Sie Nachweise privat, ohne Zugangsdaten in öffentlichen Belegen:

- APK-Versionen, Hashes, Zuordnung von physischer/OTT-/USB-Identität und gewähltes Preset.
- Plex-Bibliothek, Elementzahl/Reihenfolge, strenge Richtlinie und anfänglich fortschreitende Wiedergabe.
- Boot-ID vor und nach einem echten Betriebssystem-Neustart; anschließend `sys.boot_completed=1`.
- Kein manueller Playerstart und keine USB-Wiederherstellung zwischen Neustart und Beobachtung.
- OTT-Aktivität im Vordergrund, Controller-Wiederverbindung, wiederhergestellte Warteschlange/strenge Richtlinie und Wiedergabefortschritt. `requested` vom Boot-Empfänger bedeutet nur Anforderungsversand.
- Separater Einschalttastentest: Bildschirm aus, Ton gestoppt, normales Entsperren und Fortsetzen.
- Genaue Grenzen: sicheres Entsperren erforderlich, USB-gestützte Wiederherstellung, ungeprüfte physische Ausstiegsmöglichkeiten, Streamfehler oder Speichermangel. Nennen Sie solche Fälle nicht vollständig abgenommen.

Prüfen Sie vor Übergabe des nächsten Tablets dieselbe Liste, statt den Erfolg des vorherigen Geräts zu übernehmen. Ältere Fire-Tablets benötigen eventuell `hls.js` für M3U, weil herstellereigenes HLS abstürzen kann; das belegt keine stabile Plex-Wiedergabe.

## Wartung und Rücknahme

```sh
ott TABLET kiosk off
```

Öffnen Sie **OTT Startup**, deaktivieren Sie **Start OTT after reboot** und stellen Sie eine geänderte Home-Auswahl zurück. Deinstallieren Sie nötigenfalls nur `play.ott.home` über den geprüften USB-Transport; bewahren Sie `play.ott.foss` und dessen Daten. Das Abschalten des Starts ändert die Warteschlange nicht und deaktiviert Kiosk nicht automatisch.
