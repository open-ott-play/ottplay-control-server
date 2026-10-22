<div lang="nl" dir="ltr">

# Betrouwbare bediening en overdracht van afspelen

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Documentatie-index](wiki/Home.md)

Deze functies vereisen de controller en CLI uit een revisie die deze handleiding bevat. Exacte vergelijking van instellingen en overdracht vereisen ook een bijgewerkte speler. Oudere releasearchieven en de historische CLI-revisie in de installatiehandleiding krijgen deze functies niet automatisch. Installeer **alle bestanden `cli/*.py` samen**, inclusief `preset_session.py`; behoud de privéconfiguratie.

![Stroom van bediening, checkpoint en overdracht](assets/reliable-control.svg)

## Begrensde, blijvend opgeslagen resultaten inschakelen

Voeg dit optionele blok toe aan de privéconfiguratie van de server, valideer deze en herstart de server. Kies een absoluut pad op een lokaal persistent bestandssysteem waarop het serviceaccount mag schrijven. Op Unix moet de map privé zijn (0700); bestaande journaal- en vergrendelingsbestanden moeten gewone privébestanden zijn (0600). Beperk op Windows de ACL van de map tot het serviceaccount. Eén proces is eigenaar van een journaal.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

De server maakt ontbrekende mappen met modus 0700. De grenzen zijn 1–4096 records, 60–86400 seconden en 4 MiB op schijf. De bewaartermijn moet `command_ttl_seconds` omvatten. Een vol journaal weigert nieuwe bewerkingen met sleutel in plaats van nog geldige records te verdringen. Beschadigde bestanden, een onveilige Unix-map, een andere schrijver of een mislukte duurzame schrijfactie leiden tot veilige weigering. Het bestaande commandopad in het geheugen blijft de standaard.

De meegeleverde systemd-unit maakt `/var/lib/ottplay-control` met modus 0700. Maak voor Compose een hostmap die eigendom is van UID 65532, stel `CONTROL_SERVER_STATE` in op het absolute pad en combineer `deploy/compose.yml` met `deploy/compose.journal.yml`. Het laatste bestand voegt alleen de beschrijfbare statusmount toe; het rootbestandssysteem blijft alleen-lezen. Voorzie voor Helm apart een persistent-volumeclaim en stel `journalExistingClaim=CLAIM_NAME` in. De claim wordt gekoppeld op `/var/lib/ottplay-control`; gebruik de hierboven getoonde privémapping. De chart behoudt één replica, `Recreate` en `fsGroupChangePolicy: OnRootMismatch`, zodat opnieuw koppelen de rechten van reeds gemaakte privémappen niet recursief verruimt. Controleer of het gekozen opslagstuurprogramma deze eigendomssemantiek ondersteunt. De chart maakt of verwijdert de claim niet, en een mount inschakelen schakelt het journaal niet in: dat blijft de privéconfiguratie van de server bepalen.

Voeg voor `POST /api/requests` het veld `idempotency_key` toe naast `action` en `params`. Oudere commando-POSTs accepteren in plaats daarvan `Idempotency-Key`. Sleutels bevatten 16–80 ASCII-letters, cijfers, underscores of koppeltekens; genereer voor iedere intentie een nieuwe willekeurige sleutel. Dezelfde eigenaar/sleutel met hetzelfde apparaat, dezelfde actie en dezelfde genormaliseerde JSON-inhoud geeft het oorspronkelijke ID terug zonder opnieuw in de wachtrij te plaatsen. Veranderde inhoud geeft 409. Sleutels zijn begrensd per operator-ID (of beheerder), niet per apparaat. Na de bewaartermijn kan de sleutel als nieuwe intentie worden geaccepteerd: clients mogen een oude intentie buiten die termijn niet opnieuw proberen.

`GET /api/operations?device_id=DEVICE` meldt of het journaal is ingeschakeld, de grenzen en de commando-TTL. Voeg `&key=KEY` toe om een bewerking te lezen:

