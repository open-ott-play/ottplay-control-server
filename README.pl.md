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

[Dokumentacja / Wiki](docs/wiki/Home.pl.md)

Niewielki serwer do samodzielnego hostowania, dostarczający polecenia zdalne do odtwarzaczy OTT-play. Odtwarzacze wysyłają wychodzące żądania HTTP, więc telewizor nie musi udostępniać portu nasłuchującego. Każdy odtwarzacz ma własną kolejkę i token dostępu; polecenia wysyła się osobnym tokenem administratora.

Opcjonalne [niezawodne sterowanie](docs/reliable-control.md) dodaje dane dostępowe integracji o ograniczonych uprawnieniach, ograniczone trwałe przechowywanie wyników poleceń, wznawialne stosowanie konfiguracji Local/Home z dokładną weryfikacją ustawień oraz jawne przekazanie między odtwarzaniem na żywo i z archiwum. Funkcje wymagają wersji kontrolera i CLI zawierających ten przewodnik. Dokładna weryfikacja i przekazanie wymagają także zaktualizowanego odtwarzacza. Starsza przypięta wersja instalacyjna podana niżej nie zawiera tych funkcji. [Diagram architektury](docs/assets/reliable-control.svg) pokazuje, gdzie pozostają ustawienia, dane dostępowe i stan operacji.

Serwer działa jako jeden przenośny plik wykonywalny Go, z biblioteką DNS w ustalonej wersji do opcjonalnego wykrywania usług. Pakiety obejmują Linux amd64, arm64 i ARMv7; macOS Intel i Apple Silicon; Windows amd64 i arm64. Obrazy kontenerów obsługują Linux amd64 i arm64. Kubernetes i k3s korzystają z dołączonego chartu Helm.

## Uruchomienie serwera

