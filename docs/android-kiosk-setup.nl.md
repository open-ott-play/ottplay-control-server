# Android-tablet: strenge Plex-kiosk en opstarten

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB-handleiding](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB-manager](android-usb-manager.md)

Een speciale tablet heeft drie apart geteste instellingen nodig: de inhoudswachtrij, het strenge OTT-kioskbeleid en Android-opstarten. Een APK installeren of scherm-uit/hervatten testen bewijst geen start na opnieuw opstarten van het besturingssysteem.

## Identificeren en installeren

1. Noteer de fysieke tablet, OTT-alias/UUID, USB-manageralias, serienummer, model, Android-ID en SDK. OTT- en USB-aliassen kunnen verschillen; leid identiteit niet af uit een alias en hergebruik geen token van een andere tablet.
2. Gebruik de officiële APK-variant voor die SDK, controleer de release-SHA-256 en pakket-/ondertekeningsidentiteit en installeer met `install -r`. Behoud spelergegevens. Gebruik de [gedeelde manager](android-usb-manager.md) wanneer die USB beheert; start geen concurrerende ADB-server en geen `adb`-opdrachten zonder expliciet apparaat.
3. Koppel de speler met zijn eigen controllergegevens en controleer `ott TABLET`. Laad de bedoelde benoemde preset en voer `ott TABLET load PRESET verify` uit. Thuisnetwerkpresets en de localhost-preset van een ontwikkelcomputer zijn niet onderling uitwisselbaar.

## Plex vanaf de tablet controleren

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Controleer bibliotheek-ID, titel en aantal items. Een geslaagd verzoek vanaf de beheercomputer bewijst geen verbinding van de tablet. Controleer bij fouten op de tablet opgeslagen adres, routering, TLS en inloggegevens. Geef de voorkeur aan het geldige HTTPS-eindpunt van de server. Vereist oude firmware HTTP op het privénetwerk, beperk dit dan tot deze installatie, leg vast dat tokens en media onversleuteld over LAN gaan en wijzig geen gedeelde preset of globale certificaatcontrole. Publiceer nooit tokens of privémedia-URL’s in diagnostiek of documentatie.

Wijziging van het Plex-serveradres wist geselecteerde bibliotheekbasismappen. Kies aanbieder **Plex**, open **Plex settings → Folders**, vink de bedoelde bibliotheekrij aan (niet alleen **Browse folders**), kies **Ok** en dan **Save and open library**. In speler `1.1.58-beta.19` kunnen alleen servergegevens de mapkiezer openen terwijl `ready` false blijft; voltooi de selectie vóór wachtrijopdrachten. Schakel niet alleen voor `ready: true` over op M3U: een Plex-wachtrij kan daar spelen, maar `kiosk on --strict` kan een wachtend M3U-beleid zonder gekoppelde media opslaan. Dat vergrendelt of herstelt de Plex-wachtrij niet.

## Wachtrij starten en vergrendelen

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

Gebruik voor een geordende wachtrij `plex library 'LIBRARY NAME'` in plaats van `plex shuffle`. Shuffle kiest één volgorde; kiosk herhaalt die. Bevestig de bedoelde items, `enabled: true`, `strict: true` en beleid gekoppeld aan de juiste Plex-mediawachtrij. Een `waiting`-beleid zonder kanaal of media is geen geaccepteerde kiosk. Observeer minstens twee monsters met oplopende positie/frameaantal en bekijk het scherm. Een bevestiging of label `playing` zonder voortgang is onvoldoende. Test een natuurlijke overgang aan het einde van een item; is een volledige lus onpraktisch, leg die beperking expliciet vast.

Strenge kiosk bestuurt de spelerinterface. Het is geen Android-device-ownerinschrijving en geen bewijs dat Home, Recents, meldingen en Settings geblokkeerd zijn. Behoud de systeemlauncher en een onderhoudsroute onder toezicht.

## Opstarten apart inschakelen

Bouw en controleer [OTT Home 0.2.0](../native/android-home/README.md) en behoud de bestaande ondertekeningssleutel voor updates. Installeer `play.ott.home` naast `play.ott.foss`. Open `play.ott.home/.StartupActivity` en schakel **Start OTT after reboot** in. De instelling staat standaard uit. Ze verandert niet het officiële spelerbestand, de OTA-handtekening, profielgegevens of toewijzing van de Home-knop.

Heeft de firmware een Home-kiezer, kies dan eventueel ook **OTT Home → Always**. Zonder kiezer (ook bij geteste oudere Fire-firmware) verzorgt de opstartoptie zelfstandig de start. Bestaande standaard-Home-installaties behouden hun gedrag. Schakel beide mechanismen uit voor volledig terugdraaien.

`recover_on_boot` van de gedeelde USB-manager is een derde, onafhankelijk mechanisme: het vereist NAS en USB-kabel, handelt alleen een nieuwe boot af en herstart gecrashte apps niet voortdurend. Het vervangt geen autonome start. Laat dat herstelbeleid tijdens een autonome-starttest uit, zodat het een ontbrekende start door de begeleidende app niet maskeert. Respecteer beveiligde vergrendelschermen; ontgrendel normaal wanneer daarom wordt gevraagd.

## Acceptatie en overdracht

Bewaar bewijs privé, zonder inloggegevens in openbare ontvangstbewijzen:

- APK-versies, hashes, koppeling van fysieke/OTT-/USB-identiteit en geselecteerde preset.
- Plex-bibliotheek, itemaantal/volgorde, streng beleid en aanvankelijke afspeelvoortgang.
- Boot-ID vóór en na een echte herstart van het besturingssysteem; daarna `sys.boot_completed=1`.
- Geen handmatige spelerstart of USB-herstel tussen herstart en observatie.
- OTT-activiteit op de voorgrond, controllerherverbinding, herstelde wachtrij/streng beleid en afspeelvoortgang. `requested` van de bootontvanger betekent alleen verzending.
- Aparte aan-uitknoptest: scherm uit, geluid gestopt, normaal ontgrendelen en hervatten.
- Exacte beperkingen: beveiligd ontgrendelen vereist, USB-herstelhulp, ongeteste fysieke uitgangen, streamfouten of geheugentekort. Noem zulke gevallen niet volledig geaccepteerd.

Gebruik vóór overdracht van de volgende tablet dezelfde checklist; kopieer niet de succesclaim van het vorige apparaat. Oudere Fire-tablets kunnen `hls.js` nodig hebben voor M3U doordat de ingebouwde HLS van de fabrikant kan crashen; dat bewijst geen stabiele Plex-weergave.

## Onderhoud en terugdraaien

```sh
ott TABLET kiosk off
```

Open **OTT Startup**, schakel **Start OTT after reboot** uit en herstel de oorspronkelijke Home-keuze als die is gewijzigd. Verwijder zo nodig alleen `play.ott.home` via het geverifieerde USB-transport; behoud `play.ott.foss` en de gegevens. Opstarten uitschakelen verandert de wachtrij niet en schakelt kiosk niet automatisch uit.
