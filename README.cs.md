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

[Dokumentace / Wiki](docs/wiki/Home.cs.md)

Malý server pro vlastní provoz, který doručuje vzdálené příkazy přehrávačům OTT-play. Přehrávače odesílají odchozí HTTP požadavky, takže televizor nemusí otevírat naslouchající port. Každý přehrávač má vlastní frontu a přístupový token; příkazy se odesílají samostatným tokenem správce.

Volitelné [spolehlivé ovládání](docs/reliable-control.md) přidává přihlašovací údaje integrací s omezenými oprávněními, omezené trvalé ukládání výsledků příkazů, obnovitelné použití konfigurací Local/Home s přesným ověřením nastavení a výslovné předání mezi živým a archivním přehráváním. Tyto funkce vyžadují revize řadiče a CLI obsahující tuto příručku. Přesné ověření a předání také vyžadují aktualizovaný přehrávač. Níže uvedená starší pevně určená instalační revize tyto funkce neobsahuje. [Schéma architektury](docs/assets/reliable-control.svg) ukazuje, kde zůstávají nastavení, přihlašovací údaje a stav operací.

Server běží jako jediný přenositelný spustitelný soubor Go s pevně určenou verzí knihovny DNS pro volitelné vyhledávání služeb. Balíčky jsou dostupné pro Linux amd64, arm64 a ARMv7; macOS Intel a Apple Silicon; Windows amd64 a arm64. Kontejnerové obrazy podporují Linux amd64 a arm64. Kubernetes a k3s používají přiložený Helm chart.

## Spuštění serveru

