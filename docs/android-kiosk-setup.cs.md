# Tablet Android: přísný kiosk Plex a spouštění

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Příručka ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Správce USB](android-usb-manager.md)

Vyhrazený tablet potřebuje tři samostatně ověřená nastavení: frontu obsahu, přísné zásady kiosku OTT a spouštění Androidu. Instalace APK ani ověření vypnutí obrazovky a obnovení nedokládá spuštění po restartu systému.

## Identifikace a instalace

1. Zaznamenejte fyzický tablet, alias/UUID OTT, alias správce USB, sériové číslo, model, Android ID a SDK. Aliasy OTT a USB se mohou lišit; neodvozujte identitu z aliasu a nepoužívejte token jiného tabletu.
2. Použijte oficiální variantu APK kompatibilní s SDK, ověřte SHA-256 vydání a identitu balíčku/podpisu, nainstalujte přes `install -r`. Zachovejte data přehrávače. Pokud USB vlastní [sdílený správce](android-usb-manager.md), použijte jej; nespouštějte konkurenční server ADB ani příkazy `adb` bez určení zařízení.
3. Spárujte přehrávač jeho vlastními údaji k řadiči a ověřte `ott TABLET`. Načtěte požadovanou pojmenovanou předvolbu a spusťte `ott TABLET load PRESET verify`. Předvolby domácí sítě a localhost vývojového počítače nejsou zaměnitelné.

## Ověření Plex z tabletu

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Ověřte ID, název a počet položek knihovny. Úspěšný požadavek z počítače správce nedokládá připojení tabletu. Při selhání zkontrolujte v tabletu uloženou adresu, směrování, podporu TLS a přihlašovací údaje. Upřednostněte platný HTTPS endpoint serveru. Vyžaduje-li starý firmware HTTP v soukromé LAN, omezte jej na tuto instalaci, zaznamenejte nešifrovaný přenos tokenů a médií a nepřepisujte sdílenou předvolbu ani globálně nevypínejte ověřování certifikátů. Nikdy nezveřejňujte tokeny ani soukromé adresy médií v diagnostice či dokumentaci.

V novějších přehrávačích s funkcí `categories` v `caps` otevře Plex všechna videa bez povinného výběru složek. Použijte `ott TABLET categories` a `ott TABLET category all`; viz [kategorie médií](cli.md#media-categories-and-stable-video-selection). Níže uvedený výběr složek platí pro výslovně uvedenou starší verzi přehrávače.

Změna adresy serveru Plex vymaže vybrané kořeny knihoven. Zvolte poskytovatele **Plex**, otevřete **Plex settings → Folders**, zaškrtněte řádek správné knihovny (nejen **Browse folders**), zvolte **Ok** a poté **Save and open library**. V přehrávači `1.1.58-beta.19` mohou samotné údaje serveru otevřít výběr složek, zatímco `ready` zůstává false; dokončete výběr před příkazy fronty. Nepřepínejte na M3U pouze kvůli `ready: true`: fronta Plex se tam může přehrát, ale `kiosk on --strict` může uložit čekající zásadu M3U bez navázaných médií. Ta frontu Plex nezamkne ani neobnoví.

## Spuštění a uzamčení fronty

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

Pro seřazenou frontu použijte `plex library 'LIBRARY NAME'` místo `plex shuffle`. Shuffle jednou vybere pořadí; kiosk jej opakuje. Potvrďte správné položky, `enabled: true`, `strict: true` a zásadu navázanou na správnou frontu médií Plex. Zásada `waiting` bez kanálu nebo médií není přijatý kiosk. Sledujte alespoň dva vzorky s rostoucí pozicí/počtem snímků a prohlédněte displej. Potvrzení nebo označení `playing` bez postupu nestačí. Otestujte přirozený přechod po skončení položky; pokud není praktické zkoušet celou smyčku, výslovně zaznamenejte tento limit.

Přísný kiosk řídí rozhraní přehrávače. Není registrací Android device owner ani důkazem zablokování Home, Recents, oznámení a Settings. Zachovejte systémový spouštěč a obsluhovanou cestu údržby.

## Samostatné povolení spouštění

Sestavte a ověřte [OTT Home 0.2.0](../native/android-home/README.md), zachovejte stávající podpisový klíč pro aktualizace. Nainstalujte `play.ott.home` vedle `play.ott.foss`. Otevřete `play.ott.home/.StartupActivity` a povolte **Start OTT after reboot**. Volba je ve výchozím stavu vypnuta. Nemění oficiální binární soubor, podpis OTA, profily ani přiřazení tlačítka Home.

Má-li firmware výběr Home, lze navíc zvolit **OTT Home → Always**. Pokud výběr není dostupný (včetně testovaného starého Fire firmwaru), povolení startu funguje nezávisle. Dříve nastavené instalace výchozího Home zachovávají chování. Pro úplný návrat vypněte oba mechanismy.

`recover_on_boot` sdíleného správce USB je třetí nezávislý mechanismus: vyžaduje NAS a USB kabel, řeší pouze nové spuštění systému a nerestartuje průběžně spadlou aplikaci. Nenahrazuje autonomní start. Při jeho testu ponechte obnovu vypnutou, aby nezakryla chybějící spuštění doprovodnou aplikací. Respektujte zabezpečené zámky obrazovky; při výzvě odemkněte běžným způsobem.

## Přejímka a předání

Důkazy uložte soukromě, bez přihlašovacích údajů ve veřejných potvrzeních:

- Verze APK, hashe, mapování fyzické/OTT/USB identity a vybraná předvolba.
- Knihovna Plex, počet/pořadí položek, přísná zásada a počáteční postupující přehrávání.
- Boot ID před skutečným restartem systému a po něm; následně `sys.boot_completed=1`.
- Žádné ruční spuštění přehrávače nebo obnova USB mezi restartem a pozorováním.
- Aktivita OTT v popředí, obnovení připojení řadiče, fronty/přísné zásady a postup přehrávání. Výsledek `requested` přijímače startu znamená jen odeslání požadavku.
- Samostatný test vypínače: obrazovka vypnutá, zvuk zastavený, běžné odemknutí a obnovení.
- Přesné limity: nutné zabezpečené odemknutí, obnova pomocí USB, neotestované fyzické úniky, chyby streamu či nedostatek paměti. Takové případy neoznačujte za plně přijaté.

Před předáním dalšího tabletu použijte stejný kontrolní seznam místo převzetí úspěchu předchozího zařízení. Starší Fire mohou potřebovat `hls.js` pro M3U, protože nativní HLS výrobce může spadnout; to nedokládá stabilitu Plex.

## Údržba a návrat

```sh
ott TABLET kiosk off
```

Otevřete **OTT Startup**, vypněte **Start OTT after reboot** a obnovte původní Home, pokud se změnil. Případně odinstalujte pouze `play.ott.home` přes ověřený USB transport; zachovejte `play.ott.foss` a jeho data. Vypnutí spouštění nemění frontu ani automaticky nevypíná kiosk.
