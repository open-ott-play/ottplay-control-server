# OTT-play Control Server

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15562/badge)](https://www.bestpractices.dev/projects/15562)

[![Fuzzing: RC only](https://img.shields.io/badge/Fuzzing-RC%20only-blue)](docs/fuzzing.md)

[Dokumentation / Wiki](docs/wiki/Home.de.md)

Ein kleiner Server zum Selbsthosten, der Fernsteuerbefehle an OTT-play-Player übermittelt. Die Player senden ausgehende HTTP-Anfragen, sodass ein Fernseher keinen Port für eingehende Verbindungen öffnen muss. Jeder Player hat eine eigene Warteschlange und ein eigenes Zugriffstoken; Befehle werden mit einem separaten Administratortoken gesendet.

Die optionale [zuverlässige Steuerung](docs/reliable-control.md) ergänzt Zugangsdaten für Integrationen mit begrenzten Rechten, begrenzt gespeicherte dauerhafte Befehlsergebnisse, eine fortsetzbare Anwendung von Local/Home-Konfigurationen mit genauer Einstellungsprüfung sowie eine ausdrückliche Übergabe zwischen Live- und Archivwiedergabe. Dafür sind Controller- und CLI-Versionen erforderlich, die diese Anleitung enthalten. Die genaue Einstellungsprüfung und Übergabe benötigen außerdem einen aktualisierten Player. Die unten angegebene ältere Installationsrevision enthält diese Funktionen nicht. Das [Architekturdiagramm](docs/assets/reliable-control.svg) zeigt, wo Einstellungen, Zugangsdaten und Betriebszustand verbleiben.

Der Server besteht aus einer portablen Go-Programmdatei und verwendet für die optionale Diensterkennung eine DNS-Bibliothek mit festgelegter Version. Veröffentlichungspakete gibt es für Linux amd64, arm64 und ARMv7, macOS Intel und Apple Silicon sowie Windows amd64 und arm64. Container-Images unterstützen Linux amd64 und arm64. Für Kubernetes und k3s ist ein Helm-Chart enthalten.

## Server starten

Laden Sie das passende Archiv von den [Releases](https://github.com/open-ott-play/ottplay-control-server/releases) herunter, prüfen Sie es anhand des Release-Manifests und entpacken Sie es. Verwenden Sie unter Windows in den folgenden Befehlen `ottplay-control-server.exe`.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` erstellt zufällige, unterschiedliche Zugangsdaten in einer neuen Datei, auf die unter Unix nur der Eigentümer zugreifen darf. Vorhandene Dateien oder symbolische Links werden nicht überschrieben, und die Zugangsdaten werden niemals ausgegeben. Halten Sie die Datei privat; beschränken Sie ihre ACL unter Windows auf das Konto, unter dem der Server läuft. `config.example.json` beschreibt die Felder und enthält absichtlich ungültige Platzhaltertoken.

Die Standardadresse ist `0.0.0.0:8081` (alle IPv4-Schnittstellen einschließlich LAN). Verbinden Sie Fernseher über die LAN-Adresse und den Port des Servers. Vorhandene Konfigurationen behalten ihren ausdrücklichen `listen`-Wert; ändern Sie ihn in `0.0.0.0:8081` oder verwenden Sie `--listen 0.0.0.0:8081`, um eine bislang nur über Loopback erreichbare Installation freizugeben. Tragen Sie den **genauen Origin der Player-Seite** in `allowed_origins` ein, beispielsweise `http://192.168.1.20:8443`. Schema, Hostname und Port müssen übereinstimmen; hängen Sie keinen Pfad an. Leere Origins erlauben nur Clients außerhalb des Browsers. Konfigurationsänderungen werden nach einem Neustart wirksam. Geräte- und Administratorzugangsdaten bleiben erforderlich.

HTTP wird für ältere TV-Browser in einem vertrauenswürdigen LAN unterstützt. Verwenden Sie bei Verbindungen über nicht vertrauenswürdige Netze HTTPS: `serve --config config.json --tls-cert server.crt --tls-key server.key`, oder terminieren Sie TLS an einem Reverse-Proxy. Über HTTPS geladene Player-Seiten können sich im Browser nicht mit einem reinen HTTP-Server verbinden. Native Anwendungen behalten außerdem die Transportrichtlinie ihres Betriebssystems bei; diese Einstellung lockert sie nicht.

Direktes HTTPS prüft die bereitgestellte Zertifikatskette, bevor der Listener geöffnet wird: RSA-Schlüssel müssen mindestens 2048 Bit, ECDSA-Schlüssel mindestens 224 Bit lang sein; Ed25519 wird ebenfalls unterstützt. Ersetzen Sie ein zu kleines Zertifikat vor dem Upgrade. Siehe [TLS-Zertifikatsrichtlinie und Geltungsbereich](docs/security-design.md#direct-https-certificate-policy).

## Player verbinden

Verwenden Sie eine Player-Version, die die Verbindung zum Befehlsserver unterstützt. Öffnen Sie **Einstellungen → Fernsteuerung** und geben Sie Folgendes ein:

1. **Serveradresse:** IP-Adresse oder Hostname des Servers, optional mit Port, oder eine vollständige `http://`- / `https://`-Adresse. Ein Host ohne Schema verwendet standardmäßig HTTP-Port 8081.
2. **Zugriffscode:** das `token` des Eintrags dieses Players in `devices`, niemals `admin_token`.
3. **Verbinden:** aktivieren Sie die Abfrage und prüfen Sie den im selben Dialog angezeigten Status.

Der Player speichert diese Verbindung nur in der aktuellen Installation und schließt sie von Einstellungssicherungen und Cloud-Übertragungen aus. Ein separater lokaler HTTP-Listener ist nicht erforderlich. Webbrowser benötigen einen erlaubten CORS-Origin; paketierte TV-Seiten mit `Origin: null` können die ausdrückliche Einstellung `allow_null_origin` für vorgesehene Geräte-/Bootstrap-Routen verwenden. Administrator- oder Diagnoseoperator-Routen werden dadurch nicht freigegeben.

## Fernbedienung im Terminal

Die [bebilderte Android-OTA-Anleitung](docs/android-ota.md) unterscheidet gewöhnliche Capacitor-WLAN-Updates (`ott DEVICE update --variant api22|full`, standardmäßig die neueste Beta; `--version` wählt eine Veröffentlichung) vom separaten Agenten mit Root-Zugriff. Sie erklärt signierte Agenten- und APK/Helper-Updates, die Wiederherstellung und die Prüfung des installierten Ergebnisses.

Die [Android-ADB-Anleitung](docs/android-adb.md) behandelt USB-Zugriff über einen Arbeitsplatzrechner oder Synology, Bildschirm- und Tastatursteuerung, Screenshots, Protokolle, APK-Installation und normale Neustarts einschließlich Einschränkungen älterer Tablets.

Die optionale [DNS-SD-Erkennung und Kopplung](docs/discovery.md) ermöglicht Playern, den Controller über ihre Netzwerkdomäne zu finden und nach `ott approve NAME CODE` ein individuelles Gerätetoken zu erhalten. DNS enthält ausschließlich Dienstmetadaten. Vorhandene manuelle Verbindungen funktionieren ohne aktivierte Erkennung weiter.

Verwenden Sie die Python-CLI für Player-Aliase, aktuelle Lautstärke, Kanalsuche, aktuelle EPG-Daten, Anbieterwahl und unterstützte Anbieterkonfiguration: [CLI-Anleitung](docs/cli.md). Server und Player müssen beide die Anfrage-/Antworterweiterung enthalten.

Die Python-CLI wird aus einer Arbeitskopie des Quellcodes installiert, getrennt vom nativen Serverarchiv. Die [Installations- und Konfigurationsschritte](docs/cli.md#installation-and-connection) behandeln macOS/Linux und Windows, die Einrichtung von Python/PATH und die benötigten CLI-Module. Sie legen einen geprüften Entwicklungs-Commit der Version 0.1.1 mit Workbench und Offline-Prüfung fest; die stabile CLI v0.1.0 und native Serverarchive enthalten diese Befehle nicht. [Aktualisieren oder rollen Sie die CLI zurück](docs/cli.md#update-select-or-roll-back-the-cli), ohne eine vorhandene Arbeitskopie, einen Starter oder private Konfiguration zu ersetzen. Lokale Starter können den schreibgeschützten [Playlist-Resolver](docs/cli.md#local-playlist-resolver) verwenden, um ohne Controller nach Kanälen, aktuellen Sendungen und abspielbaren Archiven zu suchen. Nach Installation und Konfiguration:

```sh
ott --help                 # all CLI command families
ott devices                # controller registrations and last contact
ott a1                     # this player's status and available controls
ott a1 prev                # previous channel in the active category (previous also works)
ott a1 next                # next channel, wrapping at the end of the category
ott a1 channels "РЕН"       # list channel-name matches without playing
ott a1 "РЕН ТВ HD"          # search and play a matching channel
ott a1 volume 35            # vol/v are equivalent command aliases
ott a1 aspect               # current Fit/Fill mode and saved-setting readback
ott a1 aspect fill          # fill the screen proportionally, cropping the edges
ott a1 aspect "Fit to screen" # full mode name; preserve the whole picture
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 standby             # stop playback and enter player standby; stay connected
ott a1 go                  # leave standby and start configured playback (start/wake also work)
ott a1 screenshot          # save a PNG; browser builds need a selected capture source
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
ott a1 test run media-progress --report ./a1-progress
ott report verify ./a1-progress --json  # re-evaluate saved evidence offline
```

`a1` ist ein Beispielalias für einen registrierten Player, kein Computerhostname. Siehe [Registrierung und Kopplung](docs/cli.md#register-and-connect-a-player), [alle Befehle mit Beispielen](docs/cli.md#commands), [Trennen und Widerrufen des Zugriffs](docs/cli.md#disconnect-deregister-or-revoke-a-player) und [Fehlerbehebung](docs/cli.md#troubleshooting).

Verwenden Sie für einen dauerhaft eingeschalteten Player [standby und go](docs/cli.md#stop-playback-while-keeping-the-player-online), um die Wiedergabe anzuhalten und später wieder zu starten, ohne die App zu beenden. `stop` bleibt ein Alias für `standby`; `start` und `wake` bleiben Aliase für `go`. Die Fernsteuerung muss verbunden bleiben; Standby verspricht daher keinen vollständig ausbleibenden Netzwerkverkehr. `ott a1` zeigt die vom Player angebotenen Operationen. Ein angenommener Befehl bestätigt nicht, dass Abschalten oder Wiedergabestart abgeschlossen sind.

[Befehle zum Seitenverhältnis](docs/cli.md#aspect-ratio-fit-and-fill) erfordern eine aktualisierte CLI, einen Controller und einen Player, der die Fähigkeit `aspect` anbietet. Eine Änderung wird nach Bestätigung angewendet; lesen Sie `aspect` erneut aus, um den Modus und seine Speicherung zu prüfen. Fill beschneidet proportional, statt das Bild zu verzerren.

Entfernte [Screenshots](docs/cli.md#remote-screenshots) erfordern eine aktualisierte CLI, einen Controller und einen Player mit unterstütztem Aufnahmeadapter. Das Aktivieren der Fernsteuerungsverbindung des Players erlaubt diesem Controller Screenshots und Diagnosen; native Screenshots benötigen keinen separaten Berechtigungsschalter. Browser benötigen weiterhin ihre lokale Quellenauswahl. Bilder können Playereinstellungen, PIN-Bildschirme und sichtbare Zugangsdaten enthalten; verbinden Sie sich deshalb nur mit einem vertrauenswürdigen Controller. `ott a1` meldet die Verfügbarkeit und eine erforderliche Quellenauswahl. Screenshots sind von der strukturierten Telemetrieaufzeichnung getrennt.

Um alle zu einem Titel passenden VPortal-Videos zu mischen und wiederholt abzuspielen:

```sh
ott l vpr "wedding"
```

`vpr` mischt die vollständige Auswahl einmal und wiederholt dann diese Warteschlange. Für eine neue Mischung führen Sie den Befehl erneut aus. `vp` spielt Treffer in Katalogreihenfolge ab; `vp --list` listet sie auf, ohne die Wiedergabe zu ändern. Siehe [VPortal-Befehle](docs/cli.md#commands).

Für eine ausdrücklich festgelegte Plex-Sequenz mit der gespeicherten Plex-Konfiguration des Players:

```sh
ott l plex preview 78777 78776 78775  # check availability without starting playback
ott l plex play 78777 78776 78775     # starts the first item from zero, in this order
ott l plex queue                     # current item and preparation/playback state
ott l plex next                      # advance once; no wrap at the last item
ott l plex stop                      # stop and clear this queue
ott l plex libraries                 # list libraries (requires plex_library support)
ott l plex shuffle "Tom and Jerry"   # shuffle one complete library, up to 500 items
ott l plex library --preview id:16    # read-only check of an explicit library
```

Die CLI wartet standardmäßig bis zu 45 Sekunden. `ott -t 45 l plex shuffle "Tom and Jerry"` gibt dieselbe Antwortwartezeit ausdrücklich an; die Wiedergabedauer der Videos wird damit nicht begrenzt. Das Erfassen großer Plex-Bibliotheken kann länger dauern als eine kurze Wartezeit von 10–15 Sekunden. Setzen Sie `-t SECONDS` (oder `--timeout SECONDS`) vor den Player-Alias. Siehe [Wartezeiten für Plex-Bibliotheken](docs/cli.md#plex-libraries-by-name) für Vorschau-Beispiele und das Vorgehen bei einer Zeitüberschreitung einer Wiedergabeanfrage.

Plex-Warteschlangen erfordern eine CLI, einen Controller und einen Player mit `plex_queue`. `plex play --shuffle ID...` mischt die angegebenen IDs einmal. Warteschlangen stoppen nach dem letzten Eintrag, sofern eine vorhandene Kioskrichtlinie sie nicht wiederholt. Solange eine Warteschlange besteht, beziehen sich die normalen Befehle `next` und `prev` darauf; `plex stop` löscht sie. Eine Vorschau wechselt niemals den Anbieter oder ersetzt die aktuelle Wiedergabe. Grenzen und Bereitschaft erläutern die [Plex-Warteschlangenbefehle](docs/cli.md#ordered-plex-queues). Die Bibliothekssuche erfordert zusätzlich einen aktualisierten Player und Controller mit `plex_library`; sie verwendet die gespeicherte Plex-Verbindung des Players und lehnt mehrdeutige oder unvollständige Auswahlen ab. Siehe [Plex-Bibliotheken nach Namen](docs/cli.md#plex-libraries-by-name).

### Profile für lokale Videos speichern

Mit einem aktuellen Player und Controller verwenden lokale Dateien/Ordner den Anbieter `offline`:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Verbinden Sie Dateien/Ordner zuerst am Player. Speichern erfasst ihre Konfiguration und das ausgewählte Video, ohne die Wiedergabe zu starten. Der Export überträgt nur Beschreibungen; Medien und Zugriffsberechtigungen des Betriebssystems/Browsers bleiben auf dem Gerät. Eine andere Installation benötigt eine lokale Zuordnung, und Laden bedeutet keine bestätigte Wiedergabe. Siehe [Profile für lokale Videos](docs/cli.md#local-videos-profiles) für Einrichtung, Fähigkeiten, Grenzen des portablen Formats und Fehlerbehebung.

### Den aktuellen Eintrag profilübergreifend speichern

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Favoriten bleiben bei normalen Neustarts auf dem Zielplayer erhalten. Ihre Auswahl lädt den gespeicherten Anbieter bzw. das Profil und ermittelt einen neuen Link; eine geänderte Quelle oder ein fehlender Eintrag führt zum Fehler, ohne einen anderen Kanal oder Film einzusetzen. CLI, Controller und Player müssen `favorites` unterstützen; `ott l` zeigt die verfügbaren Operationen. Siehe [profilübergreifende Favoriten](docs/cli.md#favorites-across-profiles) für Aliase, Grenzen, Bestätigungssemantik und Fehlerbehebung.

### Eine gespeicherte Konfiguration laden

Speichern Sie benannte Konfigurationen im privaten Abschnitt `presets` von `~/.config/ottplay-control/cli.json`. Jede Konfiguration enthält nummerierte M3U-Profile (Playlist und Archivstunden), Plex-Zugangsdaten und das nach Abschluss des Ladens auszuwählende M3U-Profil. Siehe [Konfigurationsbeispiel](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

Die CLI prüft zuerst die gesamte Konfiguration und wartet dann vor jeder weiteren Änderung auf die Bestätigung des Players. Sie behandelt das Laden des Anbieters, speichert Plex und die M3U-Plätze und wählt das konfigurierte M3U-Profil. Ein Fehler stoppt die Sequenz und meldet die bestätigten Schritte; ein Schreibvorgang mit ungewissem Ergebnis wird niemals automatisch wiederholt. Ein zusätzlicher Profildienst oder Controller-Neustart ist nicht erforderlich. Halten Sie diese Konfiguration privat: Sie enthält Anbieterzugangsdaten.

## Einen Befehl senden

Dieses Beispiel liest das Administratortoken aus der lokalen privaten Konfiguration, ohne es in die URL oder Befehlszeilenargumente aufzunehmen:

```python
import json
import urllib.request

with open("config.json", encoding="utf-8") as stream:
    config = json.load(stream)
request = urllib.request.Request(
    "http://127.0.0.1:8081/api/webhook/commands?device_id=living-room",
    data=json.dumps({"command": "set_volume", "volume": 35}).encode(),
    headers={
        "Authorization": "Bearer " + config["admin_token"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status)
```

Weitere Befehle wählen einen Kanal nach Nummer oder Namen, einen zufälligen Kanal, wechseln Anbieter, zeigen eine Nachricht, ändern bei Unterstützung eine M3U-Playlist oder beenden den Player. Die Verfügbarkeit hängt von Playerplattform und aktuellem Anbieter ab. Siehe [API-Vertrag](docs/api.md).

Bei einem Player mit Unterstützung des eigenständigen VPortal-Anbieters wählt `ott a1 provider
vportal` dessen eigene 15 Profile. Verwenden Sie `profile N vportal LINK`, `profile N
name NAME` oder eine private Datei `profile-config N FILE` mit `name` und `vportal`; wählen Sie den konfigurierten Platz anschließend mit `profile N`. Vorhandene M3U-Profile bleiben getrennt. `profiles` meldet Metadaten, ohne Kontolinks oder Schlüssel offenzulegen.

Nach dem Start eines VPortal-Films oder einer Episodenwarteschlange sperrt `ott a1 kiosk on` diese Auswahl. Der Player behält Warteschlange und Position über Neuladevorgänge hinweg und löst Medien-URLs für die Wiederherstellung erneut auf. Verwenden Sie `kiosk off`, um einen anderen Titel zu wählen oder Profile zu ändern, und dann `kiosk on`, um die Auswahl zu sperren. Die Kioskbestätigung zeigt Titel und Episodenzahl; Anbieteranfragen und Stream-URLs verbleiben auf dem Player.

Verwenden Sie `ott a1 kiosk on --strict` für die Wiedergabe für Kinder: Eine lokale Berührung zeigt die Videofußleiste. Aktualisierte Core-Player erlauben Antippen oder Ziehen ihrer Zeitleiste, um innerhalb der aktuellen VPortal-/Plex-Episode zu suchen; andere lokale Bedienelemente bleiben gesperrt. Ältere Player, Kanalkioske und Wrapper, die Zeitleistengesten abfangen, behalten eine schreibgeschützte Fußleiste. Eine bestehende Sperre behält ihr Ziel. Lösen Sie sie vom Controller mit `ott a1 kiosk off`. Auf Android-Websites erfordert der Schutz vor dem Verlassen über Systemfunktionen zusätzliches App-Anheften mit PIN; siehe [Kioskmodus](docs/cli.md#kiosk-mode).

## Ferndiagnose

Beginnen Sie mit dem [schreibgeschützten Workbench-Schnellstart](docs/workbench.md#quick-start) für `doctor`, UI-/Medien-`inspect`, das Nachschlagen von Operationsbestätigungen, Nachweispakete, `health`, Web-/native `media-progress` und Offline-`report verify`. Er verwendet die vorhandene Administrator-CLI-Konfiguration und Player-Aliase. Der native Pfad ist für den separat bereitgestellten Android-Wartungsagenten; Tauri-Player verwenden Webinspektion. Berichte unterscheiden Decoderfortschritt von der physischen Prüfung von Bildschirm und Ton. Siehe [Workbench-Fehlerbehebung](docs/workbench.md#troubleshooting) für fehlende Fähigkeiten, nicht verfügbare Pfade und unvollständige Nachweise.

Die optionale [Protokoll-2-Diagnose](docs/remote-diagnostics.md) stellt ausdrücklich aktivierte Sitzungen pro Laufzeit mit separaten, begrenzten Operatorzugangsdaten und begrenzter strukturierter Telemetrie bereit. Die aktivierte Playerverbindung erteilt die lokale Berechtigung ohne erneute Vertrauensabfrage; jede Aufzeichnung hat weiterhin eine begrenzte Sitzungsdauer. Die Diagnose-Steuerungsabfrage ist von der vorhandenen Befehlswarteschlange unabhängig.

Die [Anleitung für Diagnose-CLI und MCP](docs/diagnostics-cli.md) behandelt Installation, Operatorzugangsdaten, Verbindungseinrichtung, sämtliche Diagnosebefehle/-werkzeuge, Aufnahme- und Reparaturbeispiele, Laufzeit-Widerruf und Fehlerbehebung. `ott diagnostics --help` listet diese separate Befehlsfamilie; gewöhnliche Player-Aliase und Administratorzugangsdaten gewähren keinen Diagnosezugriff.

## Bereitstellungspakete

- [Android-Tablet: strenger Plex-Kiosk und Start](docs/android-kiosk-setup.md)
- [Gemeinsame Android-USB-Verwaltung und Startwiederherstellung](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS und Windows](docs/deployment.md)
- [Konfiguration, Zustellungssemantik und Grenzen](docs/api.md)
- [Veröffentlichungs- und Prüfprozess](docs/development.md)

Warteschlangen sind begrenzt, laufen standardmäßig nach 60 Sekunden ab und liegen im Arbeitsspeicher. Ein Serverneustart verwirft ausstehende Befehle. Betreiben Sie eine Replik; unabhängige Repliken teilen keine Warteschlangen. Ein Player bestätigt einen Zustellversuch, nicht die bestätigte Wiedergabe oder Hardwarelautstärke. Netzwerk-Wiederholungen werden innerhalb einer laufenden Playersitzung dedupliziert, nicht über einen Anwendungsneustart hinweg.

Dieser Server folgt der Befehls-API des optionalen Beispiels `local_proxy.py` in [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), mit getrennten Administrator-/Gerätezugangsdaten und bestätigter Zustellung. Der alte Proxy bleibt eine separate Komponente. Dieses Projekt fungiert nicht als Medienproxy und ändert weder HLS, EPG noch die Anbieter-Netzwerkkommunikation des Players.

## Lizenz

MIT. Urheberhinweise zu eingebundenen Veröffentlichungswerkzeugen stehen in `NOTICE` und `LICENSES/`.

## Projektpflege

Siehe [Anforderungen an Beiträge und Tests](CONTRIBUTING.md), die [Richtlinie für Sicherheitsmeldungen](SECURITY.md), das [Sicherheitsdesign](docs/security-design.md) und die [OpenSSF-Nachweise und verbleibenden Kriterien](docs/openssf-evidence.md).

Plex-Warteschlangen akzeptieren auf aktuellen Playern bis zu 500 IDs. Verwenden Sie `ott f10 plex play --shuffle ID...` zum einmaligen Mischen und anschließend `ott f10 kiosk on --strict` bei ausgewähltem Plex-Anbieter, um diese Warteschlange zu wiederholen und zu sperren. `ott f10 kiosk off` hebt die Sperre auf. Ältere Player behalten ihre gemeldete Warteschlangengrenze.

## Fuzzing von Release Candidates

Öffentliche Release Candidates führen begrenztes natives Fuzzing der in der [Fuzzing-Anleitung](docs/fuzzing.md) beschriebenen Eingabegrenzen aus. Mutationskampagnen laufen nicht für PRs, Betas oder nächtliche Builds. Die Anleitung behandelt Umfang, Budgets, Nachweise und die Reproduktion von Regressionen.

## Community und Sicherheitsnachweise

Siehe [Mitwirken](CONTRIBUTING.md), [Projektführung](GOVERNANCE.md), den [Verhaltenskodex](CODE_OF_CONDUCT.md), die [Roadmap](ROADMAP.md) und [OpenSSF-Nachweise](docs/openssf-evidence.md). Der Nachweisindex unterscheidet unterstützte Sicherheitsprofile, tatsächliche Testergebnisse und verbleibende Silver-Anforderungen.
