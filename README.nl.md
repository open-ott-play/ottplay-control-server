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

[Documentatie / Wiki](docs/wiki/Home.nl.md)

Een kleine server voor eigen hosting die opdrachten op afstand aan OTT-play-spelers aflevert. Spelers versturen uitgaande HTTP-verzoeken, zodat een tv geen luisterende poort hoeft open te stellen. Elke speler heeft een eigen wachtrij en toegangstoken; opdrachten worden met een apart beheerderstoken ingediend.

Optionele [betrouwbare bediening](docs/reliable-control.md) voegt integratiegegevens met beperkte bevoegdheden, begrensde duurzame opslag van opdrachtresultaten, hervatbare toepassing van Local/Home-instellingen met exacte verificatie en expliciete overdracht tussen live- en archiefweergave toe. Hiervoor zijn controller- en CLI-revisies nodig die deze handleiding bevatten. Exacte verificatie en overdracht vereisen ook een bijgewerkte speler. De oudere vastgezette installatierevisie hieronder bevat deze functies niet. Het [architectuurdiagram](docs/assets/reliable-control.svg) toont waar instellingen, toegangsgegevens en de toestand van bewerkingen blijven.

De server draait als één draagbaar Go-programmabestand, met een DNS-bibliotheek met vaste versie voor optionele servicedetectie. Releasepakketten zijn beschikbaar voor Linux amd64, arm64 en ARMv7; macOS Intel en Apple Silicon; en Windows amd64 en arm64. Containerimages ondersteunen Linux amd64 en arm64. Kubernetes en k3s gebruiken de meegeleverde Helm-chart.

## Een server starten