Pobierz odpowiednie archiwum z [wydań](https://github.com/open-ott-play/ottplay-control-server/releases), zweryfikuj je według manifestu wydania i rozpakuj. W systemie Windows używaj `ottplay-control-server.exe` w poniższych poleceniach.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` tworzy losowe, różne dane dostępowe w nowym pliku, do którego w systemach Unix dostęp ma tylko właściciel. Odmawia nadpisania istniejącego pliku lub dowiązania symbolicznego i nigdy nie wypisuje danych dostępowych. Zachowaj prywatność pliku; w Windows ogranicz jego ACL do konta uruchamiającego serwer. `config.example.json` opisuje pola i celowo zawiera nieprawidłowe tokeny przykładowe.

Domyślny adres to `0.0.0.0:8081` (wszystkie interfejsy IPv4, w tym LAN). Łącz telewizory przez adres LAN i port serwera. Istniejące konfiguracje zachowują jawną wartość `listen`; zmień ją na `0.0.0.0:8081` lub użyj `--listen 0.0.0.0:8081`, aby udostępnić instalację wcześniej ograniczoną do loopback. Dodaj **dokładny origin strony** odtwarzacza do `allowed_origins`, na przykład `http://192.168.1.20:8443`. Schemat, nazwa hosta i port muszą się zgadzać; nie dopisuj ścieżki. Pusta lista originów zezwala tylko na klientów innych niż przeglądarki. Zmiany konfiguracji obowiązują po ponownym uruchomieniu. Dane dostępowe urządzenia i administratora nadal są wymagane.

HTTP jest obsługiwany dla starszych przeglądarek TV w zaufanej sieci LAN. Użyj HTTPS, jeśli połączenie przebiega przez niezaufaną sieć: `serve --config config.json --tls-cert server.crt --tls-key server.key`, albo zakończ TLS na odwrotnym proxy. Strony odtwarzacza otwarte przez HTTPS nie mogą łączyć się w przeglądarce ze zwykłym serwerem HTTP. Aplikacje natywne zachowują również zasady transportu swojego systemu operacyjnego; to ustawienie ich nie rozluźnia.

Bezpośredni HTTPS sprawdza dostarczony łańcuch certyfikatów przed otwarciem nasłuchu: klucze RSA muszą mieć co najmniej 2048 bitów, a ECDSA co najmniej 224 bity; obsługiwany jest też Ed25519. Wymień certyfikat ze zbyt krótkim kluczem przed aktualizacją. Zobacz [zasady certyfikatów TLS i ich zakres](docs/security-design.md#direct-https-certificate-policy).

## Podłączenie odtwarzacza

Użyj wydania odtwarzacza zawierającego połączenie z serwerem poleceń. Otwórz **Ustawienia → Zdalne sterowanie** i wprowadź:

1. **Adres serwera:** jego IP lub nazwę hosta, opcjonalnie z portem, albo pełny adres `http://` / `https://`. Sam host domyślnie oznacza HTTP na porcie 8081.
2. **Kod dostępu:** `token` wpisu tego odtwarzacza w `devices`, nigdy `admin_token`.
3. **Połącz:** włącz odpytywanie i sprawdź stan wyświetlany w tym samym oknie.

Odtwarzacz zapisuje połączenie tylko w bieżącej instalacji i wyklucza je z kopii ustawień oraz transferu przez chmurę. Nie wymaga osobnego lokalnego serwera HTTP. Przeglądarki potrzebują dozwolonego originu CORS; spakowane strony TV z `Origin: null` mogą używać jawnego ustawienia `allow_null_origin` dla wyznaczonych tras urządzeń i inicjalizacji. Nie włącza to tras administratora ani operatora diagnostycznego.

## Pilot w terminalu

[Ilustrowany przewodnik Android OTA](docs/android-ota.md) odróżnia zwykłe aktualizacje Capacitor przez Wi-Fi (`ott DEVICE update --variant api22|full`, domyślnie najnowsza beta; `--version` wybiera wydanie) od osobnego agenta z uprawnieniami root. Opisuje podpisane aktualizacje agenta i APK/helpera, odzyskiwanie oraz weryfikację zainstalowanego wyniku.

[Przewodnik Android ADB](docs/android-adb.md) obejmuje dostęp USB przez stację roboczą lub Synology, sterowanie ekranem i klawiaturą, zrzuty ekranu, logi, instalację APK i normalne ponowne uruchamianie, również ograniczenia starszych tabletów.

Opcjonalne [wykrywanie i parowanie DNS-SD](docs/discovery.md) pozwala odtwarzaczom odnaleźć kontroler przez domenę sieciową i otrzymać indywidualny token urządzenia po `ott approve NAME CODE`. DNS zawiera wyłącznie metadane usługi. Istniejące ręczne połączenia działają dalej bez włączania wykrywania.

Używaj CLI w Pythonie do aliasów odtwarzaczy, bieżącej głośności, wyszukiwania kanałów, aktualnego EPG, wyboru dostawcy i konfiguracji obsługiwanych dostawców: [przewodnik CLI](docs/cli.md). Zarówno serwer, jak i odtwarzacz muszą zawierać rozszerzenie żądanie/odpowiedź.

CLI w Pythonie instaluje się z kopii źródeł, niezależnie od archiwum natywnego serwera. [Instrukcje instalacji i konfiguracji](docs/cli.md#installation-and-connection) obejmują macOS/Linux i Windows, ustawienie Python/PATH oraz wymagane moduły CLI. Wskazują sprawdzony commit rozwojowy 0.1.1 z workbenchem i weryfikacją offline; stabilne CLI v0.1.0 i archiwa natywnego serwera nie zawierają tych poleceń. [Aktualizuj lub cofaj CLI](docs/cli.md#update-select-or-roll-back-the-cli) bez zastępowania istniejącej kopii roboczej, programu uruchamiającego ani prywatnej konfiguracji. Lokalne programy uruchamiające mogą używać [resolvera playlist](docs/cli.md#local-playlist-resolver) tylko do odczytu, aby wyszukiwać kanały, bieżące programy i odtwarzalne archiwa bez kontrolera. Po instalacji i konfiguracji:

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

`a1` to przykładowy alias zarejestrowanego odtwarzacza, a nie nazwa hosta komputera. Zobacz [rejestrację i parowanie](docs/cli.md#register-and-connect-a-player), [wszystkie polecenia z przykładami](docs/cli.md#commands), [rozłączanie i cofanie dostępu](docs/cli.md#disconnect-deregister-or-revoke-a-player) oraz [rozwiązywanie problemów](docs/cli.md#troubleshooting).

Dla stale włączonego odtwarzacza używaj [standby i go](docs/cli.md#stop-playback-while-keeping-the-player-online), aby zatrzymać odtwarzanie i później je wznowić bez zamykania aplikacji. `stop` pozostaje aliasem `standby`; `start` i `wake` są aliasami `go`. Zdalne sterowanie musi pozostać połączone, więc czuwanie nie oznacza braku ruchu sieciowego. `ott a1` pokazuje operacje ogłaszane przez odtwarzacz. Przyjęcie polecenia nie potwierdza zakończenia wyłączania ani uruchamiania odtwarzania.

[Polecenia proporcji obrazu](docs/cli.md#aspect-ratio-fit-and-fill) wymagają aktualnych CLI i kontrolera oraz odtwarzacza ogłaszającego funkcję `aspect`. Zmiana jest stosowana po potwierdzeniu; odczytaj `aspect` ponownie, aby sprawdzić tryb i jego zapisanie. Fill proporcjonalnie przycina, zamiast rozciągać obraz.

Zdalne [zrzuty ekranu](docs/cli.md#remote-screenshots) wymagają aktualnych CLI i kontrolera oraz odtwarzacza z obsługiwanym adapterem przechwytywania. Włączenie połączenia Zdalne sterowanie upoważnia ten kontroler do zrzutów i diagnostyki; natywne zrzuty nie potrzebują osobnego przełącznika uprawnień. Przeglądarki nadal wymagają lokalnego wyboru źródła. Obrazy mogą zawierać ustawienia, ekrany PIN i widoczne dane dostępowe, dlatego łącz się tylko z zaufanym kontrolerem. `ott a1` informuje o dostępności i wymaganym wyborze źródła. Zrzuty ekranu są niezależne od zbierania telemetrii strukturalnej.

Aby przetasować wszystkie filmy VPortal odpowiadające tytułowi i odtwarzać je w pętli:

```sh
ott l vpr "wedding"
```

`vpr` tasuje cały wybór raz, a następnie powtarza tę kolejkę. Uruchom go ponownie, aby przetasować jeszcze raz. `vp` odtwarza wyniki w kolejności katalogu; `vp --list` wyświetla je bez zmiany odtwarzania. Zobacz [polecenia VPortal](docs/cli.md#commands).

Dla jawnej sekwencji Plex korzystającej z zapisanej konfiguracji Plex odtwarzacza:

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

CLI domyślnie czeka do 45 sekund. Zapis `ott -t 45 l plex shuffle "Tom and Jerry"` jawnie ustawia ten sam limit odpowiedzi; nie ogranicza czasu odtwarzania filmów. Zebranie dużych bibliotek Plex może potrwać dłużej niż krótki limit 10–15 sekund. Umieść `-t SECONDS` (lub `--timeout SECONDS`) przed aliasem odtwarzacza. Zobacz [limity czasu bibliotek Plex](docs/cli.md#plex-libraries-by-name), gdzie są przykłady podglądu i wskazówki na wypadek przekroczenia czasu żądania odtwarzania.

Kolejki Plex wymagają CLI, kontrolera i odtwarzacza implementujących `plex_queue`. `plex play --shuffle ID...` tasuje podane ID jeden raz. Kolejki kończą się po ostatnim elemencie, chyba że istniejąca polityka kiosku je powtarza. Dopóki kolejka istnieje, zwykłe `next` i `prev` działają na niej; `plex stop` ją czyści. Podgląd nigdy nie przełącza dostawcy ani nie zastępuje bieżącego odtwarzania. Limity i gotowość opisano w [poleceniach kolejek Plex](docs/cli.md#ordered-plex-queues). Wyszukiwanie bibliotek wymaga dodatkowo aktualnego odtwarzacza i kontrolera ogłaszających `plex_library`; korzysta z zapisanego połączenia Plex odtwarzacza i odrzuca wybory niejednoznaczne lub niepełne. Zobacz [biblioteki Plex według nazwy](docs/cli.md#plex-libraries-by-name).

### Zapisywanie profili lokalnych filmów

Przy aktualnym odtwarzaczu i kontrolerze lokalne pliki/foldery korzystają z dostawcy `offline`:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Najpierw podłącz pliki/foldery w odtwarzaczu. Zapis zachowuje ich konfigurację i wybrany film bez rozpoczynania odtwarzania. Eksport przenosi tylko opisy; media i uprawnienia systemu/przeglądarki pozostają na urządzeniu. Inna instalacja wymaga lokalnego powiązania, a wczytanie nie oznacza potwierdzonego odtwarzania. Konfigurację, możliwości, ograniczenia formatu przenośnego i rozwiązywanie problemów opisano w [profilach lokalnych filmów](docs/cli.md#local-videos-profiles).

### Zapisywanie bieżącego elementu między profilami

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Ulubione pozostają na docelowym odtwarzaczu po normalnych restartach. Wybranie pozycji ładuje zapisanego dostawcę/profil i rozwiązuje nowy link; zmienione źródło lub brak elementu powoduje błąd bez podstawienia innego kanału czy filmu. CLI, kontroler i odtwarzacz muszą obsługiwać `favorites`; `ott l` pokazuje dostępne operacje. Zobacz [ulubione między profilami](docs/cli.md#favorites-across-profiles), w tym aliasy, limity, znaczenie potwierdzeń i rozwiązywanie problemów.

### Wczytywanie zapisanej konfiguracji

Przechowuj nazwane konfiguracje w prywatnej sekcji `presets` pliku `~/.config/ottplay-control/cli.json`. Każda zawiera numerowane profile M3U (playlistę i godziny archiwum), dane dostępowe Plex oraz profil M3U wybierany po zakończeniu wczytywania. Zobacz [przykład konfiguracji](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI najpierw sprawdza całą konfigurację, a następnie czeka na potwierdzenie odtwarzacza przed wysłaniem kolejnej zmiany. Obsługuje ładowanie dostawcy, zapisuje Plex i sloty M3U, po czym wybiera skonfigurowany profil M3U. Błąd zatrzymuje sekwencję i zgłasza potwierdzone kroki; zapis o niepewnym wyniku nigdy nie jest automatycznie powtarzany. Nie potrzeba dodatkowej usługi profili ani restartu kontrolera. Zachowaj prywatność konfiguracji: zawiera dane dostępowe dostawców.

## Wysłanie polecenia

Ten przykład odczytuje token administratora z lokalnej prywatnej konfiguracji, nie umieszczając go w URL ani argumentach wiersza poleceń:

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

Inne polecenia wybierają kanał według numeru lub nazwy, losują kanał, zmieniają dostawcę, pokazują komunikat, modyfikują playlistę M3U tam, gdzie jest to obsługiwane, albo zamykają odtwarzacz. Dostępność zależy od platformy odtwarzacza i bieżącego dostawcy. Zobacz [kontrakt API](docs/api.md).

W odtwarzaczu obsługującym samodzielnego dostawcę VPortal polecenie `ott a1 provider
vportal` wybiera jego własne 15 profili. Użyj `profile N vportal LINK`, `profile N
name NAME` lub prywatnego pliku `profile-config N FILE` zawierającego `name` i `vportal`, a potem wybierz skonfigurowany slot przez `profile N`. Istniejące profile M3U pozostają osobne. `profiles` zgłasza metadane bez ujawniania linków konta czy kluczy.

Po uruchomieniu filmu lub kolejki odcinków VPortal `ott a1 kiosk on` blokuje ten wybór. Odtwarzacz zachowuje kolejkę i pozycję po przeładowaniach oraz ponownie rozwiązuje URL mediów w celu odzyskania odtwarzania. Użyj `kiosk off`, aby wybrać inny tytuł lub zmienić profile, a następnie `kiosk on`, aby zablokować. Potwierdzenie kiosku pokazuje tytuł i liczbę odcinków; żądania dostawcy i URL strumieni pozostają na odtwarzaczu.

Użyj `ott a1 kiosk on --strict` do odtwarzania dla dzieci: lokalne dotknięcie pokazuje dolny pasek filmu. Zaktualizowane odtwarzacze core pozwalają dotykać lub przeciągać jego oś czasu, aby przewijać bieżący odcinek VPortal/Plex; inne lokalne elementy sterowania pozostają zablokowane. Starsze odtwarzacze, kioski kanałów i nakładki przechwytujące gesty osi czasu zachowują pasek tylko do odczytu. Istniejąca blokada zachowuje swój cel. Zwolnij ją z kontrolera przez `ott a1 kiosk off`. Na stronach Android ochrona przed wyjściem przez system wymaga osobnego przypięcia aplikacji kodem PIN; zobacz [tryb kiosku](docs/cli.md#kiosk-mode).

## Zdalna diagnostyka

Zacznij od [szybkiego startu workbencha tylko do odczytu](docs/workbench.md#quick-start) dla `doctor`, `inspect` interfejsu/mediów, wyszukiwania potwierdzeń operacji, pakietów dowodów, `health`, webowego/natywnego `media-progress` oraz offline `report verify`. Korzysta z istniejącej konfiguracji CLI administratora i aliasów odtwarzaczy. Ścieżka natywna służy osobno wdrożonemu agentowi konserwacyjnemu Android; odtwarzacze Tauri używają inspekcji webowej. Raporty odróżniają postęp dekodera od fizycznej weryfikacji ekranu/dźwięku. Zobacz [rozwiązywanie problemów workbencha](docs/workbench.md#troubleshooting) przy brakujących możliwościach, niedostępnych ścieżkach i niepełnych dowodach.

Opcjonalna [diagnostyka protokołu 2](docs/remote-diagnostics.md) udostępnia jawnie włączane sesje dla każdego środowiska wykonawczego, z osobnymi ograniczonymi danymi operatora i ograniczoną telemetrią strukturalną. Włączone połączenie odtwarzacza zapewnia lokalną autoryzację bez kolejnego pytania o zaufanie; każda rejestracja nadal ma ograniczony czas sesji. Odpytywanie sterowania diagnostycznego jest niezależne od istniejącej kolejki poleceń.

[Przewodnik diagnostycznego CLI i MCP](docs/diagnostics-cli.md) opisuje instalację, dane operatora, połączenie, każde polecenie/narzędzie diagnostyczne, przykłady rejestracji i napraw, cofanie dostępu środowiska wykonawczego oraz rozwiązywanie problemów. `ott diagnostics --help` wyświetla tę osobną rodzinę poleceń; zwykłe aliasy odtwarzaczy i dane administratora nie przyznają dostępu diagnostycznego.

## Pakiety wdrożeniowe

- [Tablet Android: ścisły kiosk Plex i uruchamianie](docs/android-kiosk-setup.md)
- [Współdzielone zarządzanie Android USB i odzyskiwanie rozruchu](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS i Windows](docs/deployment.md)
- [Konfiguracja, semantyka dostarczania i limity](docs/api.md)
- [Proces wydawania i walidacji](docs/development.md)

Kolejki mają limity, domyślnie wygasają po 60 sekundach i są przechowywane w pamięci. Restart serwera usuwa oczekujące polecenia. Uruchamiaj jedną replikę; niezależne repliki nie współdzielą kolejek. Odtwarzacz potwierdza próbę dostarczenia, a nie faktyczne odtwarzanie lub głośność sprzętową. Powtórzenia sieciowe są deduplikowane w działającej sesji odtwarzacza, nie między restartami aplikacji.

Serwer stosuje API poleceń opcjonalnego przykładu `local_proxy.py` w [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), z osobnymi danymi administratora/urządzenia i potwierdzanym dostarczaniem. Stare proxy pozostaje osobnym komponentem. Projekt nie pośredniczy w przesyłaniu mediów ani nie zmienia komunikacji HLS, EPG czy dostawców odtwarzacza.

## Licencja

MIT. Informacje o autorach dołączonych narzędzi wydawniczych zapisano w `NOTICE` i `LICENSES/`.

## Utrzymanie projektu

Zobacz [wymagania dotyczące wkładu i testów](CONTRIBUTING.md), [zasady zgłaszania problemów bezpieczeństwa](SECURITY.md), [projekt bezpieczeństwa](docs/security-design.md) i [dowody OpenSSF oraz pozostałe kryteria](docs/openssf-evidence.md).

Kolejki Plex przyjmują do 500 ID w obecnych odtwarzaczach. Użyj `ott f10 plex play --shuffle ID...`, aby przetasować raz, a następnie `ott f10 kiosk on --strict` przy wybranym dostawcy Plex, aby zapętlić i zablokować kolejkę. `ott f10 kiosk off` zwalnia blokadę. Starsze odtwarzacze zachowują zgłaszany limit kolejki.

## Fuzzing kandydatów do wydania

Publiczni kandydaci do wydania uruchamiają ograniczony natywny fuzzing granic wejścia opisanych w [przewodniku fuzzingu](docs/fuzzing.md). Kampanie mutacyjne nie działają dla PR, wersji beta ani kompilacji nocnych. Przewodnik obejmuje zakres, budżety, dowody i odtwarzanie regresji.

## Społeczność i dowody bezpieczeństwa

Zobacz [współtworzenie](CONTRIBUTING.md), [zarządzanie](GOVERNANCE.md), [kodeks postępowania](CODE_OF_CONDUCT.md), [plan rozwoju](ROADMAP.md) i [dowody OpenSSF](docs/openssf-evidence.md). Indeks dowodów rozróżnia obsługiwane profile bezpieczeństwa, rzeczywiste wyniki testów i pozostałe wymagania Silver.
