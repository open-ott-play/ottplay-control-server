<div lang="pl" dir="ltr">

# Niezawodne sterowanie i przekazywanie odtwarzania

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Spis dokumentacji](wiki/Home.md)

Te funkcje wymagają kontrolera i CLI z rewizji zawierającej ten przewodnik. Dokładne porównywanie ustawień i przekazywanie wymagają także zaktualizowanego odtwarzacza. Archiwa starszych wydań i historyczna rewizja CLI z instrukcji instalacji nie uzyskują tych funkcji automatycznie. Instaluj **wszystkie pliki `cli/*.py` razem**, w tym `preset_session.py`; zachowaj prywatną konfigurację.

![Przepływ sterowania, punktu kontrolnego i przekazywania](assets/reliable-control.svg)

## Włącz trwałe przechowywanie wyników z ograniczeniami

Dodaj ten opcjonalny blok do prywatnej konfiguracji serwera, sprawdź ją i uruchom serwer ponownie. Wybierz ścieżkę bezwzględną w lokalnym trwałym systemie plików, do którego konto usługi może zapisywać. W systemie Unix katalog musi być prywatny (0700); istniejące pliki dziennika i blokady muszą być zwykłymi prywatnymi plikami (0600). W Windows ogranicz ACL katalogu do konta usługi. Jeden dziennik należy do jednego procesu.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Serwer tworzy brakujące katalogi w trybie 0700. Limity wynoszą 1–4096 rekordów, 60–86400 sekund i 4 MiB na dysku. Okres przechowywania musi obejmować `command_ttl_seconds`. Pełny dziennik odrzuca nowe operacje z kluczem zamiast usuwać nadal ważne rekordy. Uszkodzone pliki, niezabezpieczony katalog Unix, inny proces zapisujący lub nieudany trwały zapis powodują bezpieczną odmowę. Domyślnie pozostaje dotychczasowa ścieżka poleceń w pamięci.

Dołączona jednostka systemd tworzy `/var/lib/ottplay-control` w trybie 0700. Dla Compose przygotuj katalog hosta należący do UID 65532, ustaw `CONTROL_SERVER_STATE` na jego ścieżkę bezwzględną i połącz `deploy/compose.yml` z `deploy/compose.journal.yml`. Drugi plik dodaje tylko zapisywalny punkt montowania stanu; główny system plików pozostaje tylko do odczytu. Dla Helm przygotuj osobno żądanie trwałego wolumenu i ustaw `journalExistingClaim=CLAIM_NAME`. Jest montowane w `/var/lib/ottplay-control`; użyj pokazanego wyżej prywatnego podkatalogu. Chart zachowuje jedną replikę, `Recreate` oraz `fsGroupChangePolicy: OnRootMismatch`, aby ponowne montowanie nie rozszerzało rekurencyjnie uprawnień już utworzonych prywatnych katalogów. Sprawdź, czy wybrany sterownik pamięci masowej obsługuje tę semantykę własności. Chart nie tworzy ani nie usuwa żądania, a włączenie montowania nie włącza dziennika: nadal kontroluje to prywatna konfiguracja serwera.

Dla `POST /api/requests` dodaj `idempotency_key` obok `action` i `params`. Starsze żądania POST poleceń akceptują zamiast tego `Idempotency-Key`. Klucze zawierają 16–80 liter ASCII, cyfr, podkreśleń lub łączników; dla każdego zamiaru generuj nowy losowy klucz. Ten sam właściciel/klucz z tym samym urządzeniem, akcją i znormalizowaną treścią JSON zwraca pierwotny ID bez ponownego kolejkowania. Zmieniona treść zwraca 409. Klucze są ograniczone do ID operatora (lub administratora), a nie urządzenia. Po upływie retencji klucz może zostać przyjęty jako nowy zamiar: klienci nie mogą ponawiać starego zamiaru poza tym okresem.

`GET /api/operations?device_id=DEVICE` informuje, czy dziennik jest włączony, podaje jego limity i TTL poleceń. Dodaj `&key=KEY`, aby odczytać operację:

- `pending`: przyjęto; brak zachowanej odpowiedzi.
- `responded`: nadeszła zweryfikowana odpowiedź odtwarzacza; `result_status` to `ok`, `rejected` lub `unsupported`. Pierwotna treść wyniku nadal wygasa normalnie.
- `acknowledged`: starszy odbiorca potwierdził polecenie. Nie dowodzi to widocznego odtwarzania ani zapisania zmiany ustawień.
- `unknown`: minął termin lub serwer uruchomiono ponownie przed zachowaniem odpowiedzi. Serwer **nigdy nie wykonuje ponownie** tego polecenia po restarcie.

