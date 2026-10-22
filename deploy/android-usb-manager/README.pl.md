# Obraz transportu Android USB

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Ten katalog zawiera szablony wdrożenia i [`Dockerfile`](Dockerfile) kontenera transportowego wspólnego menedżera. Menedżer w Pythonie działa na hoście USB. Obraz dostarcza ADB, Pythona i narzędzia adaptera atomowej konserwacji; nie zawiera drugiego rejestru urządzeń ani demona odzyskiwania. Przygotowanie, polecenia, migrację, ograniczenia odzyskiwania i wycofanie opisuje [instrukcja operatora](../../docs/android-usb-manager.md).

Bez kopii roboczej Git pobierz `ottplay-control-server-deployment.tar.gz` oraz `release-manifest.json` z tego samego [wydania](https://github.com/open-ott-play/ottplay-control-server/releases). Sprawdź skrót archiwum i rozpakuj je do nowego katalogu zgodnie z [Pobieraniem pakietu wdrożeniowego](../../docs/android-usb-manager.md#get-the-deployment-package). Pakiet zawiera tę recepturę i sześć skryptów hosta/zdalnych w `tools/`; zachowaj strukturę katalogów. Starsze pakiety, w tym `v0.1.5-beta.6`, nie zawierają tych skryptów i wymagają odpowiadającego archiwum źródłowego. Pakiet nie zawiera zbudowanego obrazu transportu USB. Artefakt OCI wydania serwera sterującego ma inne przeznaczenie.

## Pochodzenie źródeł i artefaktów

Podstawą jest `python:3.12.14-slim-trixie`, przypięty do `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. Obraz bazowy, instrukcję APT i instalację `websocket-client==1.9.0` przypiętą skrótem sprawdzono w [PR 128, rewizja b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Zachowano tekst instrukcji, aby umożliwić ponowne użycie dostępnej pamięci podręcznej budowania. Opcjonalny pakiet WebSocket obsługuje istniejące ogólne klienty konserwacji; zmigrowane adaptery CDP mogą też korzystać wyłącznie ze standardowej biblioteki Pythona.

Ta receptura usuwa zainstalowany pakiet `pip` po instalacji przypiętego wheel, pomija kod menedżera i odzyskiwania z PR 128, uruchamia ADB na pierwszym planie i wyłącza odziedziczone kontrole zdrowia. Usunięcie instalatora z działającego środowiska zmniejsza dostępną powierzchnię ataku; nie dowodzi braku wykrytych podatności w skanowaniu.

Sprawdzonym artefaktem PR 128 był `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Ma on inny punkt wejścia i niezgodną kontrolę zdrowia opartą na sygnale heartbeat. Jest artefaktem referencyjnym, nie kompilacją tego Dockerfile. Nie przypisuj tej recepturze jego identyfikatora obrazu, pochodzenia źródeł ani wyników skanowania.

Repozytoria APT nie są przypięte do migawek. Przyszłe czyste budowanie może więc wybrać nowsze pakiety systemowe nawet przy tym samym skrócie bazy i wheel Pythona. Kwalifikując kompilację, zapisz dokładną rewizję źródeł, builder/platformę, wynikowy identyfikator obrazu, wersje pakietów i skan. Konfiguracja produkcyjna musi używać tego zakwalifikowanego niezmiennego identyfikatora lub skrótu.

## Budowanie bez zakłócania zajętego hosta USB

Najlepiej budować i skanować na osobnym builderze. Host docelowy potrzebuje tylko zakwalifikowanego obrazu, bez pobierania pakietów i narzędzi kompilacji.

Przed ponownym użyciem przygotowanego lokalnego buildera sprawdź dostępność dokładnego obrazu bazowego oraz buforowanych warstw instalacji APT i wheel. Załadowanie samego końcowego obrazu PR 128 nie potwierdza żadnego z tych warunków. W katalogu głównym repozytorium lub rozpakowanym katalogu wdrożenia celowo odłączona od sieci próba wygląda tak:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` dotyczy kroków budowania; nie gwarantuje ogólnie, że builder nigdy nie połączy się z rejestrem podczas rozpoznawania bazy. Potwierdź lokalne rozpoznawanie bazy przed użyciem procedury na hoście o ograniczonych zasobach. Gdy brakuje warstwy APT lub wheel, przerwij zamiast po cichu zezwalać na sieciowe budowanie na hoście USB.

BuildKit uwzględnia tryb sieci kroku w kluczu pamięci podręcznej. Pamięć utworzona z domyślnym trybem sieci może więc nie zostać użyta w powyższej próbie offline. Użyj jej ponownie dopiero po sprawdzeniu dokładnej bazy i łańcucha instrukcji; przerwij, jeśli którykolwiek krok instalacji pakietów uruchomi się zamiast zgłosić `CACHED`. Końcowe usunięcie `pip` jawnie używa `RUN --network=none`; nie wymaga sieci nawet wtedy, gdy wcześniejsze buforowane kroki używają pierwotnego trybu.

Przed zmianą konfiguracji produkcyjnej sprawdź wersje Pythona i ADB, przeskanuj dokładnie wynikowy obraz, sprawdź transfer plików binarnych i przygotowanie APK, a potem wykonaj opisane w instrukcji testy akceptacyjne tożsamości urządzenia, autoryzacji kluczy, uśpienia i rozruchu. ADB 34 trzeba przetestować ze starszym Androidem; sam numer wersji nie dowodzi zgodności z KitKat ani Fire. Zachowaj bieżący obraz i prywatną kopię wdrożenia, dopóki wycofanie nie przestanie być potrzebne.

## Budżet wykonania

Menedżer hosta, nie ten Dockerfile, narzuca 256 MiB pamięci, 256 MiB pamięci wraz ze swapem i udziały CPU 128, bez twardego limitu CPU lub PID. Wymusza `--no-healthcheck`, `--network none`, główny system plików tylko do odczytu, usunięte capabilities i dokładne mapowania węzłów USB. `/tmp` jest ograniczonym tmpfs korzystającym z tego samego budżetu pamięci. Adapter przygotowania zapisuje bloki 1 MiB i ogranicza pojedynczy ładunek do 128 MiB; operacje dzielą blokadę menedżera.

Docker dokumentuje różnicę między [kontrolą pamięci, swapu i udziałów CPU](https://docs.docker.com/engine/containers/resource_constraints/) a [opcją `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
