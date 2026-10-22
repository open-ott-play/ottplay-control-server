# Android-USB-Transportimage

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Dieses Verzeichnis enthält die Bereitstellungsvorlagen und das [`Dockerfile`](Dockerfile) für den Transportcontainer des gemeinsamen Managers. Der Python-Manager läuft auf dem USB-Host. Das Image stellt ADB, Python und Werkzeuge für den atomaren Wartungsadapter bereit; es enthält weder ein zweites Geräteverzeichnis noch einen Wiederherstellungsdienst. Einrichtung, Befehle, Migration, Wiederherstellungsgrenzen und Rücknahme beschreibt das [Betriebshandbuch](../../docs/android-usb-manager.md).

Ohne Git-Checkout laden Sie `ottplay-control-server-deployment.tar.gz` und `release-manifest.json` aus demselben [Release](https://github.com/open-ott-play/ottplay-control-server/releases). Prüfen Sie den Archiv-Digest und entpacken Sie es gemäß [Bereitstellungspaket beziehen](../../docs/android-usb-manager.md#get-the-deployment-package) in ein neues Verzeichnis. Das Paket enthält dieses Rezept und die sechs lokalen/entfernten Skripte unter `tools/`; behalten Sie die Verzeichnisstruktur bei. Ältere Pakete, einschließlich `v0.1.5-beta.6`, enthalten diese Skripte nicht und benötigen stattdessen das zugehörige Quellarchiv. Ein fertig gebautes USB-Transportimage ist nicht enthalten. Das OCI-Release-Artefakt des Control-Servers hat einen anderen Zweck.

## Herkunft von Quellcode und Artefakten

Die Basis ist `python:3.12.14-slim-trixie`, festgelegt auf `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. Basis, APT-Anweisung und hashgebundene Installation von `websocket-client==1.9.0` wurden in [PR 128, Revision b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) geprüft. Der Anweisungstext bleibt erhalten, damit vorhandener Build-Cache wiederverwendet werden kann. Das optionale WebSocket-Paket unterstützt bestehende allgemeine Wartungsclients; die migrierten CDP-Adapter können auch ausschließlich die Python-Standardbibliothek nutzen.

Dieses Rezept entfernt nach Installation des festgelegten Wheels das installierte `pip`-Paket, lässt Manager- und Wiederherstellungscode aus PR 128 weg, startet ADB im Vordergrund und deaktiviert geerbte Healthchecks. Das Entfernen des Installers zur Laufzeit verringert die verfügbare Angriffsfläche; es belegt keinen Schwachstellenscan ohne Befunde.

Das geprüfte Artefakt aus PR 128 war `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Es enthält einen anderen Einstiegspunkt und einen inkompatiblen Heartbeat-Healthcheck. Es ist ein Referenzartefakt, kein Build dieses Dockerfiles. Schreiben Sie dessen Image-ID, Quellherkunft oder Scanergebnisse nicht diesem Rezept zu.

Die APT-Repositorys sind nicht auf Snapshots festgelegt. Ein künftiger sauberer Neubau kann deshalb auch bei identischem Basis-Digest und Python-Wheel-Hash neuere Betriebssystempakete wählen. Dokumentieren Sie bei der Build-Freigabe die genaue Quellrevision, Builder/Plattform, erzeugte Image-ID, Paketversionen und den Scan. Die Produktionskonfiguration muss diese freigegebene unveränderliche Image-ID oder den Digest verwenden.

## Bauen, ohne einen ausgelasteten USB-Host zu stören

Bauen und scannen Sie vorzugsweise auf einem separaten Builder. Der Zielhost benötigt nur das freigegebene Image, keine Paketdownloads oder Build-Werkzeugkette.

Prüfen Sie vor Wiederverwendung eines vorbereiteten lokalen Builders, ob genau das Basisimage vorhanden ist und die APT- sowie Wheel-Installationsschichten im Cache liegen. Nur das endgültige Image aus PR 128 zu laden, belegt keine dieser Voraussetzungen. Ein bewusst netzloser Versuch aus dem Repository-Stamm oder dem entpackten Bereitstellungsverzeichnis lautet:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` gilt für Build-Schritte; es garantiert nicht generell, dass der Builder bei der Basisauflösung nie eine Registry kontaktiert. Bestätigen Sie die lokale Basisauflösung, bevor Sie dieses Verfahren auf einem eingeschränkten Host einsetzen. Fehlt eine APT- oder Wheel-Schicht, brechen Sie ab, statt stillschweigend einen Neubau über das Netzwerk auf dem USB-Host zuzulassen.

BuildKit nimmt den Netzwerkmodus eines Build-Schritts in dessen Cache-Schlüssel auf. Ein im Standardnetzwerkmodus erzeugter Cache kann beim obigen Offlineversuch daher verfehlt werden. Verwenden Sie ihn erst nach Prüfung der exakten Basis und Anweisungskette erneut und stoppen Sie, falls einer der Paketinstallationsschritte ausgeführt wird, statt `CACHED` zu melden. Das abschließende Entfernen von `pip` verwendet ausdrücklich `RUN --network=none`; es benötigt auch dann kein Netzwerk, wenn frühere Cache-Schritte ihren ursprünglichen Modus behalten.

Vor Änderung der Produktionskonfiguration prüfen Sie Python- und ADB-Versionen, scannen das exakt erzeugte Image, verifizieren Binärdateiübertragung und APK-Bereitstellung und führen anschließend die Abnahmeprüfungen des Handbuchs für Geräteidentität, Schlüsselautorisierung, Ruhezustand und Start aus. ADB 34 muss mit einem älteren Android-Gerät getestet werden; allein die Versionsnummer belegt keine KitKat- oder Fire-Kompatibilität. Bewahren Sie das aktuelle Image und die private Bereitstellungssicherung auf, bis eine Rücknahme nicht mehr benötigt wird.

## Laufzeitbudget

Der Host-Manager, nicht dieses Dockerfile, setzt 256 MiB Arbeitsspeicher, 256 MiB für Arbeitsspeicher plus Swap und CPU-Shares 128 ohne harte CPU- oder PID-Quote. Er erzwingt `--no-healthcheck`, `--network none`, ein schreibgeschütztes Root-Dateisystem, entfernte Capabilities und genaue USB-Geräteknotenzuordnungen. `/tmp` ist ein begrenztes tmpfs und verbraucht dasselbe Speicherbudget. Der Staging-Adapter schreibt in 1-MiB-Blöcken und begrenzt einzelne Nutzlasten auf 128 MiB; alle Operationen teilen sich die Manager-Sperre.

Docker dokumentiert den Unterschied zwischen [Speicher-, Swap- und CPU-Share-Steuerung](https://docs.docker.com/engine/containers/resource_constraints/) und der [Option `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
