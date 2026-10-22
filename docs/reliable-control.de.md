<div lang="de" dir="ltr">

# Zuverlässige Steuerung und Übergabe der Wiedergabe

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Dokumentationsübersicht](wiki/Home.md)

Diese Funktionen benötigen Controller und CLI aus einer Revision, die dieses Handbuch enthält. Der exakte Einstellungsvergleich und die Übergabe benötigen außerdem einen aktualisierten Player. Ältere Release-Archive und die historische CLI-Revision aus der Installationsanleitung erhalten diese Funktionen nicht automatisch. Installieren Sie **alle Dateien `cli/*.py` gemeinsam**, einschließlich `preset_session.py`; behalten Sie die private Konfiguration bei.

![Ablauf von Steuerung, Checkpoint und Übergabe](assets/reliable-control.svg)

## Begrenzte, dauerhaft gespeicherte Ergebnisse aktivieren

Ergänzen Sie diesen optionalen Block in der privaten Serverkonfiguration, validieren Sie sie und starten Sie den Server neu. Wählen Sie einen absoluten Pfad auf einem lokalen persistenten Dateisystem, auf das das Dienstkonto schreiben darf. Unter Unix muss das Verzeichnis privat sein (0700); vorhandene Journal- und Sperrdateien müssen reguläre private Dateien sein (0600). Beschränken Sie unter Windows die Verzeichnis-ACL auf das Dienstkonto. Ein Journal gehört genau einem Prozess.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Der Server erstellt fehlende Verzeichnisse mit Modus 0700. Die Grenzen sind 1–4096 Datensätze, 60–86400 Sekunden und 4 MiB auf dem Datenträger. Die Aufbewahrung muss `command_ttl_seconds` abdecken. Ein volles Journal lehnt neue Operationen mit Schlüssel ab, statt noch gültige Einträge zu verdrängen. Beschädigte Dateien, ein unsicheres Unix-Verzeichnis, ein weiterer Schreiber oder ein fehlgeschlagener dauerhafter Schreibvorgang führen zur sicheren Ablehnung. Standard bleibt der vorhandene Befehlspfad im Arbeitsspeicher.

Die mitgelieferte systemd-Unit erstellt `/var/lib/ottplay-control` mit Modus 0700. Stellen Sie für Compose ein Hostverzeichnis bereit, das UID 65532 gehört, setzen Sie `CONTROL_SERVER_STATE` auf dessen absoluten Pfad und kombinieren Sie `deploy/compose.yml` mit `deploy/compose.journal.yml`. Letztere ergänzt nur den beschreibbaren Zustands-Mount; das Root-Dateisystem bleibt schreibgeschützt. Stellen Sie für Helm einen Persistent Claim separat bereit und setzen Sie `journalExistingClaim=CLAIM_NAME`. Der Claim wird unter `/var/lib/ottplay-control` eingehängt; verwenden Sie das oben gezeigte private Unterverzeichnis. Das Chart behält eine Replik, `Recreate` und `fsGroupChangePolicy: OnRootMismatch` bei, damit erneutes Einhängen die Rechte bereits erstellter privater Verzeichnisse nicht rekursiv erweitert. Prüfen Sie, ob der gewählte Speichertreiber diese Eigentümersemantik unterstützt. Das Chart erstellt oder löscht den Claim nicht, und ein aktivierter Mount aktiviert nicht das Journal: Das steuert weiterhin die private Serverkonfiguration.

Fügen Sie für `POST /api/requests` neben `action` und `params` das Feld `idempotency_key` hinzu. Ältere Befehls-POSTs akzeptieren stattdessen `Idempotency-Key`. Schlüssel enthalten 16–80 ASCII-Buchstaben, Ziffern, Unterstriche oder Bindestriche; erzeugen Sie für jede Absicht einen neuen zufälligen Schlüssel. Derselbe Eigentümer/Schlüssel mit demselben Gerät, derselben Aktion und derselben normalisierten JSON-Nutzlast liefert die ursprüngliche ID zurück, ohne erneut einzureihen. Eine geänderte Nutzlast führt zu 409. Schlüssel gelten pro Operator-ID (oder Administrator), nicht pro Gerät. Nach Ablauf der Aufbewahrung kann der Schlüssel als neue Absicht akzeptiert werden: Clients dürfen alte Absichten außerhalb dieses Zeitfensters nicht erneut versuchen.

`GET /api/operations?device_id=DEVICE` meldet, ob das Journal aktiviert ist, seine Grenzen und die Befehls-TTL. Ergänzen Sie `&key=KEY`, um eine Operation zu lesen:

- `pending`: akzeptiert; noch keine gespeicherte Antwort.
- `responded`: eine validierte Player-Antwort ist eingetroffen; `result_status` ist `ok`, `rejected` oder `unsupported`. Der ursprüngliche Ergebnisinhalt läuft weiterhin regulär ab.
- `acknowledged`: ein älterer Verbraucher hat den Befehl bestätigt. Das beweist weder sichtbare Wiedergabe noch die Speicherung einer Einstellungsänderung.
- `unknown`: die Frist ist abgelaufen oder der Server wurde vor Speicherung einer Antwort neu gestartet. Der Server **wiederholt diesen Befehl nach einem Neustart niemals**.

Ein wiederholter POST mit Schlüssel liefert `status: retained`, die ursprüngliche ID und den Zustand. Gewöhnliche neue POSTs behalten `status: queued`. Journaleinträge enthalten nur Eigentümer, Gerät, Aktion, zufällige IDs, einen Nutzlast-Digest, Zustand und Fristen. Sie enthalten weder Befehlsnutzlasten noch Anbieter-URLs, Tokens, Screenshots oder Antwortinhalte. Ausstehende Befehle werden nicht persistent gespeichert. Dies ist begrenzte Deduplizierung und Ergebnisverfolgung, keine garantierte genau einmalige Ausführung, dauerhafte Arbeitswarteschlange oder Hochverfügbarkeit mehrerer Server.

## Local/Home planen, anwenden, fortsetzen und prüfen