- `pending`: geaccepteerd; nog geen bewaard antwoord.
- `responded`: er is een gevalideerd spelerantwoord binnengekomen; `result_status` is `ok`, `rejected` of `unsupported`. De oorspronkelijke resultaatinhoud verloopt nog steeds normaal.
- `acknowledged`: een oudere afnemer heeft het commando bevestigd. Dit bewijst geen zichtbare weergave of opgeslagen instellingswijziging.
- `unknown`: de termijn verstreek of de server herstartte voordat een antwoord was bewaard. De server **herhaalt dit commando nooit** na een herstart.

Een herhaalde POST met sleutel geeft `status: retained`, het oorspronkelijke ID en de status terug. Gewone nieuwe POSTs behouden `status: queued`. Journaalrecords bevatten alleen eigenaar, apparaat, actie, willekeurige IDs, een digest van de inhoud, status en termijnen. Ze bevatten geen commando-inhoud, provider-URLs, tokens, schermafbeeldingen of antwoordinhoud. Wachtende commandos worden niet persistent opgeslagen. Dit is begrensde deduplicatie en resultaatregistratie, geen gegarandeerde precies-eenmaal-uitvoering, duurzame werkwachtrij of hoge beschikbaarheid over meerdere servers.

## Local/Home plannen, toepassen, hervatten en verifiëren

