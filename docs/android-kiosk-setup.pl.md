# Tablet Android: ścisły kiosk Plex i uruchamianie

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Instrukcja ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Menedżer USB](android-usb-manager.md)

Dedykowany tablet wymaga trzech osobno sprawdzonych ustawień: kolejki treści, ścisłych zasad kiosku OTT i uruchamiania Androida. Instalacja APK lub test wyłączenia/wznowienia ekranu nie potwierdza uruchomienia po restarcie systemu.

## Identyfikacja i instalacja

1. Zapisz fizyczny tablet, alias/UUID OTT, alias menedżera USB, numer seryjny, model, Android ID i SDK. Aliasy OTT i USB mogą się różnić; nie ustalaj tożsamości na podstawie aliasu i nie używaj tokenu innego tabletu.
2. Użyj oficjalnego wariantu APK zgodnego z SDK, sprawdź SHA-256 wydania i tożsamość pakietu/podpisu, zainstaluj przez `install -r`. Zachowaj dane odtwarzacza. Używaj [wspólnego menedżera](android-usb-manager.md), gdy zarządza USB; nie uruchamiaj konkurencyjnego serwera ADB ani poleceń `adb` bez wskazania urządzenia.
3. Sparuj odtwarzacz z jego własnymi danymi kontrolera i sprawdź `ott TABLET`. Załaduj właściwy nazwany preset i wykonaj `ott TABLET load PRESET verify`. Presety sieci domowej i localhost komputera programisty nie są zamienne.

## Sprawdzenie Plex z tabletu

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Sprawdź ID biblioteki, tytuł i liczbę elementów. Udane żądanie z komputera administratora nie potwierdza połączenia tabletu. Przy błędach sprawdź na tablecie zapisany adres, routing, obsługę TLS i dane uwierzytelniające. Preferuj prawidłowy adres HTTPS serwera. Jeżeli stary firmware wymaga HTTP w prywatnej LAN, ogranicz zmianę do tej instalacji, odnotuj niezaszyfrowany przesył tokenów i multimediów, nie zmieniaj wspólnego presetu ani globalnie nie wyłączaj weryfikacji certyfikatów. Nigdy nie publikuj tokenów ani prywatnych adresów multimediów w diagnostyce lub dokumentacji.

Zmiana adresu serwera Plex usuwa wybrane korzenie bibliotek. Wybierz dostawcę **Plex**, otwórz **Plex settings → Folders**, zaznacz wiersz właściwej biblioteki (nie tylko **Browse folders**), wybierz **Ok**, następnie **Save and open library**. W odtwarzaczu `1.1.58-beta.19` same dane serwera mogą otworzyć wybór folderów, gdy `ready` pozostaje false; zakończ wybór przed poleceniami kolejki. Nie przełączaj na M3U tylko dla `ready: true`: kolejka Plex może tam grać, lecz `kiosk on --strict` może zapisać oczekującą zasadę M3U bez powiązanych mediów. Nie blokuje ona ani nie przywraca kolejki Plex.

## Uruchomienie i zablokowanie kolejki

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

Dla kolejki uporządkowanej użyj `plex library 'LIBRARY NAME'` zamiast `plex shuffle`. Shuffle raz wybiera kolejność; kiosk ją powtarza. Potwierdź właściwe elementy, `enabled: true`, `strict: true` i zasadę powiązaną z właściwą kolejką Plex. Zasada `waiting` bez kanału lub mediów nie jest zaakceptowanym kioskiem. Sprawdź co najmniej dwie próbki z rosnącą pozycją/licznikiem klatek i obejrzyj ekran. Potwierdzenie lub etykieta `playing` bez postępu nie wystarcza. Przetestuj naturalne przejście po końcu elementu; jeśli pełna pętla jest niepraktyczna, jawnie zapisz ten limit.

Ścisły kiosk steruje interfejsem odtwarzacza. Nie oznacza rejestracji Android device owner ani dowodu blokady Home, Recents, powiadomień i Settings. Zachowaj launcher systemowy oraz drogę konserwacji pod nadzorem.

## Osobne włączenie uruchamiania

Zbuduj i sprawdź [OTT Home 0.2.0](../native/android-home/README.md), zachowując obecny klucz podpisu dla aktualizacji. Zainstaluj `play.ott.home` obok `play.ott.foss`. Otwórz `play.ott.home/.StartupActivity` i włącz **Start OTT after reboot**. Opcja jest domyślnie wyłączona. Nie zmienia oficjalnego pliku odtwarzacza, podpisu OTA, profili ani przypisania przycisku Home.

Jeśli firmware udostępnia wybór Home, można też wybrać **OTT Home → Always**. Gdy wyboru brak (w tym na sprawdzonym starym Fire), opcja startu działa niezależnie. Wcześniej skonfigurowane instalacje domyślnego Home zachowują działanie. Wyłącz oba mechanizmy, aby całkowicie wycofać zmianę.

`recover_on_boot` wspólnego menedżera USB jest trzecim, niezależnym mechanizmem: wymaga NAS i kabla USB, obsługuje tylko nowy rozruch i nie wznawia stale aplikacji po awarii. Nie zastępuje autonomicznego uruchamiania. Podczas jego testu pozostaw odzyskiwanie wyłączone, aby nie maskowało braku uruchomienia aplikacji towarzyszącej. Respektuj bezpieczne blokady ekranu; odblokuj normalnie na żądanie.

## Odbiór i przekazanie

Zapisuj dowody prywatnie, bez danych uwierzytelniających w publicznych potwierdzeniach:

- Wersje APK, skróty, mapowanie tożsamości fizycznej/OTT/USB i wybrany preset.
- Biblioteka Plex, liczba/kolejność elementów, ścisła zasada i początkowe odtwarzanie z postępem.
- Boot ID przed prawdziwym restartem systemu i po nim; następnie `sys.boot_completed=1`.
- Brak ręcznego uruchamiania odtwarzacza i odzyskiwania USB między restartem a obserwacją.
- Aktywność OTT na pierwszym planie, ponowne połączenie kontrolera, przywrócona kolejka/ścisła zasada i postęp odtwarzania. Wynik `requested` odbiornika rozruchu oznacza tylko wysłanie.
- Osobny test przycisku zasilania: ekran wyłączony, dźwięk zatrzymany, normalne odblokowanie i wznowienie.
- Dokładne ograniczenia: wymagane bezpieczne odblokowanie, odzyskiwanie z USB, niesprawdzone fizyczne wyjścia, błędy strumienia lub brak pamięci. Nie nazywaj takich przypadków w pełni zaakceptowanymi.

Przed przekazaniem kolejnego tabletu użyj tej samej listy zamiast kopiowania sukcesu poprzedniego. Starsze Fire mogą wymagać `hls.js` dla M3U, bo natywne HLS producenta może ulec awarii; nie potwierdza to stabilności Plex.

## Konserwacja i wycofanie

```sh
ott TABLET kiosk off
```

Otwórz **OTT Startup**, wyłącz **Start OTT after reboot** i przywróć pierwotny Home, jeśli zmieniono wybór. W razie potrzeby odinstaluj tylko `play.ott.home` przez sprawdzony transport USB; zachowaj `play.ott.foss` i dane. Wyłączenie uruchamiania nie zmienia kolejki ani automatycznie nie wyłącza kiosku.
