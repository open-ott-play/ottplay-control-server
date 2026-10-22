# Obraz přenosové vrstvy Android USB

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Tento adresář obsahuje šablony nasazení a [`Dockerfile`](Dockerfile) přenosového kontejneru sdíleného správce. Správce v Pythonu běží na hostiteli USB. Obraz dodává ADB, Python a nástroje adaptéru atomické údržby; neobsahuje druhý registr zařízení ani démona obnovy. Zprovoznění, příkazy, migraci, omezení obnovy a návrat popisuje [příručka operátora](../../docs/android-usb-manager.md).

Bez pracovní kopie Gitu stáhněte `ottplay-control-server-deployment.tar.gz` a `release-manifest.json` ze stejného [vydání](https://github.com/open-ott-play/ottplay-control-server/releases). Ověřte otisk archivu a rozbalte jej do nového adresáře podle [Získání balíčku nasazení](../../docs/android-usb-manager.md#get-the-deployment-package). Balíček obsahuje tento předpis a šest hostitelských/vzdálených skriptů v `tools/`; zachovejte strukturu adresářů. Starší balíčky včetně `v0.1.5-beta.6` tyto skripty nemají a vyžadují odpovídající zdrojový archiv. Balíček neobsahuje sestavený obraz přenosové vrstvy USB. Vydaný artefakt OCI řídicího serveru slouží jinému účelu.

## Původ zdrojů a artefaktů

Základem je `python:3.12.14-slim-trixie`, připnutý k `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. Základ, instrukce APT a instalace `websocket-client==1.9.0` uzamčená hashem byly zkontrolovány v [PR 128, revize b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Text instrukcí zůstává zachován pro využití dostupné mezipaměti sestavení. Volitelný balíček WebSocket podporuje stávající obecné klienty údržby; převedené adaptéry CDP mohou využít také pouze standardní knihovnu Pythonu.

Tento předpis po instalaci uzamčeného wheel odstraní nainstalovaný balíček `pip`, vynechá kód správce a obnovy z PR 128, spustí ADB v popředí a vypne zděděné kontroly zdraví. Odstranění instalátoru z běhového prostředí snižuje dostupnou plochu útoku; nedokládá sken zranitelností bez nálezů.

Zkontrolovaný artefakt PR 128 byl `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Obsahuje jiný vstupní bod a nekompatibilní kontrolu zdraví pomocí heartbeat. Jde o referenční artefakt, nikoli sestavení tohoto Dockerfile. Nepřipisujte tomuto předpisu jeho ID obrazu, původ zdrojů ani výsledky skenu.

Repozitáře APT nejsou připnuty ke snímkům. Budoucí čisté sestavení proto může vybrat novější systémové balíčky i při shodném otisku základu a hashi Python wheel. Při kvalifikaci sestavení zaznamenejte přesnou zdrojovou revizi, builder/platformu, výsledné ID obrazu, verze balíčků a sken. Produkční konfigurace musí používat toto kvalifikované neměnné ID nebo otisk.

## Sestavení bez narušení vytíženého hostitele USB

Upřednostněte sestavení a sken na samostatném builderu. Cílový hostitel potřebuje jen kvalifikovaný obraz, nikoli stahování balíčků nebo sestavovací nástroje.

Před opětovným využitím připraveného místního builderu ověřte dostupnost přesného základního obrazu a mezipaměť instalačních vrstev APT a wheel. Načtení pouze konečného obrazu PR 128 nedokládá ani jednu podmínku. Z kořene repozitáře nebo rozbaleného adresáře nasazení lze záměrně offline pokus provést takto:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` platí pro kroky sestavení; obecně nezaručuje, že builder při hledání základu nikdy nekontaktuje registr. Před použitím na omezeném hostiteli potvrďte místní rozlišení základu. Chybí-li vrstva APT nebo wheel, pokus zastavte místo tichého povolení síťového sestavení na hostiteli USB.

BuildKit zahrnuje síťový režim kroku do klíče mezipaměti. Mezipaměť vytvořená s výchozím síťovým režimem proto při uvedeném offline pokusu nemusí být použitelná. Použijte ji až po kontrole přesného základu a řetězce instrukcí a zastavte se, pokud se instalační krok spustí místo hlášení `CACHED`. Konečné odstranění `pip` výslovně používá `RUN --network=none`; síť nepotřebuje, i když předchozí kroky v mezipaměti používají původní režim.

Před změnou produkční konfigurace ověřte verze Pythonu a ADB, proskenujte přesný výsledný obraz, ověřte binární přenos souborů a přípravu APK a následně proveďte přejímací kontroly identity zařízení, autorizace klíčů, uspání a startu z příručky. ADB 34 je nutné se starším Androidem otestovat; samotné číslo verze nedokládá kompatibilitu s KitKat či Fire. Uchovejte současný obraz a soukromou zálohu nasazení, dokud již návrat nebude potřeba.

## Běhový rozpočet

Hostitelský správce, nikoli tento Dockerfile, nastavuje 256 MiB paměti, 256 MiB paměti spolu se swapem a CPU shares 128, bez tvrdé kvóty CPU či PID. Vynucuje `--no-healthcheck`, `--network none`, kořenový systém souborů jen pro čtení, odebrané capabilities a přesná mapování uzlů USB. `/tmp` je omezený tmpfs čerpající stejný paměťový rozpočet. Přípravný adaptér zapisuje bloky po 1 MiB a omezuje jeden přenášený obsah na 128 MiB; operace sdílejí zámek správce.

Docker dokumentuje rozdíl mezi [řízeními paměti, swapu a podílů CPU](https://docs.docker.com/engine/containers/resource_constraints/) a [volbou `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
