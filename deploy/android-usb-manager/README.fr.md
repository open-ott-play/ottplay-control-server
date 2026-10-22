# Image de transport USB Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Ce répertoire contient les modèles de déploiement et le [`Dockerfile`](Dockerfile) du conteneur de transport du gestionnaire partagé. Le gestionnaire Python s’exécute sur l’hôte USB. L’image fournit ADB, Python et les outils de l’adaptateur de maintenance atomique ; elle ne contient ni second registre d’appareils ni démon de récupération. Le [guide d’exploitation](../../docs/android-usb-manager.md) décrit le provisionnement, les commandes, la migration, les limites de récupération et le retour arrière.

Sans copie de travail Git, téléchargez `ottplay-control-server-deployment.tar.gz` et `release-manifest.json` depuis la même [version](https://github.com/open-ott-play/ottplay-control-server/releases). Vérifiez l’empreinte de l’archive et extrayez-la dans un nouveau répertoire selon [Obtenir le paquet de déploiement](../../docs/android-usb-manager.md#get-the-deployment-package). Le paquet inclut cette recette et les six scripts locaux/distants sous `tools/` ; conservez son arborescence. Les anciens paquets, dont `v0.1.5-beta.6`, n’ont pas ces scripts et nécessitent l’archive source correspondante. Le paquet n’inclut pas d’image de transport USB déjà construite. L’artefact OCI publié du serveur a une autre fonction.

## Provenance des sources et des artefacts

La base est `python:3.12.14-slim-trixie`, figée à `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. La base, l’instruction APT et l’installation de `websocket-client==1.9.0` verrouillée par empreinte ont été examinées dans [la PR 128, révision b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Leur texte est conservé pour réutiliser un cache de construction disponible. Le paquet WebSocket facultatif prend en charge les clients génériques de maintenance existants ; les adaptateurs CDP migrés peuvent aussi n’utiliser que la bibliothèque standard Python.

Cette recette supprime le paquet `pip` installé après l’installation du wheel verrouillé, exclut le code de gestion et de récupération de la PR 128, lance ADB au premier plan et désactive les contrôles de santé hérités. Retirer l’installateur à l’exécution réduit la surface d’attaque disponible ; ce n’est pas la preuve d’une analyse sans vulnérabilité.

L’artefact examiné de la PR 128 était `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Il contient un autre point d’entrée et un contrôle de santé par battement incompatible. C’est un artefact de référence, pas une construction de ce Dockerfile. N’attribuez pas son identifiant d’image, la provenance de ses sources ou ses résultats d’analyse à cette recette.

Les dépôts APT ne sont pas figés à des instantanés. Une future reconstruction propre peut donc sélectionner des paquets système plus récents, même avec la même empreinte de base et du wheel Python. Lors de la qualification d’une construction, consignez la révision exacte, le constructeur/la plateforme, l’identifiant d’image obtenu, les versions des paquets et l’analyse. La configuration de production doit utiliser cet identifiant d’image immuable qualifié ou son empreinte.

## Construire sans perturber un hôte USB occupé

Privilégiez un constructeur distinct pour construire et analyser. L’hôte cible n’a besoin que de l’image qualifiée, sans téléchargements de paquets ni chaîne de compilation.

Avant de réutiliser un constructeur local préparé, vérifiez la présence de l’image de base exacte et des couches d’installation APT et wheel en cache. Charger seulement l’image finale de la PR 128 ne garantit aucune de ces conditions. Depuis la racine du dépôt ou le répertoire de déploiement extrait, voici une tentative délibérément hors ligne :

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` s’applique aux étapes de construction ; cela ne garantit pas qu’un constructeur ne contacte jamais un registre pour résoudre la base. Confirmez sa résolution locale avant d’appliquer cette procédure sur un hôte contraint. Si une couche APT ou wheel manque, arrêtez la tentative plutôt que d’autoriser silencieusement une reconstruction réseau sur l’hôte USB.

BuildKit inclut le mode réseau de chaque étape dans sa clé de cache. Un cache créé avec le mode réseau par défaut peut donc ne pas correspondre à la tentative hors ligne ci-dessus. Ne le réutilisez qu’après vérification de la base exacte et de la chaîne d’instructions ; arrêtez-vous si une étape d’installation s’exécute au lieu d’indiquer `CACHED`. La suppression finale de `pip` utilise explicitement `RUN --network=none` ; elle n’a pas besoin du réseau même si les étapes précédentes en cache conservent leur mode initial.

Avant de modifier la configuration de production, contrôlez les versions de Python et d’ADB, analysez l’image exacte obtenue, vérifiez le transfert de fichiers binaires et la préparation des APK, puis effectuez les contrôles d’acceptation du guide : identité de l’appareil, autorisation des clés, veille et démarrage. ADB 34 doit être testé avec un ancien appareil Android ; son numéro de version ne prouve pas la compatibilité KitKat ou Fire. Conservez l’image actuelle et la sauvegarde privée du déploiement tant qu’un retour arrière peut être nécessaire.

## Budget d’exécution

Le gestionnaire hôte, et non ce Dockerfile, impose 256 MiB de mémoire, 256 MiB de mémoire plus swap et des parts CPU de 128, sans quota strict de CPU ni de PID. Il impose `--no-healthcheck`, `--network none`, une racine en lecture seule, des capacités supprimées et des correspondances précises de nœuds USB. `/tmp` est un tmpfs borné consommant le même budget mémoire. L’adaptateur de préparation écrit par blocs de 1 MiB et limite une charge utile à 128 MiB ; les opérations partagent le verrou du gestionnaire.

Docker explique la distinction entre les [contrôles de mémoire, de swap et de parts CPU](https://docs.docker.com/engine/containers/resource_constraints/) et [l’option `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