Het bestaande gedrag van `ott PLAYER load PRESET` blijft behouden. Voor de nieuwe werkwijze:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` werkt offline: het valideert de lokale preset en toont de instellingsslots en het gewenste actieve profiel zonder hun inhoud af te drukken. Het inspecteert de speler niet. `verify` voert exacte alleen-lezenvergelijkingen uit en controleert het gekozen M3U-profiel. Het geeft een niet-nulstatus terug als iets afwijkt of vergelijken niet wordt ondersteund.

`apply` vereist het serverjournaal. Het maakt een privécheckpoint en vergelijkt iedere geconfigureerde provider vóór de eerste wijziging. Iedere opslagactie vergelijkt via `settings_compare` de opgeslagen bytes met de verwachte waarden; bij overeenkomst wordt schrijven overgeslagen. Een eindcontrole omvat Plex, alle M3U-slots, optionele Stalker-slots en het gekozen M3U-profiel. Vergelijkingen geven een booleaanse waarde terug, nooit opgeslagen geheimen.

Het standaardcheckpoint staat onder `~/.config/ottplay-control/checkpoints/`. Gebruik `--checkpoint /absolute/private/path/session.json` na de modus om een ander bestand te kiezen. Checkpoints bevatten apparaat, presetnaam, gehashte server-/presetrevisie, namen van voltooide stappen en één wachtende willekeurige bewerkingssleutel met termijn. Ze bevatten geen instellingswaarden. Bestanden worden na fsync atomair vervangen en vergrendeld tegen gelijktijdige CLI-schrijvers. Houd de bijbehorende lokale preset beschikbaar. Op Unix moet de checkpointmap privé zijn (0700), met gewone bestanden in 0600. Beperk op Windows de overgeërfde map-ACL tot de gebruiker die de CLI uitvoert; Unix-modusbits dwingen geen Windows-toegangsrechten af.

`resume` valideert dezelfde server, hetzelfde apparaat en dezelfde presetrevisie en leest daarna het wachtende resultaat. Een bewaard antwoord maakt onmiddellijke hercontrole mogelijk. Een onzeker resultaat stopt wijzigingen totdat de vorige commandotermijn, de aanvraagtijdslimiet en een kleine marge verstreken zijn. Voer daarna `resume` opnieuw uit. Het controleert opgeslagen instellingen opnieuw, ook voor voltooide stappen, zodat gebruikerswijzigingen niet als geslaagde eerdere schrijfacties worden gezien. Het herhaalt nooit blind een relatief commando. Absolute provider-/profielselectie mag na de wachttijd worden herhaald.

Een bestaand checkpoint wordt nooit overschreven door een nieuwe `apply`. Hergebruik het met `resume`, of kies een nieuw bestand voor een gewijzigde preset. Verschillende apparaten hebben aparte checkpoints. Er is geen transactie of rollback over alle spelers heen; geslaagde eerdere schrijfacties blijven staan als een latere stap mislukt. `verified` bevestigt de opgeslagen spelerwaarden op het moment van vergelijken, niet geslaagd laden via het netwerk of fysieke video-/audiouitvoer.

## Integraties alleen de benodigde bevoegdheden geven

Gebruik [gewone commando-operators](security-design.md#scoped-command-operators) voor HA, een RF-brug of een agent in plaats van `admin_token` te verspreiden. Sta exacte apparaat-IDs en bewerkingen zoals `input.channel_up` en `input.channel_down` toe. Instellingsbewerkingen vereisen voor deze presetwerkwijze expliciete rechten voor `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` en `provider_settings`. Overdracht vereist `playback_handoff_export` op de bron en `playback_handoff_import` op het doel. Een operatorvermelding combineert de genoemde apparaten en acties; gebruik aparte operatoridentiteiten als dezelfde bewerking niet op ieder genoemd apparaat mag gelden. Tokens en toestemming voor diagnostiek blijven aparte bevoegdheden.

Operators kunnen geen spelerwachtrijen peilen, spelerantwoorden indienen, alle apparaten weergeven, beheerdersdebuguitvoer of resultaten van andere operators lezen. Het beperken van een actie verhindert ook het lezen van de bewaarde bewerking. Een operator verwijderen en herstarten trekt diens toegangsbewijs in. Hergebruik geen operator-ID voor een andere actor zolang diens oude journaalrecords bestaan.

## Doorgaan op een andere speler

```sh
ott handoff living-room bedroom
```

De bron exporteert een live-/archiefverwijzing van versie 1: provider, ondoorzichtige bronidentiteit, ondoorzichtige itemvingerafdruk `channel-v1:<8 hex>.<8 hex>` en, voor archief, de huidige programmatijd. Ruwe provider-IDs en stream-URLs blijven lokaal. Het doel moet **dezelfde geconfigureerde bron/account** hebben, een uniek overeenkomend item in de eigen catalogus vinden en met eigen lokale toegangsgegevens een nieuwe URL verkrijgen. De controller kopieert geen afspeelbare URL en wijzigt geen doeltoegangsgegevens. De bron blijft afspelen. Een geslaagd CLI-resultaat betekent dat het doel de aanvraag heeft verstuurd, niet dat het fysieke scherm of de luidsprekers zijn gecontroleerd.

Versie 1 ondersteunt live-/archiefcatalogi van `m3u`, `xtream` en `stalker` wanneer de speler de vereiste identiteiten kan leveren. Onbekende of dubbelzinnige items, niet-overeenkomende bronnen en afwijzing door lokaal beleid mislukken zonder terugval op zoeken op naam. VOD/series, koppeling tussen verschillende accounts, de bron pauzeren en doorlopende synchronisatie van geschiedenis/favorieten vallen buiten dit eerste contract.

## Validatie- en hersteloefeningen

Voer `go test -race ./...`, `go vet ./...` en `python3 -m unittest discover -s tests` uit. De tests omvatten verloren antwoorden na geslaagde schrijfacties, exacte vergelijking vóór hervatten, configuratieafwijkingen, termijnen van onbekende bewerkingen, twee schrijvers, beschadigde journalen, lezen tussen operators en ongeldige overdrachtsinhoud. Onderbreek op een stagingspeler bovendien de CLI na het opslaan van een instelling, herstart de controller, hervat en verifieer; test overdracht tussen overeenkomende en verschillende bronnen. Bevestig video/audio apart.

Voer voor lokale validatie tussen repositories, met de huidige FOSS-checkout en geïnstalleerde ontwikkelafhankelijkheden, het volgende uit:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

De smoketest bouwt deze Go-server, compileert het volledige FOSS-TypeScript-project en voert het echte transport, de commandohandlers, bronidentiteit en meegeleverde playlistcodec uit tegen de Python-CLI. Hij controleert exacte instellingen voor alle drie ondersteunde providers, afwijkingen, live-overdracht met verschillende lokale rij-IDs, archiefzoekposities, mislukte URL-resolutie en strenge validatie van het overdrachtsformaat. Alleen opslag, providerbeleid en interfaces van de afspeelhost zijn testfixtures; hij verifieert geen browser, fysieke media-uitvoer of echte accounttoegang. Hij bindt aan loopback, gebruikt tijdelijke privébestanden, benadert geen stream-URLs en verwijdert bestanden en server bij afsluiten.

</div>
