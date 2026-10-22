# Tablette Android : kiosque Plex strict et démarrage

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Guide ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Gestionnaire USB](android-usb-manager.md)

Une tablette dédiée nécessite trois réglages vérifiés séparément : sa file de contenus, la politique de kiosque strict d’OTT et le démarrage Android. Installer un APK ou vérifier l’extinction/reprise de l’écran ne prouve pas le démarrage après un redémarrage du système.

## Identifier et installer

1. Notez la tablette physique, l’alias/UUID OTT, l’alias du gestionnaire USB, le numéro de série, le modèle, l’ID Android et le SDK. Les alias OTT et USB peuvent différer ; ne déduisez pas l’identité d’un alias et ne réutilisez pas le jeton d’une autre tablette.
2. Utilisez la variante APK officielle compatible avec ce SDK, vérifiez son SHA-256 de publication et l’identité du paquet/de sa signature, puis installez avec `install -r`. Préservez les données du lecteur. Utilisez le [gestionnaire partagé](android-usb-manager.md) s’il possède l’USB ; ne lancez ni serveur ADB concurrent ni commandes `adb` sans ciblage explicite.
3. Appairez le lecteur avec ses propres identifiants de contrôleur et vérifiez `ott TABLET`. Chargez le préréglage nommé voulu et exécutez `ott TABLET load PRESET verify`. Les préréglages du réseau domestique et le préréglage localhost d’un poste de développement ne sont pas interchangeables.

## Vérifier Plex depuis la tablette

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Vérifiez l’ID, le titre et le nombre d’éléments de la bibliothèque. Une requête réussie depuis l’ordinateur d’administration ne prouve pas la connectivité de la tablette. En cas d’échec, contrôlez sur la tablette l’adresse enregistrée, le routage, TLS et les identifiants. Préférez le point d’accès HTTPS valide du serveur. Si un ancien micrologiciel exige HTTP sur le LAN privé, limitez ce choix à cette installation, notez que jetons et médias transitent sans chiffrement et ne modifiez ni préréglage partagé ni vérification globale des certificats. Ne publiez jamais de jetons ou d’URL de médias privés dans les diagnostics ou la documentation.

Sur les lecteurs récents annonçant `categories` dans `caps`, Plex ouvre toutes les vidéos sans imposer de sélection de dossiers. Utilisez `ott TABLET categories` et `ott TABLET category all` ; voir les [catégories multimédias](cli.md#media-categories-and-stable-video-selection). Les étapes de sélection des dossiers ci-dessous concernent la version antérieure explicitement indiquée.

Changer l’adresse du serveur Plex efface les racines de bibliothèque sélectionnées. Choisissez le fournisseur **Plex**, ouvrez **Plex settings → Folders**, cochez la ligne de bibliothèque voulue (pas seulement **Browse folders**), puis **Ok** et **Save and open library**. Dans le lecteur `1.1.58-beta.19`, les identifiants seuls peuvent ouvrir le sélecteur de dossiers tandis que `ready` reste false ; terminez cette sélection avant les commandes de file. Ne choisissez pas M3U uniquement pour obtenir `ready: true` : une file Plex peut y jouer, mais `kiosk on --strict` peut enregistrer une politique M3U en attente sans média lié. Cela ne verrouille ni ne restaure la file Plex.

## Démarrer et verrouiller la file

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

Pour une file ordonnée, utilisez `plex library 'LIBRARY NAME'` au lieu de `plex shuffle`. Shuffle choisit un ordre une fois ; le kiosque le répète. Confirmez les éléments voulus, `enabled: true`, `strict: true` et une politique liée à la bonne file Plex. Une politique `waiting` sans chaîne ni média ne constitue pas un kiosque accepté. Observez au moins deux relevés avec progression de position/compteur d’images et regardez l’écran. Un accusé ou l’étiquette `playing` sans progression ne suffit pas. Testez une transition naturelle en fin d’élément ; si un cycle complet est impraticable, consignez explicitement cette limite.

Le kiosque strict contrôle l’interface du lecteur. Ce n’est ni un enrôlement Android device owner ni la preuve que Home, Recents, les notifications et Settings sont bloqués. Conservez le lanceur système et un accès de maintenance supervisé.

## Activer le démarrage séparément

Construisez et vérifiez [OTT Home 0.2.0](../native/android-home/README.md), en conservant sa clé de signature pour les mises à jour. Installez `play.ott.home` à côté de `play.ott.foss`. Ouvrez `play.ott.home/.StartupActivity` et activez **Start OTT after reboot**. Cette option est désactivée par défaut. Elle ne modifie ni le binaire officiel du lecteur, ni sa signature OTA, ni les profils, ni l’affectation du bouton Home.

Si le micrologiciel propose un sélecteur Home, vous pouvez aussi choisir **OTT Home → Always**. Sans sélecteur (y compris sur l’ancien micrologiciel Fire testé), l’activation du démarrage fonctionne indépendamment. Les installations déjà configurées comme Home par défaut conservent leur comportement. Désactivez les deux mécanismes pour un retour arrière complet.

`recover_on_boot` du gestionnaire USB partagé est un troisième mécanisme indépendant : il exige le NAS et le câble USB, ne traite qu’un nouveau démarrage et ne relance pas continuellement une application plantée. Il ne remplace pas un démarrage autonome. Lors du test autonome, laissez cette récupération désactivée pour ne pas masquer l’absence de lancement par l’application compagnon. Respectez les écrans de verrouillage sécurisés et déverrouillez normalement si demandé.

## Acceptation et remise

Conservez les preuves en privé, sans identifiants dans les reçus publics :

- Versions APK, empreintes, correspondance des identités physique/OTT/USB et préréglage choisi.
- Bibliothèque Plex, nombre/ordre des éléments, politique stricte et progression initiale de lecture.
- ID de démarrage avant et après un vrai redémarrage du système ; `sys.boot_completed=1` ensuite.
- Aucun lancement manuel du lecteur ni récupération USB entre redémarrage et observation.
- Activité OTT au premier plan, reconnexion au contrôleur, file/politique stricte restaurée et lecture qui progresse. Le résultat `requested` du récepteur de démarrage indique seulement l’envoi de la demande.
- Test séparé du bouton d’alimentation : écran éteint, son arrêté, déverrouillage normal et reprise.
- Limites exactes : déverrouillage sécurisé obligatoire, récupération assistée par USB, sorties physiques non testées, erreurs de flux ou mémoire épuisée. Ne déclarez pas ces cas entièrement acceptés.

Avant de remettre la tablette suivante, reprenez la même liste de contrôle plutôt que d’hériter de la réussite de la précédente. Les anciennes Fire peuvent nécessiter `hls.js` pour M3U, car le HLS natif du fabricant peut planter ; cela ne prouve pas la stabilité de Plex.

## Maintenance et retour arrière

```sh
ott TABLET kiosk off
```

Ouvrez **OTT Startup**, désactivez **Start OTT after reboot** et restaurez le Home initial s’il a changé. Au besoin, désinstallez uniquement `play.ott.home` par le transport USB vérifié ; préservez `play.ott.foss` et ses données. Désactiver le démarrage ne modifie pas la file et ne désactive pas automatiquement le kiosque.