Stáhněte odpovídající archiv z [vydání](https://github.com/open-ott-play/ottplay-control-server/releases), ověřte jej podle manifestu vydání a rozbalte. Ve Windows používejte v následujících příkazech `ottplay-control-server.exe`.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` vytvoří náhodné, odlišné přihlašovací údaje v novém souboru, který je v Unixu přístupný pouze vlastníkovi. Odmítne přepsat existující soubor nebo symbolický odkaz a nikdy údaje nevypisuje. Soubor chraňte; ve Windows omezte jeho ACL na účet, pod kterým server běží. `config.example.json` popisuje pole a záměrně obsahuje neplatné zástupné tokeny.

Výchozí adresa je `0.0.0.0:8081` (všechna rozhraní IPv4 včetně LAN). Televizory připojujte pomocí LAN adresy a portu serveru. Existující konfigurace si zachovají výslovnou hodnotu `listen`; změňte ji na `0.0.0.0:8081` nebo použijte `--listen 0.0.0.0:8081`, pokud chcete zpřístupnit instalaci dříve omezenou na loopback. Do `allowed_origins` přidejte **přesný origin stránky** přehrávače, například `http://192.168.1.20:8443`. Schéma, název hostitele a port se musí shodovat; nepřipojujte cestu. Prázdný seznam originů povoluje pouze klienty mimo prohlížeč. Změny konfigurace se projeví po restartu. Přihlašovací údaje zařízení i správce jsou stále povinné.

HTTP je podporováno pro starší televizní prohlížeče v důvěryhodné LAN. Pokud připojení prochází nedůvěryhodnou sítí, použijte HTTPS: `serve --config config.json --tls-cert server.crt --tls-key server.key`, nebo ukončete TLS na reverzní proxy. Stránky přehrávače načtené přes HTTPS se v prohlížeči nemohou připojit k prostému HTTP serveru. Nativní aplikace také zachovávají přenosová pravidla svého operačního systému; toto nastavení je neuvolňuje.

Přímé HTTPS ověřuje dodaný řetězec certifikátů před otevřením naslouchání: klíče RSA musí mít alespoň 2048 bitů a ECDSA alespoň 224 bitů; podporováno je také Ed25519. Certifikát s příliš malým klíčem před aktualizací vyměňte. Viz [pravidla certifikátů TLS a jejich rozsah](docs/security-design.md#direct-https-certificate-policy).

## Připojení přehrávače

Použijte vydání přehrávače s podporou připojení k příkazovému serveru. Otevřete **Nastavení → Vzdálené ovládání** a zadejte:

1. **Adresa serveru:** IP nebo název hostitele, volitelně s portem, nebo úplná adresa `http://` / `https://`. Samotný hostitel standardně používá HTTP na portu 8081.
2. **Přístupový kód:** `token` položky tohoto přehrávače v `devices`, nikdy `admin_token`.
3. **Připojit:** povolte dotazování a zkontrolujte stav ve stejném dialogu.

Přehrávač ukládá toto připojení pouze v aktuální instalaci a vynechává je ze záloh nastavení a cloudového přenosu. Samostatný místní HTTP server není potřeba. Webové prohlížeče vyžadují povolený CORS origin; zabalené televizní stránky s `Origin: null` mohou použít výslovné nastavení `allow_null_origin` pro určené trasy zařízení a počátečního připojení. Tím se nepovolují trasy správce ani diagnostického operátora.

## Dálkové ovládání v terminálu

[Ilustrovaná příručka Android OTA](docs/android-ota.md) rozlišuje běžné aktualizace Capacitor přes Wi-Fi (`ott DEVICE update --variant api22|full`, standardně nejnovější beta; `--version` vybírá vydání) od samostatného agenta s root přístupem. Vysvětluje podepsané aktualizace agenta a APK/helperu, obnovu a ověření nainstalovaného výsledku.

[Příručka Android ADB](docs/android-adb.md) popisuje USB přístup přes pracovní stanici nebo Synology, ovládání obrazovky a klávesnice, snímky obrazovky, protokoly, instalaci APK a běžný restart včetně omezení starších tabletů.

Volitelné [vyhledávání a párování DNS-SD](docs/discovery.md) umožňuje přehrávačům najít řadič přes jejich síťovou doménu a po `ott approve NAME CODE` získat individuální token zařízení. DNS obsahuje pouze metadata služby. Existující ruční připojení fungují dál bez zapnutí vyhledávání.

Python CLI nabízí aliasy přehrávačů, aktuální hlasitost, vyhledávání kanálů, aktuální EPG, výběr poskytovatele a konfiguraci podporovaných poskytovatelů: [příručka CLI](docs/cli.md). Server i přehrávač musí obsahovat rozšíření požadavek/odpověď.

Python CLI se instaluje z pracovní kopie zdrojů odděleně od archivu nativního serveru. [Postup instalace a konfigurace](docs/cli.md#installation-and-connection) zahrnuje macOS/Linux a Windows, nastavení Python/PATH a potřebné moduly CLI. Určuje ověřený vývojový commit 0.1.1 s workbenchem a offline ověřováním; stabilní CLI v0.1.0 a archivy nativního serveru tyto příkazy neobsahují. [Aktualizujte CLI nebo se vraťte ke starší verzi](docs/cli.md#update-select-or-roll-back-the-cli) bez nahrazování existující pracovní kopie, spouštěče nebo soukromé konfigurace. Místní spouštěče mohou použít [resolver playlistů](docs/cli.md#local-playlist-resolver) pouze pro čtení k vyhledávání kanálů, aktuálních pořadů a přehratelných archivů bez řadiče. Po instalaci a konfiguraci:

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

`a1` je příklad aliasu registrovaného přehrávače, nikoli název počítače. Viz [registrace a párování](docs/cli.md#register-and-connect-a-player), [všechny příkazy s příklady](docs/cli.md#commands), [odpojení a odebrání přístupu](docs/cli.md#disconnect-deregister-or-revoke-a-player) a [řešení potíží](docs/cli.md#troubleshooting).

U trvale zapnutého přehrávače použijte [standby a go](docs/cli.md#stop-playback-while-keeping-the-player-online) k zastavení přehrávání a pozdějšímu probuzení bez ukončení aplikace. `stop` zůstává aliasem `standby`; `start` a `wake` jsou aliasy `go`. Vzdálené ovládání musí zůstat připojené, takže pohotovostní režim nezaručuje nulový síťový provoz. `ott a1` ukazuje operace nabízené přehrávačem. Přijatý příkaz nepotvrzuje dokončené vypnutí ani zahájení přehrávání.

[Příkazy poměru stran](docs/cli.md#aspect-ratio-fit-and-fill) vyžadují aktualizované CLI a řadič a přehrávač nabízející schopnost `aspect`. Změna se použije po potvrzení; načtěte `aspect` znovu a ověřte režim i jeho uložení. Fill obraz proporcionálně ořízne, nikoli roztáhne.

Vzdálené [snímky obrazovky](docs/cli.md#remote-screenshots) vyžadují aktualizované CLI a řadič a přehrávač s podporovaným adaptérem zachytávání. Zapnutí připojení Vzdálené ovládání opravňuje daný řadič k pořizování snímků a diagnostice; nativní snímky nepotřebují další přepínač oprávnění. Prohlížeče stále vyžadují místní výběr zdroje. Snímky mohou obsahovat nastavení, obrazovky PIN a viditelné přihlašovací údaje, proto se připojujte pouze k důvěryhodnému řadiči. `ott a1` hlásí dostupnost a požadovaný výběr zdroje. Snímky jsou oddělené od sběru strukturované telemetrie.

Chcete-li promíchat všechna videa VPortal odpovídající názvu a přehrávat je opakovaně:

```sh
ott l vpr "wedding"
```

`vpr` jednou promíchá celý výběr a pak tuto frontu opakuje. Pro nové promíchání jej spusťte znovu. `vp` přehrává výsledky v pořadí katalogu; `vp --list` je vypíše bez změny přehrávání. Viz [příkazy VPortal](docs/cli.md#commands).

Pro výslovné pořadí Plex s uloženou konfigurací Plex přehrávače:

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

CLI standardně čeká až 45 sekund. Zápis `ott -t 45 l plex shuffle "Tom and Jerry"` výslovně určuje stejný časový limit odpovědi; neomezuje délku přehrávání videí. Načtení velkých knihoven Plex může trvat déle než krátký limit 10–15 sekund. `-t SECONDS` (nebo `--timeout SECONDS`) vložte před alias přehrávače. Viz [časové limity knihoven Plex](docs/cli.md#plex-libraries-by-name), kde jsou příklady náhledu a postup při vypršení požadavku na přehrávání.

Fronty Plex vyžadují CLI, řadič a přehrávač s implementací `plex_queue`. `plex play --shuffle ID...` jednou promíchá zadaná ID. Fronty se zastaví po poslední položce, pokud je neopakuje existující pravidlo kiosku. Dokud fronta existuje, běžné `next` a `prev` pracují s ní; `plex stop` ji vymaže. Náhled nikdy nepřepíná poskytovatele ani nenahrazuje aktuální přehrávání. Omezení a připravenost popisují [příkazy front Plex](docs/cli.md#ordered-plex-queues). Vyhledávání knihoven navíc vyžaduje aktualizovaný přehrávač a řadič nabízející `plex_library`; používá uložené připojení Plex přehrávače a odmítá nejednoznačné nebo neúplné výběry. Viz [knihovny Plex podle názvu](docs/cli.md#plex-libraries-by-name).

### Uložení profilů místních videí

S aktuálním přehrávačem a řadičem používají místní soubory/složky poskytovatele `offline`:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Nejprve připojte soubory/složky na přehrávači. Uložení zaznamená jejich konfiguraci a vybrané video bez spuštění přehrávání. Export přenáší pouze popisy; média a oprávnění OS/prohlížeče zůstávají na zařízení. Jiná instalace vyžaduje místní přiřazení a načtení neznamená potvrzené přehrávání. Nastavení, možnosti, omezení přenosného formátu a řešení potíží najdete v [profilech místních videí](docs/cli.md#local-videos-profiles).

### Uložení aktuální položky napříč profily

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Oblíbené položky přetrvávají na cílovém přehrávači přes běžné restarty. Výběr načte uloženého poskytovatele/profil a získá nový odkaz; změněný zdroj nebo chybějící položka způsobí chybu bez náhrady jiným kanálem či filmem. CLI, řadič a přehrávač musí podporovat `favorites`; `ott l` ukazuje dostupné operace. Viz [oblíbené položky napříč profily](docs/cli.md#favorites-across-profiles), kde jsou aliasy, omezení, význam potvrzení a řešení potíží.

### Načtení uložené konfigurace

Pojmenované konfigurace ukládejte do soukromé sekce `presets` souboru `~/.config/ottplay-control/cli.json`. Každá obsahuje číslované profily M3U (playlist a počet hodin archivu), přihlašovací údaje Plex a profil M3U, který se má vybrat po načtení. Viz [příklad konfigurace](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI nejprve ověří celou konfiguraci a pak před každou další změnou čeká na potvrzení přehrávače. Zajišťuje načtení poskytovatele, ukládá Plex a sloty M3U a vybírá nakonfigurovaný profil M3U. Chyba sekvenci zastaví a vypíše potvrzené kroky; zápis s nejistým výsledkem se nikdy automaticky neopakuje. Další služba profilů ani restart řadiče nejsou potřeba. Konfiguraci chraňte: obsahuje přihlašovací údaje poskytovatelů.

## Odeslání příkazu

Tento příklad načte token správce z místní soukromé konfigurace, aniž by jej vložil do URL nebo argumentů příkazového řádku:

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

Další příkazy vybírají kanál podle čísla či názvu, náhodný kanál, přepínají poskytovatele, zobrazují zprávu, mění playlist M3U tam, kde je to podporováno, nebo ukončují přehrávač. Dostupnost závisí na platformě přehrávače a aktuálním poskytovateli. Viz [kontrakt API](docs/api.md).

Na přehrávači podporujícím samostatného poskytovatele VPortal vybere `ott a1 provider
vportal` jeho vlastních 15 profilů. Použijte `profile N vportal LINK`, `profile N
name NAME` nebo soukromý soubor `profile-config N FILE` obsahující `name` a `vportal`, pak vyberte nakonfigurovaný slot příkazem `profile N`. Existující profily M3U zůstávají oddělené. `profiles` hlásí metadata bez odhalení odkazů účtu nebo klíčů.

Po spuštění filmu či fronty epizod VPortal uzamkne `ott a1 kiosk on` daný výběr. Přehrávač zachová frontu i pozici přes opětovná načtení a pro obnovu znovu získá URL médií. Pomocí `kiosk off` vyberte jiný titul nebo změňte profily a pak `kiosk on` výběr uzamkněte. Potvrzení kiosku ukazuje název a počet epizod; požadavky poskytovatele a URL streamů zůstávají na přehrávači.

Pro přehrávání určené dětem použijte `ott a1 kiosk on --strict`: místní klepnutí zobrazí spodní lištu videa. Aktualizované přehrávače core dovolují klepnout nebo táhnout po časové ose a posouvat se v aktuální epizodě VPortal/Plex; ostatní místní ovládání zůstává uzamčené. Starší přehrávače, kanálové kiosky a obálky zachytávající gesta časové osy zachovávají lištu pouze pro čtení. Existující zámek si ponechá svůj cíl. Uvolněte jej z řadiče příkazem `ott a1 kiosk off`. Na webových stránkách Android vyžaduje ochrana před systémovým ukončením samostatné připnutí aplikace s PIN; viz [režim kiosku](docs/cli.md#kiosk-mode).

## Vzdálená diagnostika

Začněte [rychlým úvodem do workbenche pouze pro čtení](docs/workbench.md#quick-start) pro `doctor`, `inspect` rozhraní/médií, vyhledávání potvrzení operací, balíčky důkazů, `health`, webové/nativní `media-progress` a offline `report verify`. Používá existující konfiguraci CLI správce a aliasy přehrávačů. Nativní větev slouží samostatně nasazenému agentovi údržby Android; přehrávače Tauri používají webovou inspekci. Zprávy rozlišují postup dekodéru od fyzického ověření obrazovky a zvuku. Viz [řešení potíží workbenche](docs/workbench.md#troubleshooting) při chybějících schopnostech, nedostupných větvích a neúplných důkazech.

Volitelná [diagnostika protokolu 2](docs/remote-diagnostics.md) poskytuje výslovně povolené relace pro každé běhové prostředí, se samostatnými omezenými údaji operátora a omezenou strukturovanou telemetrií. Zapnuté připojení přehrávače poskytuje místní oprávnění bez dalšího dotazu na důvěru; každý záznam má stále omezenou dobu relace. Dotazování diagnostického řízení je nezávislé na existující frontě příkazů.

[Příručka diagnostického CLI a MCP](docs/diagnostics-cli.md) zahrnuje instalaci, údaje operátora, připojení, každý diagnostický příkaz/nástroj, příklady záznamu a oprav, odvolání přístupu běhového prostředí a řešení potíží. `ott diagnostics --help` vypisuje tuto samostatnou rodinu příkazů; běžné aliasy přehrávačů a údaje správce diagnostický přístup neposkytují.

## Balíčky pro nasazení

- [Tablet Android: přísný kiosk Plex a spuštění](docs/android-kiosk-setup.md)
- [Sdílená správa Android USB a obnova spuštění](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS a Windows](docs/deployment.md)
- [Konfigurace, význam doručení a omezení](docs/api.md)
- [Proces vydání a ověření](docs/development.md)

Fronty jsou omezené, standardně vyprší po 60 sekundách a sídlí v paměti. Restart serveru zahodí čekající příkazy. Provozujte jednu repliku; nezávislé repliky nesdílejí fronty. Přehrávač potvrzuje pokus o doručení, nikoli potvrzené přehrávání nebo hardwarovou hlasitost. Síťové opakování se deduplikuje v běžící relaci přehrávače, ne napříč restartem aplikace.

Tento server dodržuje příkazové API volitelného příkladu `local_proxy.py` v [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), se samostatnými údaji správce/zařízení a potvrzovaným doručováním. Starý proxy server zůstává samostatnou součástí. Projekt nezprostředkovává média a nemění síťovou komunikaci HLS, EPG ani poskytovatelů přehrávače.

## Licence

MIT. Autorské údaje zahrnutých nástrojů pro vydávání jsou uvedeny v `NOTICE` a `LICENSES/`.

## Údržba projektu

Viz [požadavky na příspěvky a testy](CONTRIBUTING.md), [zásady hlášení bezpečnostních problémů](SECURITY.md), [návrh zabezpečení](docs/security-design.md) a [důkazy OpenSSF a zbývající kritéria](docs/openssf-evidence.md).

Fronty Plex přijímají v aktuálních přehrávačích až 500 ID. Použijte `ott f10 plex play --shuffle ID...` pro jednorázové promíchání a pak `ott f10 kiosk on --strict` s vybraným poskytovatelem Plex pro opakování a uzamčení fronty. `ott f10 kiosk off` zámek uvolní. Starší přehrávače si zachovávají svůj deklarovaný limit fronty.

## Fuzzing kandidátů na vydání

Veřejní kandidáti na vydání spouštějí omezený nativní fuzzing vstupních hranic popsaných v [příručce fuzzingu](docs/fuzzing.md). Mutační kampaně neběží pro PR, bety ani noční sestavení. Příručka popisuje rozsah, rozpočty, důkazy a reprodukci regresí.

## Komunita a bezpečnostní důkazy

Viz [přispívání](CONTRIBUTING.md), [správa projektu](GOVERNANCE.md), [kodex chování](CODE_OF_CONDUCT.md), [plán rozvoje](ROADMAP.md) a [důkazy OpenSSF](docs/openssf-evidence.md). Rejstřík důkazů rozlišuje podporované bezpečnostní profily, skutečné výsledky testů a zbývající požadavky Silver.