Download het juiste archief bij [Releases](https://github.com/open-ott-play/ottplay-control-server/releases), controleer het met het releasemanifest en pak het uit. Gebruik op Windows `ottplay-control-server.exe` in de onderstaande opdrachten.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` maakt willekeurige, verschillende toegangsgegevens aan in een nieuw bestand dat op Unix alleen voor de eigenaar toegankelijk is. Het weigert bestaande bestanden of symbolische koppelingen te overschrijven en drukt de toegangsgegevens nooit af. Houd het bestand privé; beperk op Windows de ACL tot het account dat de server uitvoert. `config.example.json` beschrijft de velden en bevat bewust ongeldige voorbeeldtokens.

Het standaardadres is `0.0.0.0:8081` (alle IPv4-interfaces, inclusief LAN). Verbind tv's via het LAN-adres en de poort van de server. Bestaande configuraties behouden hun expliciete `listen`-waarde; wijzig die in `0.0.0.0:8081` of gebruik `--listen 0.0.0.0:8081` om een installatie die voorheen alleen via loopback bereikbaar was open te stellen. Voeg de **exacte pagina-origin** van de speler toe aan `allowed_origins`, bijvoorbeeld `http://192.168.1.20:8443`. Schema, hostnaam en poort moeten overeenkomen; voeg geen pad toe. Een lege lijst origins staat alleen clients buiten browsers toe. Configuratiewijzigingen worden actief na herstart. Toegangsgegevens voor apparaat en beheerder blijven verplicht.

HTTP wordt ondersteund voor oudere tv-browsers op een vertrouwd LAN. Gebruik HTTPS wanneer de verbinding een niet-vertrouwd netwerk passeert: `serve --config config.json --tls-cert server.crt --tls-key server.key`, of beëindig TLS op een reverse proxy. Via HTTPS geladen spelerpagina's kunnen in een browser geen verbinding maken met een gewone HTTP-server. Native apps behouden ook het transportbeleid van hun besturingssysteem; deze instelling versoepelt dat niet.

Direct HTTPS controleert de aangeleverde certificaatketen voordat de luisterpoort wordt geopend: RSA-sleutels moeten minimaal 2048 bits en ECDSA-sleutels minimaal 224 bits zijn; ook Ed25519 wordt ondersteund. Vervang een te zwak certificaat vóór het upgraden. Zie het [TLS-certificaatbeleid en het toepassingsgebied](docs/security-design.md#direct-https-certificate-policy).

## De speler verbinden

Gebruik een spelerversie met ondersteuning voor de verbinding met de opdrachtserver. Open **Instellingen → Afstandsbediening** en vul in:

1. **Serveradres:** het IP-adres of de hostnaam van de server, eventueel met poort, of een volledig `http://`- / `https://`-adres. Een host zonder schema gebruikt standaard HTTP-poort 8081.
2. **Toegangscode:** het `token` van de vermelding van deze speler in `devices`, nooit `admin_token`.
3. **Verbinden:** schakel polling in en controleer de status in hetzelfde dialoogvenster.

De speler bewaart deze verbinding alleen in de huidige installatie en sluit haar uit van instellingenback-ups en cloudoverdracht. De aparte lokale HTTP-listener is niet nodig. Webbrowsers vereisen een toegestane CORS-origin; verpakte tv-pagina's met `Origin: null` kunnen de expliciete instelling `allow_null_origin` gebruiken voor aangewezen apparaat-/bootstraproutes. Dit activeert geen routes voor beheerders of diagnoseoperators.

## Afstandsbediening via de terminal

De [geïllustreerde Android-OTA-handleiding](docs/android-ota.md) maakt onderscheid tussen gewone Capacitor-updates via wifi (`ott DEVICE update --variant api22|full`, standaard de nieuwste bèta; `--version` kiest een release) en de aparte agent met roottoegang. Ze beschrijft ondertekende agent- en APK/helperupdates, herstel en verificatie van het geïnstalleerde resultaat.

De [Android-ADB-handleiding](docs/android-adb.md) behandelt USB-toegang via een werkstation of Synology, scherm- en toetsenbordbediening, schermafbeeldingen, logs, APK-installatie en normaal herstarten, inclusief beperkingen op oudere tablets.

Met optionele [DNS-SD-detectie en koppeling](docs/discovery.md) kunnen spelers de controller via hun netwerkdomein vinden en na `ott approve NAME CODE` een individueel apparaattoken ontvangen. DNS bevat alleen servicemetadata. Bestaande handmatige verbindingen blijven werken zonder detectie in te schakelen.

Gebruik de Python-CLI voor speleraliassen, actueel volume, kanaalzoeken, actuele EPG, providerkeuze en ondersteunde providerconfiguratie: [CLI-handleiding](docs/cli.md). Zowel server als speler moeten de verzoek-/antwoorduitbreiding bevatten.

De Python-CLI wordt uit een werkkopie van de broncode geïnstalleerd, los van het native serverarchief. De [installatie- en configuratiestappen](docs/cli.md#installation-and-connection) behandelen macOS/Linux en Windows, Python/PATH en de vereiste CLI-modules. Ze zetten een beoordeelde ontwikkelcommit van 0.1.1 vast met workbench en offlineverificatie; de stabiele CLI v0.1.0 en native serverarchieven bevatten die opdrachten niet. [Werk de CLI bij of zet haar terug](docs/cli.md#update-select-or-roll-back-the-cli) zonder een bestaande werkkopie, launcher of privéconfiguratie te vervangen. Lokale launchers kunnen de alleen-lezen [playlistresolver](docs/cli.md#local-playlist-resolver) gebruiken om zonder controller kanalen, actuele programma's en afspeelbare archieven te zoeken. Na installatie en configuratie:

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

`a1` is een voorbeeldalias voor een geregistreerde speler, geen computerhostnaam. Zie [registratie en koppeling](docs/cli.md#register-and-connect-a-player), [alle opdrachten met voorbeelden](docs/cli.md#commands), [verbinding verbreken en toegang intrekken](docs/cli.md#disconnect-deregister-or-revoke-a-player) en [probleemoplossing](docs/cli.md#troubleshooting).

Gebruik voor een altijd ingeschakelde speler [standby en go](docs/cli.md#stop-playback-while-keeping-the-player-online) om weergave te stoppen en later te hervatten zonder de app af te sluiten. `stop` blijft een alias voor `standby`; `start` en `wake` blijven aliassen voor `go`. De afstandsbediening moet verbonden blijven, dus stand-by belooft geen afwezigheid van netwerkverkeer. `ott a1` toont de bewerkingen die de speler aanbiedt. Een geaccepteerde opdracht bevestigt niet dat uitschakelen of starten van de weergave voltooid is.

[Opdrachten voor de beeldverhouding](docs/cli.md#aspect-ratio-fit-and-fill) vereisen een bijgewerkte CLI, controller en een speler die de mogelijkheid `aspect` aanbiedt. Een wijziging wordt na bevestiging toegepast; lees `aspect` opnieuw om de modus en het opslaan ervan te controleren. Fill snijdt proportioneel bij in plaats van het beeld uit te rekken.

[Schermopnamen](docs/cli.md#remote-screenshots) op afstand vereisen een bijgewerkte CLI, controller en een speler met een ondersteunde opnameadapter. Het inschakelen van de afstandsbedieningsverbinding geeft die controller toestemming voor schermopnamen en diagnostiek; native schermopnamen vereisen geen aparte toestemmingsschakelaar. Browsers vereisen nog steeds hun lokale bronkiezer. Afbeeldingen kunnen spelerinstellingen, pincodevensters en zichtbare toegangsgegevens bevatten; verbind daarom alleen met een vertrouwde controller. `ott a1` meldt beschikbaarheid en eventueel vereiste bronselectie. Schermopnamen staan los van gestructureerde telemetrieverzameling.

Om alle VPortal-video's die bij een titel passen te schudden en herhaald af te spelen:

```sh
ott l vpr "wedding"
```

`vpr` schudt de volledige selectie eenmaal en herhaalt daarna die wachtrij. Voer de opdracht opnieuw uit voor een nieuwe volgorde. `vp` speelt overeenkomsten in catalogusvolgorde af; `vp --list` toont ze zonder de weergave te wijzigen. Zie [VPortal-opdrachten](docs/cli.md#commands).

Voor een expliciete Plex-volgorde met de opgeslagen Plex-configuratie van de speler:

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

De CLI wacht standaard maximaal 45 seconden. `ott -t 45 l plex shuffle "Tom and Jerry"` maakt diezelfde antwoordtime-out expliciet; het beperkt niet hoe lang de video's worden afgespeeld. Het verzamelen van grote Plex-bibliotheken kan langer duren dan een korte time-out van 10–15 seconden. Plaats `-t SECONDS` (of `--timeout SECONDS`) vóór de speleralias. Zie [time-outs voor Plex-bibliotheken](docs/cli.md#plex-libraries-by-name) voor voorbeelden van voorvertoningen en wat te doen als een afspeelverzoek verloopt.

Plex-wachtrijen vereisen een CLI, controller en speler met `plex_queue`. `plex play --shuffle ID...` schudt de opgegeven ID's eenmaal. Wachtrijen stoppen na het laatste item, tenzij bestaand kioskbeleid ze herhaalt. Zolang een wachtrij bestaat, werken de gewone `next` en `prev` daarop; `plex stop` wist de wachtrij. Een voorvertoning wisselt nooit van provider en vervangt nooit de huidige weergave. Zie [Plex-wachtrijopdrachten](docs/cli.md#ordered-plex-queues) voor grenzen en gereedheid. Bibliotheekzoeken vereist daarnaast een bijgewerkte speler en controller met `plex_library`; het gebruikt de opgeslagen Plex-verbinding van de speler en weigert dubbelzinnige of onvolledige selecties. Zie [Plex-bibliotheken op naam](docs/cli.md#plex-libraries-by-name).

### Profielen voor lokale video's opslaan

Met een actuele speler en controller gebruiken lokale bestanden/mappen de provider `offline`:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Koppel bestanden/mappen eerst op de speler. Opslaan legt hun configuratie en de geselecteerde video vast zonder de weergave te starten. Exporteren draagt alleen beschrijvingen over; media en toegangsrechten van besturingssysteem/browser blijven op het apparaat. Een andere installatie vereist lokale koppeling, en laden betekent niet dat weergave bevestigd is. Zie [profielen voor lokale video's](docs/cli.md#local-videos-profiles) voor installatie, mogelijkheden, grenzen van het draagbare formaat en probleemoplossing.

### Het huidige item over profielen heen bewaren

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Favorieten blijven op de doelspeler bewaard bij normale herstarts. Een favoriet kiezen laadt de opgeslagen provider/het profiel en bepaalt een nieuwe link; een gewijzigde bron of ontbrekend item leidt tot een fout zonder vervanging door een ander kanaal of andere film. CLI, controller en speler moeten `favorites` ondersteunen; `ott l` toont beschikbare bewerkingen. Zie [favorieten over profielen heen](docs/cli.md#favorites-across-profiles) voor aliassen, grenzen, bevestigingssemantiek en probleemoplossing.

### Een opgeslagen configuratie laden

Bewaar benoemde configuraties in de privésectie `presets` van `~/.config/ottplay-control/cli.json`. Elke configuratie bevat genummerde M3U-profielen (playlist en archiefuren), Plex-toegangsgegevens en het M3U-profiel dat na het laden geselecteerd moet worden. Zie het [configuratievoorbeeld](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

De CLI valideert eerst de volledige configuratie en wacht daarna vóór elke volgende wijziging op bevestiging van de speler. Ze regelt het laden van de provider, bewaart Plex en de M3U-slots en selecteert het ingestelde M3U-profiel. Een fout stopt de reeks en meldt de bevestigde stappen; een schrijfactie met onzekere uitkomst wordt nooit automatisch herhaald. Een extra profielservice of controllerherstart is niet nodig. Houd deze configuratie privé: ze bevat providertoegangsgegevens.

## Een opdracht versturen

Dit voorbeeld leest het beheerderstoken uit de lokale privéconfiguratie zonder het in de URL of opdrachtregelargumenten te zetten:

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

Andere opdrachten kiezen een kanaal op nummer of naam, selecteren een willekeurig kanaal, wisselen providers, tonen een bericht, wijzigen een M3U-playlist waar ondersteund of sluiten de speler af. Beschikbaarheid hangt af van het spelerplatform en de huidige provider. Zie het [API-contract](docs/api.md).

Bij een speler die de zelfstandige VPortal-provider ondersteunt, selecteert `ott a1 provider
vportal` diens eigen 15 profielen. Gebruik `profile N vportal LINK`, `profile N
name NAME` of een privébestand `profile-config N FILE` met `name` en `vportal`, en selecteer vervolgens het ingestelde slot met `profile N`. Bestaande M3U-profielen blijven apart. `profiles` meldt metadata zonder accountlinks of sleutels te tonen.

Na het starten van een VPortal-film of afleveringenwachtrij vergrendelt `ott a1 kiosk on` die selectie. De speler bewaart wachtrij en positie bij herladen en bepaalt media-URL's opnieuw voor herstel. Gebruik `kiosk off` om een andere titel te kiezen of profielen te wijzigen, en vervolgens `kiosk on` om te vergrendelen. Het kioskantwoord toont titel en aantal afleveringen; providerverzoeken en stream-URL's blijven op de speler.

Gebruik `ott a1 kiosk on --strict` voor kinderen: lokaal aanraken toont de videovoetbalk. Bijgewerkte core-spelers laten de tijdlijn aantikken of verslepen om binnen de huidige VPortal-/Plex-aflevering te zoeken; andere lokale bediening blijft vergrendeld. Oudere spelers, kanaalkiosken en wrappers die tijdlijngebaren onderscheppen behouden een alleen-lezen voetbalk. Een bestaande vergrendeling behoudt haar doel. Hef haar vanaf de controller op met `ott a1 kiosk off`. Op Android-websites vereist bescherming tegen afsluiten via het systeem apart vastzetten van de app met een pincode; zie [kioskmodus](docs/cli.md#kiosk-mode).

## Diagnose op afstand

Begin met de [alleen-lezen workbench-snelstart](docs/workbench.md#quick-start) voor `doctor`, UI-/media-`inspect`, opzoeken van operatiebevestigingen, bewijspakketten, `health`, web/native `media-progress` en offline `report verify`. De bestaande beheerderconfiguratie van de CLI en speleraliassen worden gebruikt. Het native traject is voor de afzonderlijk ingerichte Android-onderhoudsagent; Tauri-spelers gebruiken webinspectie. Rapporten onderscheiden decodervoortgang van fysieke controle van scherm/geluid. Zie [workbench-probleemoplossing](docs/workbench.md#troubleshooting) voor ontbrekende mogelijkheden, niet-beschikbare trajecten en onvolledig bewijs.

Optionele [protocol-2-diagnostiek](docs/remote-diagnostics.md) biedt expliciet ingeschakelde sessies per runtime, met aparte operatorgegevens met beperkte bevoegdheden en begrensde gestructureerde telemetrie. De ingeschakelde spelerverbinding verleent lokale toestemming zonder nieuwe vertrouwensvraag; elke opname behoudt een begrensde sessieduur. Polling voor diagnosebesturing staat los van de bestaande opdrachtwachtrij.

De [handleiding voor diagnose-CLI en MCP](docs/diagnostics-cli.md) behandelt installatie, operatorgegevens, verbinding, elke diagnoseopdracht/-tool, opname- en herstelvoorbeelden, runtime-intrekking en probleemoplossing. `ott diagnostics --help` toont deze aparte opdrachtfamilie; gewone speleraliassen en beheerdergegevens geven geen diagnosetoegang.

## Implementatiepakketten

- [Android-tablet: strikte Plex-kiosk en opstarten](docs/android-kiosk-setup.md)
- [Gedeeld Android-USB-beheer en opstartherstel](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS en Windows](docs/deployment.md)
- [Configuratie, afleversemantiek en grenzen](docs/api.md)
- [Release- en validatieproces](docs/development.md)

Wachtrijen zijn begrensd, verlopen standaard na 60 seconden en bevinden zich in het geheugen. Herstart van de server verwijdert wachtende opdrachten. Gebruik één replica; onafhankelijke replica's delen geen wachtrijen. Een speler bevestigt een afleverpoging, niet de daadwerkelijke weergave of het hardwarevolume. Netwerkherhalingen worden binnen een actieve spelersessie gededupliceerd, niet over een appherstart heen.

Deze server volgt de opdracht-API van het optionele voorbeeld `local_proxy.py` in [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), met aparte beheerder-/apparaatgegevens en bevestigde aflevering. De oude proxy blijft een afzonderlijk onderdeel. Dit project fungeert niet als mediaproxy en verandert het HLS-, EPG- of providernetwerkverkeer van de speler niet.

## Licentie

MIT. Naamsvermeldingen voor meegeleverde releasetools staan in `NOTICE` en `LICENSES/`.

## Projectonderhoud

Zie [bijdrage- en testvereisten](CONTRIBUTING.md), het [beleid voor beveiligingsmeldingen](SECURITY.md), het [beveiligingsontwerp](docs/security-design.md) en de [OpenSSF-bewijzen en resterende criteria](docs/openssf-evidence.md).

Plex-wachtrijen accepteren op actuele spelers maximaal 500 ID's. Gebruik `ott f10 plex play --shuffle ID...` om eenmaal te schudden, daarna `ott f10 kiosk on --strict` met de Plex-provider geselecteerd om de wachtrij te herhalen en vergrendelen. `ott f10 kiosk off` heft de vergrendeling op. Oudere spelers behouden hun gemelde wachtrijlimiet.

## Fuzzing van release candidates

Publieke release candidates voeren begrensde native fuzzing uit op de invoergrenzen uit de [fuzzing-handleiding](docs/fuzzing.md). Mutatiecampagnes draaien niet op PR's, bèta's of nachtelijke builds. De handleiding behandelt bereik, budgetten, bewijs en reproductie van regressies.

## Gemeenschap en beveiligingsbewijs

Zie [bijdragen](CONTRIBUTING.md), [bestuur](GOVERNANCE.md), de [gedragscode](CODE_OF_CONDUCT.md), de [roadmap](ROADMAP.md) en [OpenSSF-bewijs](docs/openssf-evidence.md). De bewijsindex onderscheidt ondersteunde beveiligingsprofielen, daadwerkelijke testresultaten en resterende Silver-vereisten.
