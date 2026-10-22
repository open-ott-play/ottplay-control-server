<div lang="cs" dir="ltr">

# Spolehlivé ovládání a předání přehrávání

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Přehled dokumentace](wiki/Home.md)

Tyto funkce vyžadují řadič a CLI z revize obsahující tuto příručku. Přesné porovnávání nastavení a předání vyžadují také aktualizovaný přehrávač. Starší archivy vydání a historická revize CLI z instalační příručky tyto funkce automaticky nezískají. Instalujte **všechny soubory `cli/*.py` společně**, včetně `preset_session.py`; zachovejte soukromou konfiguraci.

![Průběh ovládání, kontrolního bodu a předání](assets/reliable-control.svg)

## Zapnutí omezeného trvalého uchovávání výsledků

Přidejte tento volitelný blok do soukromé konfigurace serveru, ověřte ji a restartujte server. Zvolte absolutní cestu na místním trvalém souborovém systému, kam smí účet služby zapisovat. V Unixu musí být adresář soukromý (0700); existující soubory žurnálu a zámku musejí být běžné soukromé soubory (0600). Ve Windows omezte ACL adresáře na účet služby. Jeden žurnál vlastní jediný proces.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Server vytváří chybějící adresáře s režimem 0700. Limity jsou 1–4096 záznamů, 60–86400 sekund a 4 MiB na disku. Doba uchování musí pokrývat `command_ttl_seconds`. Plný žurnál odmítá nové operace s klíčem místo vytěsňování dosud platných záznamů. Poškozené soubory, nezabezpečený unixový adresář, další zapisovatel nebo selhání trvalého zápisu vedou k bezpečnému odmítnutí. Výchozí zůstává dosavadní zpracování příkazů v paměti.

Přibalená jednotka systemd vytváří `/var/lib/ottplay-control` s režimem 0700. Pro Compose připravte hostitelský adresář vlastněný UID 65532, nastavte `CONTROL_SERVER_STATE` na jeho absolutní cestu a kombinujte `deploy/compose.yml` s `deploy/compose.journal.yml`. Druhý soubor přidává pouze zapisovatelný svazek stavu; kořenový souborový systém zůstává jen pro čtení. Pro Helm připravte samostatně požadavek na trvalý svazek a nastavte `journalExistingClaim=CLAIM_NAME`. Připojí se do `/var/lib/ottplay-control`; použijte výše uvedený soukromý podadresář. Chart zachovává jednu repliku, `Recreate` a `fsGroupChangePolicy: OnRootMismatch`, aby opětovné připojení rekurzivně nerozšiřovalo oprávnění již vytvořených soukromých adresářů. Ověřte, že zvolený ovladač úložiště tuto vlastnickou sémantiku podporuje. Chart požadavek nevytváří ani nemaže a povolení připojení nezapíná žurnál: ten nadále řídí soukromá konfigurace serveru.

Pro `POST /api/requests` přidejte `idempotency_key` vedle `action` a `params`. Starší příkazové POST místo toho přijímají `Idempotency-Key`. Klíče obsahují 16–80 ASCII písmen, číslic, podtržítek nebo spojovníků; pro každý záměr vygenerujte nový náhodný klíč. Stejný vlastník/klíč se stejným zařízením, akcí a normalizovaným JSON obsahem vrátí původní ID bez opětovného zařazení do fronty. Změna obsahu vrací 409. Klíče mají rozsah podle ID operátora (nebo správce), nikoli zařízení. Po uplynutí doby uchování může být klíč přijat jako nový záměr: klienti nesmějí mimo toto okno opakovat starý záměr.

`GET /api/operations?device_id=DEVICE` hlásí, zda je žurnál zapnutý, jeho limity a TTL příkazů. Pro čtení operace přidejte `&key=KEY`:

- `pending`: přijato; zatím není uchována odpověď.
- `responded`: přišla ověřená odpověď přehrávače; `result_status` je `ok`, `rejected` nebo `unsupported`. Původní tělo výsledku nadále běžně expiruje.
- `acknowledged`: starší konzument potvrdil příkaz. To nedokazuje viditelné přehrávání ani uložení změny nastavení.
- `unknown`: vypršel termín nebo byl server restartován před uchováním odpovědi. Server tento příkaz po restartu **nikdy nepřehraje znovu**.

Opakovaný POST s klíčem vrací `status: retained`, původní ID a stav. Běžné nové POST zachovávají `status: queued`. Záznamy žurnálu obsahují pouze vlastníka, zařízení, akci, náhodná ID, otisk obsahu, stav a termíny. Neobsahují obsah příkazů, URL poskytovatelů, tokeny, snímky obrazovky ani těla odpovědí. Čekající příkazy se trvale neukládají. Jde o omezenou deduplikaci a sledování výsledků, nikoli provedení právě jednou, trvalou pracovní frontu nebo vysokou dostupnost více serverů.

## Plánování, použití, obnovení a ověření Local/Home

Dosavadní chování `ott PLAYER load PRESET` je zachováno. Pro nový postup:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` pracuje offline: ověří místní předvolbu a vypíše pozice nastavení a požadovaný aktivní profil bez jejich obsahu. Přehrávač nezkoumá. `verify` provádí přesná porovnání pouze pro čtení a kontroluje vybraný profil M3U. Pokud se cokoli liší nebo porovnání není podporováno, vrací nenulový kód.

`apply` vyžaduje žurnál serveru. Vytvoří soukromý kontrolní bod a před první změnou porovná každého nakonfigurovaného poskytovatele. Každé uložení pomocí `settings_compare` porovná uložené bajty s očekávanými hodnotami; shoda přeskočí zápis. Závěrečné ověření zahrnuje Plex, všechny pozice M3U, volitelné pozice Stalker a vybraný profil M3U. Porovnání vracejí logickou hodnotu, nikdy uložená tajemství.

Výchozí kontrolní bod je pod `~/.config/ottplay-control/checkpoints/`. Jiný soubor zvolíte pomocí `--checkpoint /absolute/private/path/session.json` za režimem. Kontrolní body obsahují zařízení, název předvolby, hash revize serveru/předvolby, názvy dokončených kroků a jeden čekající náhodný klíč operace s termínem. Neobsahují hodnoty nastavení. Soubory se po fsync atomicky nahrazují a zamykají proti souběžným zápisům CLI. Odpovídající místní předvolbu ponechte dostupnou. V Unixu musí být adresář soukromý (0700), s běžnými soubory v režimu 0600. Ve Windows omezte zděděné ACL adresáře na uživatele spouštějícího CLI; unixové bity režimu nevynucují přístupová oprávnění Windows.

`resume` ověří stejný server, zařízení a revizi předvolby a potom přečte čekající výsledek. Uchovaná odpověď dovolí okamžitou novou kontrolu. Nejistý výsledek zastaví změny, dokud neuplyne termín předchozího příkazu, časový limit požadavku a malá rezerva. Poté znovu spusťte `resume`. Znovu kontroluje uložená nastavení i u hotových kroků, aby změny uživatele nebyly zaměněny za úspěšné dřívější zápisy. Nikdy slepě neopakuje relativní příkaz. Absolutní výběr poskytovatele/profilu lze po ochranné lhůtě zopakovat.

Nové `apply` nikdy nepřepíše existující kontrolní bod. Použijte jej znovu pomocí `resume`, nebo pro změněnou předvolbu vyberte nový soubor. Různá zařízení mají samostatné kontrolní body. Neexistuje transakce ani návrat změn přes všechny přehrávače; úspěšné dřívější zápisy zůstávají, když pozdější krok selže. `verified` potvrzuje uložené hodnoty přehrávače v okamžiku porovnání, nikoli úspěšné síťové načtení nebo fyzický výstup obrazu/zvuku.

## Integracím přidělujte jen nezbytná oprávnění

Pro HA, RF most nebo agenta používejte [běžné příkazové operátory](security-design.md#scoped-command-operators) místo rozdávání `admin_token`. Povolujte přesná ID zařízení a operace jako `input.channel_up` a `input.channel_down`. Operace nastavení v tomto postupu předvoleb vyžadují explicitní oprávnění `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` a `provider_settings`. Předání vyžaduje `playback_handoff_export` na zdroji a `playback_handoff_import` na cíli. Záznam operátora kombinuje uvedená zařízení a akce; pokud stejná operace nemá platit pro všechna uvedená zařízení, použijte oddělené identity operátorů. Tokeny a souhlas s diagnostikou zůstávají oddělenými oprávněními.

Operátoři nemohou dotazovat fronty přehrávačů, odesílat odpovědi přehrávačů, vypsat všechna zařízení, číst ladicí výstup správce ani výsledek jiného operátora. Zúžení rozsahu akce také zabrání čtení její uchované operace. Odstranění operátora a restart odvolají jeho přihlašovací údaj. Nepoužívejte ID operátora pro jiný subjekt, dokud existují jeho staré záznamy žurnálu.

## Pokračování na jiném přehrávači

```sh
ott handoff living-room bedroom
```

Zdroj exportuje odkaz na živé vysílání/archiv verze 1: poskytovatele, neprůhlednou identitu zdroje, neprůhledný otisk položky `channel-v1:<8 hex>.<8 hex>` a pro archiv aktuální čas pořadu. Surová ID poskytovatelů a URL streamů zůstávají místní. Cíl musí mít **stejný nakonfigurovaný zdroj/účet**, vyhledat jedinou odpovídající položku ve vlastním katalogu a získat novou URL pomocí vlastních místních přihlašovacích údajů. Řadič nekopíruje přehratelnou URL ani nemění přihlašovací údaje cíle. Zdroj pokračuje v přehrávání. Úspěšný výsledek CLI znamená, že cíl odeslal požadavek, nikoli že byla ověřena jeho fyzická obrazovka nebo reproduktory.

Verze 1 podporuje živé/archivní katalogy `m3u`, `xtream` a `stalker`, pokud přehrávač může dodat požadované identity. Neznámé či nejednoznačné položky, neodpovídající zdroje a odmítnutí místní politikou selžou bez náhradního hledání podle názvu. VOD/seriály, mapování mezi různými účty, pozastavení zdroje a průběžná synchronizace historie/oblíbených nejsou součástí tohoto prvního kontraktu.

## Ověřovací a obnovovací cvičení

Spusťte `go test -race ./...`, `go vet ./...` a `python3 -m unittest discover -s tests`. Testy pokrývají ztracené odpovědi po úspěšném zápisu, přesné porovnání před obnovením, odchylky konfigurace, termíny neznámých operací, dva zapisovatele, poškozené žurnály, čtení napříč operátory a chybně sestavená data předání. Na testovacím přehrávači navíc přerušte CLI po uložení nastavení, restartujte řadič, obnovte a ověřte; otestujte předání mezi odpovídajícími i neodpovídajícími zdroji. Obraz/zvuk potvrďte samostatně.

Pro místní ověření napříč repozitáři s aktuální pracovní kopií FOSS a nainstalovanými vývojovými závislostmi spusťte:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Smoke test sestaví tento Go server, zkompiluje celý TypeScript projekt FOSS a proti Python CLI spustí jeho skutečný transport, obsluhu příkazů, identitu zdroje a dodávaný kodek playlistů. Kontroluje přesná nastavení všech tří podporovaných poskytovatelů, neshody, předání živého vysílání s odlišnými místními ID řádků, posuny pozice v archivu, selhání překladu URL a přísnou validaci přenášeného formátu. Testovacími náhradami jsou pouze úložiště, politika poskytovatele a rozhraní hostitele přehrávání; neověřuje prohlížeč, fyzický mediální výstup ani skutečný přístup k účtu. Naslouchá na loopbacku, používá soukromé dočasné soubory, nekontaktuje URL streamů a při ukončení odstraní své soubory i server.

</div>
