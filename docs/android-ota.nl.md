<div lang="nl" dir="ltr">

# Android-OTA: gewone spelers en de aparte rootagent

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Gebruik voor **k8a/fire8 en andere Capacitor-spelers** de wifi-updater van de normale speler, niet het commando voor de rootagent:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Zonder `--version` wordt de nieuwste gepubliceerde bèta gekozen. Gebruik op Android 7+ `full` in plaats van `api22`. De CLI zoekt de officiële release, controleert het manifest, bereidt de APK voor en peilt de status voordat zij eenmalig installatie aanvraagt. Android kan bevestiging op de tablet vereisen. Schakel vóór onderhoud kiosk uit en ontgrendel beschermde instellingen expliciet; het commando wijzigt dit beleid nooit. Zie de [volledige CLI-procedure](cli.md#capacitor-apk-updates-over-wi-fi).

Dit vereist een eerder geïnstalleerde APK met `AppUpdate`, wifi en een actieve speler die met de controller is verbonden. Gewone volgende updates hebben geen USB nodig, maar kunnen een offline tablet of gestopte WebView niet bereiken. Een eerste installatie, ontbrekende updater, defecte app of netwerkuitval kan nog steeds lokaal herstel vereisen. Dit impliceert geen automatische releaseplanner of algemene stille installer.

De rest van deze handleiding beschrijft een **andere onderhoudsagent voor a1 met root**, beperkt tot de ingerichte Matricom/API19-installatie `play.ott.kitkat`. Deze is op gewone Fire-/Capacitor-apparaten niet al geïnstalleerd of bruikbaar.

Na de eenmalige USB-initialisatie met root kan de onafhankelijke Android-agent ondertekende updates ontvangen via zijn uitgaande HTTPS-verbinding. Hij heeft geen werkende WebView nodig om zichzelf bij te werken. Agent 0.1.2 ondersteunt ook een gecoördineerde bundel van APK en systeemaanraakhelper, met opgeslagen herstelbestanden en verificatie vóór definitieve afronding.

Deze handleiding legt het volledige traject uit. Raadpleeg voor exacte manifestvelden, grenzen en hardware-eisen de [referentie van de native agent](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Wat wordt bijgewerkt

![OTA-architectuur: beoordeelde releasebytes worden ondertekend, via directe HTTPS gepubliceerd, door de onafhankelijke agent geverifieerd en na installatie gecontroleerd. Webuitrol staat apart.](images/ota-architecture.svg)

- **Native agent:** `kind: agent` vervangt zijn eigen ARMv7-binary. Dit werkt ook wanneer OTT uitgeschakeld is en een andere HOME-app wordt gebruikt.
- **Alleen APK:** `kind: apk` vervangt de wrapper met behoud van gegevens. OTT moet ingeschakeld en op de voorgrond zijn. De ingebouwde helper van de kandidaat moet exact overeenkomen met de geïnstalleerde systeemhelper.
- **APK en aanraakhelper:** `kind: bundle` werkt beide componenten als paar bij. Een oudere agent moet eerst een update van alleen de agent krijgen die bundels ondersteunt.
- **Gehoste speler:** plaats het geverifieerde webartefact op de bestaande origin, herlaad daarna een actieve speler en controleer hem. Een APK-update rolt het gehoste JavaScript niet uit. Zie de [releasewerkwijze](release-workflow.md).
- **Controller en CLI:** rol de controller uit en installeer de volledige CLI-moduleset apart. Het bijwerken van een van beide vervangt geen code op de tablet.

Firmware, kernel, systeem-WebView, rootbroker en opstartscripts vallen buiten dit OTA-formaat. De [native referentie](../native/android-agent/README.md#bootstrap) beschrijft grenzen van initialisatie en herstel. Ondersteunde OTA-bewerkingen vereisen geen netwerk-ADB of willekeurige remote shell.

## Van beoordeelde release naar bevestigde installatie

1. **Kies geverifieerde bytes.** Voeg beoordeelde wijzigingen samen nadat CI slaagt. Download het gewenste releaseartefact en controleer bronrevisie, onveranderlijk releasebewijs en controlesom. Pak de binary `agent` uit het native archief; het archief zelf is geen installeerbare agentpayload.
2. **Bereid voor en onderteken.** Gebruik de bestaande private Ed25519-sleutel waarvan de publieke sleutel bij de initialisatie is vastgezet. Houd die sleutel en apparaattoegangsgegevens privé.
3. **Publiceer en controleer transport.** Publiceer alleen payload en `signed.json` op onveranderlijke HTTPS-paden. Beide moeten hun bytes rechtstreeks met HTTP 200 teruggeven; de updater weigert omleidingen. Voer `verify` van het pakketgereedschap uit.
4. **Stuur één update.** Noteer het aanvraag-ID en inspecteer de status na bevestiging. Een verloren of onzeker antwoord is geen reden om een bewerking blind te herhalen.
5. **Bevestig het effect.** Vergelijk nieuw opgevraagde geïnstalleerde hashes en versies met de voorbereide release. Controleer bij een actieve speler ook kioskdoel, wachtrij en voortgaande weergave. Fysiek beeld en geluid vereisen observatie.

Voorbeeld van een agentrelease; vervang de gekozen versie en werkelijke paden:

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

`ENVELOPE_SHA256` is de `envelope_sha256` van het pakketgereedschap, niet de payloadhash. `accepted` betekent toelating, niet geslaagde installatie. Zelfvervanging kan een oud bewerkingsbewijs op `unknown` laten staan; de nieuwe runtime, verwachte agentversie en passende geïnstalleerde hash vormen het installatiebewijs. De [CLI-referentie](cli.md#independent-android-agent) beschrijft onafhankelijke native routering en verwerking van ontvangstbewijzen.

## Hoe de APK-/helpertransactie herstelt

![APK-/helpertransactie: vooraf controleren, vorig paar opslaan, toepassen, verifiëren, afronden; fouten herstellen het vorige paar en onvolledig herstel houdt appwijzigingen geblokkeerd.](images/ota-transaction.svg)

De bundel bevat precies `app.apk` en `ott-touch-guard`. Het pakketgereedschap kan hem rechtstreeks uit de ondertekende wrapper-APK maken met `--kind bundle --apk`. Het haalt de ingebouwde helper eruit, zodat beide geleverde kopieën overeenkomen. Zie het [bundelcommando en de eisen](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Vóór bestandswijzigingen vereist de agent een overeenkomend geïnstalleerd paar, genoeg vrije ruimte, een reagerende OTT-speler op de voorgrond, een alleen-lezen systeempartitie en gemelde APK-downgradeondersteuning. Bij een vergrendelde kiosk controleert hij hetzelfde tv-kanaal-ID of de geordende VPortal-IDs; gelijke wachtrijlengtes alleen zijn onvoldoende. Niet-ondersteunde vergrendelde wachtrijen worden geweigerd. Een bewust uitgeschakelde OTT-app wordt ook geweigerd, waardoor de huidige HOME-app behouden blijft.

Herstelbestanden en de wachtende transactie worden persistent opgeslagen voordat de app stopt. De agent wacht tot app en oude helper gestopt zijn, vervangt de helper atomair, zet `/system` weer op alleen-lezen, installeert de APK en controleert het resultaat. Mislukt toepassen of verifiëren, dan wordt het vorige paar hersteld. Herstel tijdens opstarten herstelt bestanden zonder OTT te starten of HOME over te nemen.

Kan herstel niet worden afgerond, dan is `updates.recovery_failed` waar. Diagnostiek en ondertekende reparatie van alleen de agent blijven beschikbaar; appwijzigingen en de afspeelwaakhond worden geblokkeerd. Verwijder de opgeslagen transactie niet om installatie af te dwingen. Herstel van binaries draait migraties van appgegevens niet terug en firmware kan een downgrade blijven weigeren. Zie de [herstelbeperkingen](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Resultaten en acceptatieregistratie

![Validatiegrafiek: 488 CLI-tests, 576 tests van releasegereedschap en 6 OTA-pakkettests; de referentietablet draait de geverifieerde nieuwe agent, maar fysieke APK-/helperacceptatie is nog niet afgerond.](images/ota-validation.svg)

De implementatieaudit van 14 oktober 2026 slaagde voor 488 CLI-tests (één overgeslagen), 576 tests van releasegereedschap, zes OTA-pakkettests, native Go-race-/statische controles en een ARMv7-build. Die aantallen beschrijven suitegrootte, geen dekking of prestaties. Foutinjectiontests testen ongeldige archieven, onderbroken transacties, gedeeltelijk herstel, doelwijzigingen en herstelblokkades. Onafhankelijke beoordeling vond en corrigeerde ook een onvoldoende controle op wachtrij-identiteit en afwijkende APK-/helpervalidatie tussen host en apparaat.

Gecontroleerde stand op 15 oktober 2026:

- De OTA-implementatie is samengevoegd in [controller-PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) en opgenomen in [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Het huidige en vorige ondertekende agentpakket zijn gepubliceerd op de [OTA-leveringshost](https://android-updates.ottplay.here.now/). Hun payload- en envelopbytes zijn via directe HTTPS succesvol gedownload en vergeleken.
- De referentietablet is via het onafhankelijke OTA-kanaal bijgewerkt van `0.1.1-beta.21` naar `0.1.2-beta.4`. De draaiende agentversie en geïnstalleerde SHA-256 kwamen overeen met de gekozen release; zijn runtime veranderde en het OS-boot-ID bleef gelijk. OTT had nog steeds geen draaiend appproces, en er is geen appstart of HOME-selectie verstuurd.
- Native status meldde de hash van de systeemaanraakhelper, geen wachtende bundel en geen herstelfout. APK-metadata waren niet beschikbaar, dus dit bewijst versie of hash van de geïnstalleerde APK niet.
- De gecoördineerde APK-/helpertransactie is op de host gevalideerd; fysieke installatie, downgradeherstel, beeld en geluid zijn nog niet geaccepteerd op de referentietablet.

Bewaar bij iedere volgende uitrol releasebron en controlesommen, voorbereide envelophash, aanvraag-ID, native status vóór/na en het aparte resultaat van fysieke acceptatie. Een geslaagde download of commandobevestiging vervangt die waarnemingen niet.

</div>