Das bestehende Verhalten von `ott PLAYER load PRESET` bleibt erhalten. Für den neuen Ablauf:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` arbeitet offline: Es validiert das lokale Preset und listet die Einstellungsplätze sowie das gewünschte aktive Profil auf, ohne deren Inhalte auszugeben. Es untersucht den Player nicht. `verify` führt exakte schreibgeschützte Vergleiche durch und prüft das gewählte M3U-Profil. Bei Abweichungen oder nicht unterstütztem Vergleich gibt es einen von null verschiedenen Status zurück.

`apply` benötigt das Serverjournal. Es erstellt einen privaten Checkpoint und vergleicht vor der ersten Änderung jeden konfigurierten Anbieter. Jeder Speichervorgang vergleicht über `settings_compare` die gespeicherten Bytes mit den erwarteten Werten; bei Übereinstimmung entfällt das Schreiben. Eine abschließende Prüfung umfasst Plex, alle M3U-Plätze, optionale Stalker-Plätze und das gewählte M3U-Profil. Vergleiche liefern einen booleschen Wert, niemals gespeicherte Geheimnisse.

Der Standard-Checkpoint liegt unter `~/.config/ottplay-control/checkpoints/`. Mit `--checkpoint /absolute/private/path/session.json` nach dem Modus wählen Sie eine andere Datei. Checkpoints enthalten Gerät, Preset-Namen, gehashte Server-/Preset-Revision, Namen abgeschlossener Schritte und einen ausstehenden zufälligen Operationsschlüssel mit Frist. Sie enthalten keine Einstellungswerte. Dateien werden nach fsync atomar ersetzt und gegen konkurrierende CLI-Schreiber gesperrt. Halten Sie das passende lokale Preset verfügbar. Unter Unix muss das Checkpoint-Verzeichnis privat sein (0700), mit regulären Dateien im Modus 0600. Beschränken Sie unter Windows die vererbte ACL des Verzeichnisses auf den Benutzer, der die CLI ausführt; Unix-Modusbits erzwingen keine Windows-Zugriffsrechte.

`resume` validiert denselben Server, dasselbe Gerät und dieselbe Preset-Revision und liest dann das ausstehende Ergebnis. Eine gespeicherte Antwort ermöglicht sofortiges erneutes Prüfen. Bei ungewissem Ergebnis werden Änderungen gestoppt, bis die Frist des vorherigen Befehls, das Anfrage-Timeout und ein kleiner Zuschlag vergangen sind. Führen Sie danach `resume` erneut aus. Es prüft gespeicherte Einstellungen auch für abgeschlossene Schritte erneut, damit Benutzeränderungen nicht als erfolgreiche frühere Schreibvorgänge gelten. Ein relativer Befehl wird niemals blind wiederholt. Die absolute Anbieter-/Profilauswahl darf nach der Wartefrist wiederholt werden.

Ein vorhandener Checkpoint wird niemals durch ein neues `apply` überschrieben. Verwenden Sie ihn mit `resume` weiter oder wählen Sie für ein geändertes Preset eine neue Datei. Unterschiedliche Geräte haben eigene Checkpoints. Es gibt keine Transaktion oder Rücknahme über alle Player hinweg; erfolgreiche frühere Schreibvorgänge bleiben bestehen, wenn ein späterer Schritt fehlschlägt. `verified` bestätigt die gespeicherten Werte des Players zum Vergleichszeitpunkt, nicht erfolgreiches Laden über das Netzwerk oder physische Bild-/Tonausgabe.

## Integrationen nur die erforderlichen Befugnisse geben

Verwenden Sie für HA, eine RF-Brücke oder einen Agenten [normale Befehlsoperatoren](security-design.md#scoped-command-operators), statt `admin_token` zu verteilen. Erlauben Sie exakte Geräte-IDs und Operationen wie `input.channel_up` und `input.channel_down`. Einstellungsoperationen benötigen für diesen Preset-Ablauf explizite Berechtigungen für `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` und `provider_settings`. Die Übergabe benötigt `playback_handoff_export` an der Quelle und `playback_handoff_import` am Ziel. Ein Operatoreintrag kombiniert seine aufgeführten Geräte und Aktionen; verwenden Sie getrennte Operatoridentitäten, wenn dieselbe Operation nicht für jedes aufgeführte Gerät gelten darf. Tokens und die Zustimmung zur Diagnose bleiben getrennte Befugnisse.

Operatoren können keine Player-Warteschlangen abfragen, Player-Antworten einreichen, alle Geräte auflisten, Admin-Debugausgaben oder Ergebnisse anderer Operatoren lesen. Eine eingeschränkte Aktionsberechtigung verhindert auch das Lesen ihrer gespeicherten Operation. Das Entfernen eines Operators mit anschließendem Neustart widerruft seine Zugangsdaten. Verwenden Sie eine Operator-ID nicht für einen anderen Akteur, solange alte Journaleinträge bestehen.

## Auf einem anderen Player fortsetzen

```sh
ott handoff living-room bedroom
```

Die Quelle exportiert eine Live-/Archiv-Referenz der Version 1: Anbieter, undurchsichtige Quellenidentität, undurchsichtigen Element-Fingerabdruck `channel-v1:<8 hex>.<8 hex>` und bei Archivwiedergabe die aktuelle Programmzeit. Rohe Anbieter-IDs und Stream-URLs bleiben lokal. Das Ziel muss **dieselbe konfigurierte Quelle/dasselbe Konto** besitzen, ein eindeutig passendes Element im eigenen Katalog auflösen und mit eigenen lokalen Zugangsdaten eine neue URL beziehen. Der Controller kopiert weder eine abspielbare URL noch ändert er die Zugangsdaten des Ziels. Die Quelle spielt weiter. Ein erfolgreiches CLI-Ergebnis bedeutet, dass das Ziel die Anfrage abgeschickt hat, nicht dass Bildschirm oder Lautsprecher physisch geprüft wurden.

Version 1 unterstützt Live-/Archivkataloge von `m3u`, `xtream` und `stalker`, sofern der Player die erforderlichen Identitäten liefern kann. Unbekannte oder mehrdeutige Elemente, nicht passende Quellen und Ablehnung durch lokale Richtlinien schlagen fehl, ohne ersatzweise nach Namen zu suchen. VOD/Serien, die Zuordnung verschiedener Konten, das Pausieren der Quelle und die fortlaufende Synchronisierung von Verlauf/Favoriten gehören nicht zu diesem ersten Vertrag.

## Validierung und Wiederherstellungsübungen

Führen Sie `go test -race ./...`, `go vet ./...` und `python3 -m unittest discover -s tests` aus. Die Tests decken verlorene Antworten nach erfolgreichem Schreiben, exakten Vergleich vor dem Fortsetzen, Konfigurationsabweichungen, Fristen unbekannter Operationen, zwei Schreiber, beschädigte Journale, operatorübergreifende Lesezugriffe und fehlerhafte Übergabenutzlasten ab. Unterbrechen Sie auf einem Staging-Player zusätzlich die CLI nach dem Speichern einer Einstellung, starten Sie den Controller neu, setzen Sie fort und prüfen Sie; testen Sie eine Übergabe zwischen passenden und unpassenden Quellen. Bestätigen Sie Bild und Ton separat.

Führen Sie für die lokale Prüfung über Repository-Grenzen hinweg mit dem aktuellen FOSS-Checkout und installierten Entwicklungsabhängigkeiten Folgendes aus:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Der Smoke-Test baut diesen Go-Server, kompiliert das vollständige FOSS-TypeScript-Projekt und führt dessen echten Transport, Befehlshandler, Quellenidentität und ausgelieferten Playlist-Codec gegen die Python-CLI aus. Er prüft exakte Einstellungen für alle drei unterstützten Anbieter, Abweichungen, Live-Übergaben mit unterschiedlichen lokalen Zeilen-IDs, Suchpositionen im Archiv, fehlgeschlagene URL-Auflösung und strenge Validierung des Übertragungsformats. Nur Speicher, Anbieterrichtlinie und Schnittstellen des Wiedergabe-Hosts sind Test-Fixtures; Browser, physische Medienausgabe oder echter Kontozugriff werden nicht geprüft. Er bindet an Loopback, verwendet private temporäre Dateien, kontaktiert keine Stream-URLs und entfernt seine Dateien sowie den Server beim Beenden.

</div>