Powtórzony POST z kluczem zwraca `status: retained`, pierwotny ID i stan. Zwykłe nowe żądania POST zachowują `status: queued`. Rekordy dziennika zawierają wyłącznie właściciela, urządzenie, akcję, losowe ID, skrót treści, stan i terminy. Nie zawierają treści poleceń, URL dostawców, tokenów, zrzutów ekranu ani treści odpowiedzi. Oczekujące polecenia nie są utrwalane. Jest to ograniczona deduplikacja i śledzenie wyników, a nie wykonanie dokładnie raz, trwała kolejka zadań czy wysoka dostępność wielu serwerów.

## Planowanie, zastosowanie, wznowienie i weryfikacja Local/Home

Dotychczasowe zachowanie `ott PLAYER load PRESET` jest zachowane. Dla nowego procesu:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` działa offline: sprawdza lokalny preset i wyświetla sloty ustawień oraz żądany aktywny profil bez ich zawartości. Nie sprawdza odtwarzacza. `verify` wykonuje dokładne porównania tylko do odczytu i sprawdza wybrany profil M3U. Zwraca kod różny od zera, jeśli występuje różnica lub porównanie nie jest obsługiwane.

`apply` wymaga dziennika serwera. Tworzy prywatny punkt kontrolny i porównuje każdego skonfigurowanego dostawcę przed pierwszą zmianą. Każdy zapis porównuje zapisane bajty z oczekiwanymi wartościami przez `settings_compare`; zgodność pomija zapis. Końcowa weryfikacja obejmuje Plex, wszystkie sloty M3U, opcjonalne sloty Stalker i wybrany profil M3U. Porównania zwracają wartość logiczną, nigdy zapisane sekrety.

Domyślny punkt kontrolny znajduje się pod `~/.config/ottplay-control/checkpoints/`. Użyj `--checkpoint /absolute/private/path/session.json` po trybie, aby wybrać inny plik. Punkty kontrolne zawierają urządzenie, nazwę presetu, skrót rewizji serwera/presetu, nazwy zakończonych kroków i jeden oczekujący losowy klucz operacji z terminem. Nie zawierają wartości ustawień. Pliki są zastępowane atomowo po fsync i blokowane przed jednoczesnymi zapisami CLI. Zachowaj dostępny odpowiedni lokalny preset. W Unix katalog musi być prywatny (0700), ze zwykłymi plikami w trybie 0600. W Windows ogranicz odziedziczoną ACL katalogu do użytkownika uruchamiającego CLI; bity trybu Unix nie wymuszają uprawnień dostępu Windows.

`resume` sprawdza ten sam serwer, urządzenie i rewizję presetu, a następnie odczytuje oczekujący wynik. Zachowana odpowiedź umożliwia natychmiastową ponowną kontrolę. Niepewny wynik blokuje wysyłanie zmian do czasu upływu terminu poprzedniego polecenia, limitu czasu żądania i niewielkiego marginesu. Po tym okresie uruchom ponownie `resume`. Ponownie sprawdza zapisane ustawienia nawet dla zakończonych kroków, aby zmian użytkownika nie uznać za udane wcześniejsze zapisy. Nigdy nie powtarza ślepo polecenia względnego. Bezwzględny wybór dostawcy/profilu można powtórzyć po okresie ochronnym.

Nowe `apply` nigdy nie nadpisuje istniejącego punktu kontrolnego. Użyj go ponownie przez `resume` lub wybierz nowy plik dla zmienionego presetu. Różne urządzenia mają osobne punkty kontrolne. Nie istnieje transakcja ani wycofanie obejmujące wszystkie odtwarzacze; wcześniejsze udane zapisy pozostają, gdy późniejszy krok zawiedzie. `verified` potwierdza zapisane wartości odtwarzacza w chwili porównania, a nie udane wczytanie sieciowe czy fizyczne wyjście obrazu/dźwięku.

## Przydzielaj integracjom tylko niezbędne uprawnienia

Dla HA, mostu RF lub agenta używaj [zwykłych operatorów poleceń](security-design.md#scoped-command-operators), zamiast przekazywać `admin_token`. Zezwalaj na dokładne ID urządzeń i operacje takie jak `input.channel_up` oraz `input.channel_down`. Operacje ustawień w tym procesie presetów wymagają jawnych uprawnień `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` i `provider_settings`. Przekazanie wymaga `playback_handoff_export` u źródła i `playback_handoff_import` u celu. Wpis operatora łączy wymienione urządzenia i akcje; stosuj osobne tożsamości, jeśli ta sama operacja nie powinna dotyczyć każdego wymienionego urządzenia. Tokeny i zgoda na diagnostykę pozostają osobnymi uprawnieniami.

Operatorzy nie mogą odpytywać kolejek odtwarzaczy, wysyłać odpowiedzi odtwarzaczy, wyświetlać wszystkich urządzeń, czytać wyjścia diagnostycznego administratora ani wyniku innego operatora. Zawężenie zakresu akcji uniemożliwia też odczyt jej zachowanej operacji. Usunięcie operatora i restart unieważniają jego dane dostępowe. Nie używaj ponownie ID operatora dla innego podmiotu, dopóki istnieją jego stare rekordy dziennika.

## Kontynuowanie na innym odtwarzaczu

```sh
ott handoff living-room bedroom
```

Źródło eksportuje odwołanie do transmisji na żywo/archiwum w wersji 1: dostawcę, nieprzezroczystą tożsamość źródła, nieprzezroczysty odcisk elementu `channel-v1:<8 hex>.<8 hex>` i, dla archiwum, bieżący czas programu. Surowe ID dostawców i URL strumieni pozostają lokalne. Cel musi mieć **to samo skonfigurowane źródło/konto**, odnaleźć jeden jednoznacznie pasujący element we własnym katalogu i uzyskać nowy URL za pomocą własnych lokalnych danych dostępowych. Kontroler nie kopiuje odtwarzalnego URL ani nie zmienia danych dostępowych celu. Źródło odtwarza dalej. Sukces CLI oznacza, że cel wysłał żądanie, a nie że sprawdzono fizyczny ekran lub głośniki.

Wersja 1 obsługuje katalogi na żywo/archiwum `m3u`, `xtream` i `stalker`, gdy odtwarzacz może dostarczyć wymagane tożsamości. Nieznane lub niejednoznaczne elementy, niezgodne źródła i odmowa lokalnej polityki kończą się błędem bez awaryjnego dopasowania po nazwie. VOD/seriale, mapowanie między różnymi kontami, wstrzymywanie źródła i ciągła synchronizacja historii/ulubionych nie należą do tego pierwszego kontraktu.

## Ćwiczenia weryfikacji i odzyskiwania

Uruchom `go test -race ./...`, `go vet ./...` i `python3 -m unittest discover -s tests`. Testy obejmują utracone odpowiedzi po udanym zapisie, dokładne porównanie przed wznowieniem, rozbieżności konfiguracji, terminy nieznanych operacji, dwa procesy zapisujące, uszkodzone dzienniki, odczyty między operatorami i nieprawidłowe dane przekazania. Na odtwarzaczu testowym przerwij dodatkowo CLI po zapisie ustawień, uruchom kontroler ponownie, wznów i zweryfikuj; sprawdź przekazanie między zgodnymi i niezgodnymi źródłami. Obraz/dźwięk potwierdź osobno.

Aby przeprowadzić lokalną weryfikację między repozytoriami z aktualną kopią roboczą FOSS i zainstalowanymi zależnościami programistycznymi, uruchom:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Test dymny buduje ten serwer Go, kompiluje cały projekt TypeScript FOSS i uruchamia jego rzeczywisty transport, obsługę poleceń, tożsamość źródła i dostarczany kodek playlist przeciwko CLI Python. Sprawdza dokładne ustawienia wszystkich trzech obsługiwanych dostawców, niezgodności, przekazanie na żywo z różnymi lokalnymi ID wierszy, przesunięcia wyszukiwania w archiwum, błędy uzyskiwania URL i ścisłą walidację formatu przesyłania. Tylko pamięć, polityka dostawcy i interfejsy hosta odtwarzania są atrapami testowymi; nie sprawdza przeglądarki, fizycznego wyjścia multimediów ani prawdziwego dostępu do kont. Nasłuchuje na loopback, używa prywatnych plików tymczasowych, nie łączy się z URL strumieni i usuwa pliki/serwer po zakończeniu.

</div>
