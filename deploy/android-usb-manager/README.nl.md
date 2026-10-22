# Android-USB-transportimage

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Deze map bevat de uitrolsjablonen en het [`Dockerfile`](Dockerfile) voor de transportcontainer van de gedeelde manager. De Python-manager draait op de USB-host. Het image levert ADB, Python en gereedschap voor de atomaire onderhoudsadapter; het bevat geen tweede apparatenregister of hersteldaemon. Zie de [beheerdershandleiding](../../docs/android-usb-manager.md) voor inrichting, opdrachten, migratie, herstelbeperkingen en terugdraaien.

Download zonder Git-checkout `ottplay-control-server-deployment.tar.gz` en `release-manifest.json` van dezelfde [release](https://github.com/open-ott-play/ottplay-control-server/releases). Controleer de archiefdigest en pak het uit in een nieuwe map volgens [Het uitrolpakket ophalen](../../docs/android-usb-manager.md#get-the-deployment-package). Het pakket bevat dit recept en de zes host-/externe scripts onder `tools/`; behoud de mappenstructuur. Oudere pakketten, waaronder `v0.1.5-beta.6`, missen die scripts en vereisen het bijpassende bronarchief. Het pakket bevat geen gebouwd USB-transportimage. Het OCI-releasebestand van de control-server heeft een ander doel.

## Herkomst van broncode en artefacten

De basis is `python:3.12.14-slim-trixie`, vastgezet op `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. De basis, APT-instructie en op hash vastgezette installatie van `websocket-client==1.9.0` zijn beoordeeld in [PR 128, revisie b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). De instructietekst blijft behouden voor hergebruik van beschikbare bouwcache. Het optionele WebSocket-pakket ondersteunt bestaande algemene onderhoudsclients; de gemigreerde CDP-adapters kunnen ook alleen de Python-standaardbibliotheek gebruiken.

Dit recept verwijdert het geïnstalleerde `pip`-pakket na installatie van het vastgezette wheel, laat beheer- en herstelcode uit PR 128 weg, start ADB op de voorgrond en schakelt overgenomen gezondheidscontroles uit. Het verwijderen van de installatievoorziening tijdens uitvoering verkleint het beschikbare aanvalsoppervlak; het bewijst geen kwetsbaarheidsscan zonder bevindingen.

Het beoordeelde artefact uit PR 128 was `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Het bevat een ander entrypoint en een onverenigbare heartbeat-controle. Het is een referentieartefact, geen build van dit Dockerfile. Schrijf de image-ID, bronherkomst of scanresultaten ervan niet toe aan dit recept.

APT-repositories zijn niet vastgezet op snapshots. Een latere schone herbouw kan dus nieuwere systeempakketten kiezen, zelfs met dezelfde basisdigest en Python-wheelhash. Leg bij kwalificatie de exacte bronrevisie, bouwer/platform, resulterende image-ID, pakketversies en scan vast. Productieconfiguratie moet die gekwalificeerde onveranderlijke image-ID of digest gebruiken.

## Bouwen zonder een drukke USB-host te verstoren

Bouw en scan bij voorkeur op een afzonderlijke bouwer. De doelhost heeft alleen het gekwalificeerde image nodig, geen pakketdownloads of bouwgereedschap.

Controleer vóór hergebruik van een voorbereide lokale bouwer dat het exacte basisimage beschikbaar is en dat de APT- en wheelinstallatielagen in de cache staan. Alleen het uiteindelijke PR 128-image laden bewijst geen van beide voorwaarden. Vanuit de hoofdmap van de repository of de uitgepakte uitrolmap is een bewust offline poging:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` geldt voor bouwstappen; het garandeert niet algemeen dat een bouwer nooit een registry benadert bij het bepalen van de basis. Bevestig de lokale basisresolutie voordat u deze procedure op een beperkte host gebruikt. Ontbreekt een APT- of wheellaag, stop dan de poging in plaats van stilzwijgend een netwerkherbouw op de USB-host toe te staan.

BuildKit neemt de netwerkmodus van de bouwstap op in de cachesleutel. Een cache uit de standaardnetwerkmodus kan daarom bij bovenstaande offline poging missen. Hergebruik die cache pas na controle van de exacte basis en instructieketen; stop zodra een pakketinstallatiestap wordt uitgevoerd in plaats van `CACHED` te melden. De uiteindelijke verwijdering van `pip` gebruikt expliciet `RUN --network=none`; daarvoor is geen netwerk nodig, ook als eerdere gecachte stappen hun oorspronkelijke modus gebruiken.

Controleer vóór wijziging van productieconfiguratie de Python- en ADB-versies, scan het exact resulterende image, controleer binaire bestandsoverdracht en APK-voorbereiding en voer vervolgens de acceptatiecontroles voor apparaatidentiteit, sleutelautorisatie, slaap en opstarten uit de handleiding uit. ADB 34 moet met een ouder Android-apparaat worden getest; het versienummer alleen bewijst geen KitKat- of Fire-compatibiliteit. Bewaar het huidige image en de privéback-up van de uitrol totdat terugdraaien niet meer nodig is.

## Uitvoeringsbudget

De hostmanager, niet dit Dockerfile, stelt 256 MiB geheugen, 256 MiB geheugen plus swap en CPU-shares 128 in, zonder harde CPU- of PID-quota. Hij dwingt `--no-healthcheck`, `--network none`, een alleen-lezen rootbestandssysteem, verwijderde capabilities en exacte USB-knooppuntkoppelingen af. `/tmp` is een begrensd tmpfs dat hetzelfde geheugenbudget gebruikt. De stagingadapter schrijft in blokken van 1 MiB en beperkt één payload tot 128 MiB; bewerkingen delen het managerslot.

Docker beschrijft het verschil tussen [geheugen-, swap- en CPU-share-instellingen](https://docs.docker.com/engine/containers/resource_constraints/) en de [optie `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
