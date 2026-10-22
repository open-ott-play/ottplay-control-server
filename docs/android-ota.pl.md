<div lang="pl" dir="ltr">

# Android OTA: zwykłe odtwarzacze i osobny agent z rootem

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Dla **k8a/fire8 i innych odtwarzaczy Capacitor** używaj aktualizatora Wi-Fi zwykłego odtwarzacza, a nie polecenia agenta z rootem:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Pominięcie `--version` wybiera najnowszą opublikowaną betę. Na Androidzie 7+ użyj `full` zamiast `api22`. CLI odnajduje oficjalne wydanie, sprawdza manifest, przygotowuje APK i odpytuje stan przed jednokrotnym żądaniem instalacji. Android może wymagać potwierdzenia na tablecie. Przed konserwacją jawnie wyłącz kiosk i odblokuj chronione ustawienia; polecenie nigdy nie zmienia tych zasad. Zobacz [pełną procedurę CLI](cli.md#capacitor-apk-updates-over-wi-fi).

Wymaga to wcześniej zainstalowanego APK obsługującego `AppUpdate`, Wi-Fi i działającego odtwarzacza połączonego z kontrolerem. Zwykłe kolejne aktualizacje nie wymagają USB, ale nie docierają do tabletu offline lub zatrzymanego WebView. Pierwsza instalacja, brak aktualizatora, uszkodzona aplikacja albo utrata sieci nadal mogą wymagać lokalnego odzyskiwania. Nie oznacza to automatycznego harmonogramu wydań ani uniwersalnego cichego instalatora.

Dalsza część opisuje **innego agenta konserwacji a1 z rootem**, ograniczonego do przygotowanej instalacji Matricom/API19 `play.ott.kitkat`. Nie jest on już zainstalowany ani użyteczny na zwykłych urządzeniach Fire/Capacitor.

Po jednorazowej inicjalizacji USB z rootem niezależny agent Android może odbierać podpisane aktualizacje przez własne wychodzące połączenie HTTPS. Nie potrzebuje sprawnego WebView, aby zaktualizować siebie. Agent 0.1.2 obsługuje również skoordynowany pakiet APK i systemowego pomocnika dotyku, z zapisanymi plikami odzyskiwania i weryfikacją przed zatwierdzeniem.

Przewodnik opisuje całą ścieżkę. Dokładne pola manifestu, limity i wymagania sprzętowe opisuje [dokumentacja agenta natywnego](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Co jest aktualizowane

![Architektura OTA: sprawdzone bajty wydania są podpisywane, publikowane przez bezpośredni HTTPS, weryfikowane przez niezależnego agenta i sprawdzane po instalacji. Wdrożenie WWW jest osobne.](images/ota-architecture.svg)

- **Agent natywny:** `kind: agent` zastępuje własny plik binarny ARMv7. Działa również przy wyłączonym OTT i używaniu innej aplikacji HOME.
- **Tylko APK:** `kind: apk` zastępuje opakowanie, zachowując jego dane. OTT musi być włączone i na pierwszym planie. Wbudowany pomocnik kandydata musi dokładnie odpowiadać zainstalowanemu pomocnikowi systemowemu.
- **APK i pomocnik dotyku:** `kind: bundle` aktualizuje oba elementy jako parę. Starszy agent musi najpierw otrzymać aktualizację samego agenta obsługującą pakiety.
- **Hostowany odtwarzacz:** wdróż zweryfikowany artefakt WWW pod istniejącym origin, następnie przeładuj i sprawdź aktywny odtwarzacz. Aktualizacja APK nie wdraża hostowanego JavaScriptu. Zobacz [proces wydania](release-workflow.md).
- **Kontroler i CLI:** wdróż kontroler i osobno zainstaluj pełny zestaw modułów CLI. Aktualizacja jednego nie zastępuje kodu na tablecie.

Firmware, jądro, systemowy WebView, broker roota i skrypty startowe nie należą do tego formatu OTA. [Dokumentacja natywna](../native/android-agent/README.md#bootstrap) opisuje granice inicjalizacji i odzyskiwania. Obsługiwane operacje OTA nie wymagają sieciowego ADB ani dowolnej zdalnej powłoki.

## Od sprawdzonego wydania do potwierdzonej instalacji

1. **Wybierz zweryfikowane bajty.** Scal sprawdzone zmiany po sukcesie CI. Pobierz wybrany artefakt i sprawdź rewizję źródłową, niezmienne dowody wydania i sumę kontrolną. Wyodrębnij plik binarny `agent` z archiwum natywnego; samo archiwum nie jest instalowalną zawartością agenta.
2. **Przygotuj i podpisz.** Użyj istniejącego prywatnego klucza Ed25519, którego klucz publiczny przypięto podczas inicjalizacji. Chroń klucz i dane dostępowe urządzenia.
3. **Opublikuj i sprawdź transport.** Publikuj tylko zawartość i `signed.json` pod niezmiennymi ścieżkami HTTPS. Oba muszą zwracać bajty bezpośrednio z HTTP 200; aktualizator odrzuca przekierowania. Uruchom polecenie `verify` narzędzia pakującego.
4. **Wyślij jedną aktualizację.** Zapisz ID żądania i sprawdź stan po potwierdzeniu. Utracona lub niepewna odpowiedź nie uzasadnia ślepego powtarzania operacji.
5. **Potwierdź skutek.** Porównaj świeżo odczytane zainstalowane hashe i wersje z przygotowanym wydaniem. Dla aktywnego odtwarzacza sprawdź również cel kiosku, kolejkę i postęp odtwarzania. Fizyczny obraz i dźwięk wymagają obserwacji.

Przykład wydania agenta; podstaw wybraną wersję i rzeczywiste ścieżki:

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

`ENVELOPE_SHA256` to `envelope_sha256` narzędzia pakującego, nie hash zawartości. `accepted` oznacza przyjęcie, nie udaną instalację. Samozastąpienie może pozostawić stare potwierdzenie operacji jako `unknown`; nowy runtime, oczekiwana wersja agenta i zgodny zainstalowany hash stanowią dowód instalacji. [Dokumentacja CLI](cli.md#independent-android-agent) opisuje niezależny routing natywny i obsługę potwierdzeń.

## Odzyskiwanie transakcji APK/pomocnik

![Transakcja APK/pomocnik: kontrola wstępna, zapis poprzedniej pary, zastosowanie, weryfikacja i zatwierdzenie; błędy przywracają poprzednią parę, a niepełne odzyskanie nadal blokuje zmiany aplikacji.](images/ota-transaction.svg)

Pakiet zawiera dokładnie `app.apk` i `ott-touch-guard`. Narzędzie pakujące może utworzyć go bezpośrednio z podpisanego APK opakowania przez `--kind bundle --apk`. Wyodrębnia wbudowanego pomocnika, aby obie dostarczone kopie były zgodne. Zobacz [polecenie pakietu i wymagania](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Przed zmianą plików agent wymaga zgodnej zainstalowanej pary, wystarczającego wolnego miejsca, reagującego OTT na pierwszym planie, partycji systemowej tylko do odczytu i zgłoszonej obsługi obniżenia wersji APK. Przy zablokowanym kiosku sprawdza ten sam ID kanału TV lub uporządkowane ID VPortal; sama równa długość kolejki nie wystarcza. Nieobsługiwane zablokowane kolejki są odrzucane. Celowo wyłączone OTT też jest odrzucane, zachowując obecną aplikację HOME użytkownika.

Pliki odzyskiwania i oczekująca transakcja są utrwalane przed zatrzymaniem aplikacji. Agent czeka na zatrzymanie aplikacji i starego pomocnika, atomowo zastępuje pomocnika, przywraca `/system` do odczytu, instaluje APK i sprawdza wynik. Błąd zastosowania lub weryfikacji uruchamia przywracanie poprzedniej pary. Odzyskiwanie podczas startu przywraca pliki bez uruchamiania OTT ani przejmowania HOME.

Jeśli odzyskiwania nie można ukończyć, `updates.recovery_failed` jest prawdą. Diagnostyka i podpisana naprawa samego agenta pozostają dostępne; zmiany aplikacji i watchdog odtwarzania są blokowane. Nie wolno usuwać zapisanej transakcji, by wymusić instalację. Odzyskanie binariów nie cofa migracji danych aplikacji, a firmware nadal może odrzucić starszą wersję. Zobacz [ograniczenia odzyskiwania](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Wyniki i zapis odbioru

![Wykres walidacji: 488 testów CLI, 576 testów narzędzi wydania i 6 testów pakowania OTA; tablet referencyjny działa z nowym zweryfikowanym agentem, ale fizyczny odbiór APK/pomocnika pozostaje otwarty.](images/ota-validation.svg)

Audyt z 14 października 2026 przeszedł 488 testów CLI (jeden pominięty), 576 testów narzędzi wydania, sześć testów pakowania OTA, natywne kontrole Go race/statyczne i kompilację ARMv7. Liczby opisują rozmiar zestawów, nie pokrycie ani wydajność. Testy wstrzykujące błędy obejmują nieprawidłowe archiwa, przerwane transakcje, częściowe przywrócenie, zmiany celu i blokady odzyskiwania. Niezależny przegląd wykrył i poprawił też niewystarczającą kontrolę tożsamości kolejki oraz rozbieżną walidację APK/pomocnika na hoście i urządzeniu.

Stan sprawdzony 15 października 2026:

- Implementacja OTA jest scalona w [PR kontrolera #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) i dołączona do [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Obecny i poprzedni podpisany pakiet agenta są opublikowane na [hoście dystrybucji OTA](https://android-updates.ottplay.here.now/). Ich bajty zawartości i kopert pobrano i porównano pomyślnie przez bezpośredni HTTPS.
- Tablet referencyjny zaktualizowano z `0.1.1-beta.21` do `0.1.2-beta.4` przez niezależny kanał OTA. Wersja działającego agenta i zainstalowany SHA-256 odpowiadały wydaniu; runtime się zmienił, a ID rozruchu systemu pozostało takie samo. OTT nadal nie miało działającego procesu aplikacji i nie wysłano operacji uruchomienia aplikacji ani wyboru HOME.
- Stan natywny zgłaszał hash systemowego pomocnika dotyku, brak oczekującego pakietu i brak błędu odzyskiwania. Metadane APK były niedostępne, więc obserwacja nie określa wersji ani hasha zainstalowanego APK.
- Skoordynowana transakcja APK/pomocnik ma walidację po stronie hosta; instalacja fizyczna, odzyskiwanie po obniżeniu wersji, obraz i dźwięk nie są jeszcze odebrane na tablecie referencyjnym.

Przy każdym następnym wdrożeniu zachowaj źródło i sumy kontrolne wydania, hash przygotowanej koperty, ID żądania, stan natywny przed/po oraz osobny wynik fizycznego odbioru. Udane pobranie ani potwierdzenie polecenia nie zastępują tych obserwacji.

</div>
