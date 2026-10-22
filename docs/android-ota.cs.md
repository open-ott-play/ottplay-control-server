<div lang="cs" dir="ltr">

# Android OTA: běžné přehrávače a samostatný agent s rootem

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Pro **k8a/fire8 a další přehrávače Capacitor** použijte aktualizátor Wi-Fi běžného přehrávače, nikoli příkaz agenta s rootem:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Vynechání `--version` vybere nejnovější publikovanou betu. Na Androidu 7+ použijte `full` místo `api22`. CLI najde oficiální vydání, ověří manifest, připraví APK a dotazuje jeho stav, než jednou požádá o instalaci. Android může vyžadovat potvrzení na tabletu. Před údržbou výslovně vypněte kiosk a odemkněte chráněná nastavení; příkaz tyto zásady nikdy nemění. Viz [úplný postup CLI](cli.md#capacitor-apk-updates-over-wi-fi).

Je nutné mít dříve nainstalované APK podporující `AppUpdate`, Wi-Fi a běžící přehrávač připojený k řadiči. Běžné další aktualizace nepotřebují USB, ale nelze tak dosáhnout na offline tablet nebo zastavené WebView. První instalace, nedostupný aktualizátor, poškozená aplikace nebo ztracená síť mohou nadále vyžadovat místní obnovu. Neznamená to automatický plánovač vydání ani obecný tichý instalátor.

Zbytek příručky popisuje **jiného agenta údržby a1 s rootem**, omezeného na připravenou instalaci Matricom/API19 `play.ott.kitkat`. Na běžných zařízeních Fire/Capacitor není předinstalován ani použitelný.

Po jednorázovém úvodním nastavení přes USB s rootem může nezávislý agent Android přijímat podepsané aktualizace přes své odchozí HTTPS spojení. K vlastní aktualizaci nepotřebuje fungující WebView. Agent 0.1.2 podporuje také koordinovaný balíček APK a systémového dotykového helperu s uloženými soubory obnovy a ověřením před definitivním potvrzením.

Příručka vysvětluje celý postup. Přesná pole manifestu, limity a požadavky na hardware naleznete v [referenci nativního agenta](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Co se aktualizuje

![Architektura OTA: zkontrolované bajty vydání se podepisují, publikují přes přímé HTTPS, ověřují nezávislým agentem a kontrolují po instalaci. Nasazení webu je oddělené.](images/ota-architecture.svg)

- **Nativní agent:** `kind: agent` nahrazuje vlastní binární soubor ARMv7. Funguje i při vypnutém OTT a používání jiné aplikace HOME.
- **Pouze APK:** `kind: apk` nahrazuje obal a zachovává jeho data. OTT musí být povoleno a v popředí. Helper vložený do kandidáta musí přesně odpovídat nainstalovanému systémovému helperu.
- **APK a dotykový helper:** `kind: bundle` aktualizuje obě součásti jako pár. Starší agent musí nejdříve dostat samostatnou aktualizaci agenta podporující balíčky.
- **Hostovaný přehrávač:** nasaďte ověřený webový artefakt na stávající origin, potom znovu načtěte a zkontrolujte aktivní přehrávač. Aktualizace APK nenasazuje hostovaný JavaScript. Viz [postup vydání](release-workflow.md).
- **Řadič a CLI:** nasaďte řadič a samostatně nainstalujte úplnou sadu modulů CLI. Aktualizace jednoho z nich nenahrazuje kód v tabletu.

Firmware, jádro, systémové WebView, root broker a spouštěcí skripty jsou mimo tento formát OTA. [Nativní reference](../native/android-agent/README.md#bootstrap) popisuje hranice úvodního nastavení a obnovy. Podporované OTA operace nevyžadují síťové ADB ani libovolný vzdálený shell.

## Od zkontrolovaného vydání k potvrzené instalaci

1. **Vyberte ověřené bajty.** Sloučte zkontrolované změny po úspěšné CI. Stáhněte požadovaný artefakt a ověřte zdrojovou revizi, neměnné doklady vydání a kontrolní součet. Z nativního archivu vyjměte binární soubor `agent`; samotný archiv není instalovatelným obsahem agenta.
2. **Připravte a podepište.** Použijte stávající soukromý klíč Ed25519, jehož veřejný klíč byl pevně uložen při úvodním nastavení. Klíč a přihlašovací údaje zařízení chraňte.
3. **Publikujte a ověřte přenos.** Publikujte pouze obsah a `signed.json` na neměnných HTTPS cestách. Oba musí vracet bajty přímo s HTTP 200; aktualizátor odmítá přesměrování. Spusťte příkaz `verify` balicího nástroje.
4. **Pošlete jednu aktualizaci.** Zaznamenejte ID požadavku a po potvrzení zkontrolujte stav. Ztracená či nejistá odpověď není důvodem ke slepému opakování operace.
5. **Potvrďte účinek.** Porovnejte čerstvě zjištěné nainstalované hashe a verze s připraveným vydáním. U aktivního přehrávače ověřte také cíl kiosku, frontu a postup přehrávání. Fyzický obraz a zvuk vyžadují pozorování.

Příklad vydání agenta; dosaďte zvolenou verzi a skutečné cesty:

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

`ENVELOPE_SHA256` je `envelope_sha256` balicího nástroje, nikoli hash obsahu. `accepted` znamená přijetí, ne úspěšnou instalaci. Vlastní výměna může ponechat staré potvrzení operace jako `unknown`; dokladem instalace jsou nový běhový proces, očekávaná verze agenta a odpovídající nainstalovaný hash. [Reference CLI](cli.md#independent-android-agent) popisuje nezávislé nativní směrování a práci s potvrzeními.

## Obnova transakce APK/helper

![Transakce APK/helper: předběžná kontrola, uložení předchozího páru, použití, ověření a potvrzení; selhání obnoví předchozí pár a nedokončená obnova dál blokuje změny aplikace.](images/ota-transaction.svg)

Balíček obsahuje přesně `app.apk` a `ott-touch-guard`. Balicí nástroj jej může vytvořit přímo z podepsaného obalového APK pomocí `--kind bundle --apk`. Extrahuje vložený helper, takže obě dodané kopie odpovídají. Viz [příkaz balíčku a požadavky](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Před změnou souborů agent vyžaduje odpovídající nainstalovaný pár, dostatek volného místa, reagující OTT v popředí, systémový oddíl pouze pro čtení a ohlášenou podporu downgradu APK. U zamčeného kiosku ověřuje stejné ID TV kanálu nebo seřazená ID VPortal; pouhá shoda délek front nestačí. Nepodporované zamčené fronty odmítá. Odmítá i úmyslně vypnutou aplikaci OTT, čímž zachovává současnou aplikaci HOME uživatele.

Soubory obnovy a čekající transakce se trvale ukládají před zastavením aplikace. Agent vyčká na zastavení aplikace a starého helperu, atomicky helper nahradí, vrátí `/system` do režimu jen pro čtení, nainstaluje APK a ověří výsledek. Selhání použití nebo ověření spustí obnovu předchozího páru. Obnova při startu obnovuje soubory bez spuštění OTT či převzetí HOME.

Nelze-li obnovu dokončit, `updates.recovery_failed` je pravda. Diagnostika a podepsaná oprava samotného agenta zůstávají dostupné; změny aplikace a watchdog přehrávání jsou blokovány. Uložená transakce se nesmí smazat kvůli vynucení instalace. Obnova binárek nevrací migrace aplikačních dat a firmware může stále odmítnout downgrade. Viz [omezení obnovy](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Výsledky a záznam přijetí

![Graf ověření: 488 testů CLI, 576 testů nástrojů vydání a 6 testů OTA balení; referenční tablet používá ověřeného nového agenta, fyzické přijetí APK/helper však dosud čeká.](images/ota-validation.svg)

Audit implementace 14. října 2026 prošel 488 testy CLI (jeden přeskočen), 576 testy nástrojů vydání, šesti testy OTA balení, nativními Go race/statickými kontrolami a sestavením ARMv7. Počty popisují velikost sad, ne pokrytí nebo výkon. Testy vkládající chyby procvičují neplatné archivy, přerušené transakce, částečnou obnovu, změny cíle a blokování během obnovy. Nezávislá revize také odhalila a opravila nedostatečnou kontrolu identity fronty a rozdílné ověřování APK/helper na hostiteli a zařízení.

Ověřený stav k 15. říjnu 2026:

- Implementace OTA je sloučena v [PR řadiče #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) a zahrnuta ve [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Aktuální a předchozí podepsané balíčky agenta jsou publikovány na [distribučním hostiteli OTA](https://android-updates.ottplay.here.now/). Jejich obsah a obálky byly úspěšně staženy a porovnány přes přímé HTTPS.
- Referenční tablet se nezávislým OTA kanálem aktualizoval z `0.1.1-beta.21` na `0.1.2-beta.4`. Verze běžícího agenta a nainstalovaný SHA-256 odpovídaly zvolenému vydání; běhový proces se změnil a ID startu OS zůstalo stejné. OTT nadále nemělo běžící aplikační proces a nebyla odeslána operace spuštění aplikace ani výběru HOME.
- Nativní stav hlásil hash systémového dotykového helperu, žádný čekající balíček a žádnou chybu obnovy. Metadata APK nebyla dostupná, takže toto pozorování neurčuje verzi ani hash nainstalovaného APK.
- Koordinovaná transakce APK/helper je ověřena na hostiteli; fyzická instalace, obnova downgradu, obraz a zvuk dosud nejsou přijaty na referenčním tabletu.

Pro každé další nasazení uchovejte zdroj vydání a kontrolní součty, hash připravené obálky, ID požadavku, nativní stav před/po a samostatný výsledek fyzického přijetí. Úspěšné stažení ani potvrzení příkazu tato pozorování nenahrazují.

</div>
