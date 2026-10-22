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

[Documentation / Wiki](docs/wiki/Home.fr.md)

Un petit serveur à héberger soi-même qui transmet des commandes à distance aux lecteurs OTT-play. Les lecteurs émettent des requêtes HTTP sortantes : le téléviseur n'a donc pas besoin d'ouvrir un port d'écoute. Chaque lecteur possède sa propre file et son propre jeton d'accès ; un jeton administrateur distinct sert à envoyer les commandes.

Le [contrôle fiable](docs/reliable-control.md), facultatif, ajoute des identifiants d'intégration à portée limitée, une conservation durable mais bornée des résultats des commandes, l'application reprenable des configurations Local/Home avec vérification exacte des paramètres et un transfert explicite entre lecture en direct et archives. Ces fonctions nécessitent des révisions du contrôleur et de la CLI contenant ce guide. La vérification exacte des paramètres et le transfert nécessitent aussi un lecteur mis à jour. L'ancienne révision d'installation indiquée ci-dessous n'inclut pas ces fonctions. Le [schéma d'architecture](docs/assets/reliable-control.svg) indique où restent les paramètres, les identifiants et l'état des opérations.

Le serveur s'exécute sous forme d'un seul exécutable Go portable, avec une bibliothèque DNS à version fixe pour la découverte facultative des services. Les distributions couvrent Linux amd64, arm64 et ARMv7 ; macOS Intel et Apple Silicon ; Windows amd64 et arm64. Les images de conteneur prennent en charge Linux amd64 et arm64. Kubernetes et k3s utilisent le chart Helm inclus.

## Démarrer un serveur

Téléchargez l'archive appropriée depuis les [versions publiées](https://github.com/open-ott-play/ottplay-control-server/releases), vérifiez-la à l'aide du manifeste de version, puis extrayez-la. Sous Windows, utilisez `ottplay-control-server.exe` dans les commandes ci-dessous.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` crée des identifiants aléatoires et distincts dans un nouveau fichier réservé au propriétaire sous Unix. Il refuse d'écraser un fichier ou un lien symbolique existant et n'affiche jamais les identifiants. Gardez ce fichier privé ; sous Windows, limitez son ACL au compte exécutant le serveur. `config.example.json` documente les champs et contient volontairement des jetons fictifs non valides.

L'adresse par défaut est `0.0.0.0:8081` (toutes les interfaces IPv4, réseau local compris). Connectez les téléviseurs à l'adresse et au port du serveur sur le réseau local. Les configurations existantes conservent leur valeur explicite de `listen` ; remplacez-la par `0.0.0.0:8081` ou utilisez `--listen 0.0.0.0:8081` pour rendre accessible une installation auparavant limitée à l'interface de boucle locale. Ajoutez l'**origine exacte de la page** du lecteur à `allowed_origins`, par exemple `http://192.168.1.20:8443`. Le protocole, le nom d'hôte et le port doivent correspondre ; n'ajoutez aucun chemin. Une liste d'origines vide n'autorise que les clients hors navigateur. Les modifications prennent effet après redémarrage. Les identifiants de l'appareil et de l'administrateur restent obligatoires.

HTTP est pris en charge pour les anciens navigateurs de téléviseur sur un réseau local de confiance. Utilisez HTTPS lorsque la connexion traverse un réseau non fiable : `serve --config config.json --tls-cert server.crt --tls-key server.key`, ou terminez TLS sur un proxy inverse. Les pages du lecteur chargées en HTTPS ne peuvent pas se connecter à un serveur HTTP simple dans un navigateur. Les applications natives conservent également la politique de transport de leur système d'exploitation ; ce paramètre ne l'assouplit pas.

Le HTTPS direct vérifie la chaîne de certificats fournie avant d'ouvrir l'écoute : les clés RSA doivent faire au moins 2048 bits, les clés ECDSA au moins 224 bits ; Ed25519 est aussi accepté. Remplacez un certificat trop faible avant la mise à niveau. Consultez la [politique des certificats TLS et sa portée](docs/security-design.md#direct-https-certificate-policy).

## Connecter le lecteur

Utilisez une version du lecteur comprenant la connexion au serveur de commandes. Ouvrez **Paramètres → Télécommande** et renseignez :

1. **Adresse du serveur :** son IP ou son nom d'hôte, avec un port facultatif, ou une adresse complète `http://` / `https://`. Un hôte sans protocole utilise par défaut HTTP sur le port 8081.
2. **Code d'accès :** le `token` de l'entrée de ce lecteur dans `devices`, jamais `admin_token`.
3. **Connecter :** activez l'interrogation périodique et vérifiez l'état affiché dans la même boîte de dialogue.

Le lecteur enregistre cette connexion uniquement dans l'installation actuelle et l'exclut des sauvegardes de paramètres et des transferts cloud. Elle ne nécessite pas le serveur HTTP local distinct. Les navigateurs web exigent une origine CORS autorisée ; les pages TV empaquetées avec `Origin: null` peuvent utiliser le paramètre explicite `allow_null_origin` pour les routes d'appareil et d'amorçage prévues. Cela n'active pas les routes administrateur ou opérateur de diagnostic.

## Télécommande dans le terminal

Le [guide Android OTA illustré](docs/android-ota.md) distingue les mises à jour Wi-Fi Capacitor ordinaires (`ott DEVICE update --variant api22|full`, dernière bêta par défaut ; `--version` sélectionne une version) de l'agent root distinct. Il explique les mises à jour signées de l'agent et des APK/auxiliaires, la récupération et la vérification du résultat installé.

Le [guide Android ADB](docs/android-adb.md) couvre l'accès USB via un poste de travail ou Synology, le contrôle de l'écran et du clavier, les captures d'écran, les journaux, l'installation d'APK et le redémarrage normal, y compris les limites des anciennes tablettes.

La [découverte et l'association DNS-SD](docs/discovery.md), facultatives, permettent aux lecteurs de trouver le contrôleur via leur domaine réseau et de recevoir un jeton individuel après `ott approve NAME CODE`. Le DNS ne contient que les métadonnées du service. Les connexions manuelles existantes continuent à fonctionner sans activer la découverte.

Utilisez la CLI Python pour les alias de lecteurs, le volume en direct, la recherche de chaînes, l'EPG courant, la sélection du fournisseur et sa configuration lorsqu'elle est prise en charge : [guide CLI](docs/cli.md). Le serveur et le lecteur doivent tous deux inclure l'extension requête/réponse.

La CLI Python s'installe depuis une copie de travail des sources, séparément de l'archive du serveur natif. Les [étapes d'installation et de configuration](docs/cli.md#installation-and-connection) couvrent macOS/Linux et Windows, la configuration de Python/PATH et les modules CLI nécessaires. Elles fixent un commit de développement 0.1.1 vérifié, comprenant le workbench et la vérification hors ligne ; la CLI stable v0.1.0 et les archives du serveur natif n'incluent pas ces commandes. [Mettez à jour la CLI ou revenez à une version antérieure](docs/cli.md#update-select-or-roll-back-the-cli) sans remplacer une copie de travail, un lanceur ou une configuration privée existants. Les lanceurs locaux peuvent utiliser le [résolveur de playlists](docs/cli.md#local-playlist-resolver) en lecture seule pour rechercher des chaînes, des programmes actuels et des archives lisibles sans contrôleur. Après installation et configuration :

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

`a1` est un exemple d'alias d'un lecteur enregistré, pas le nom d'hôte d'un ordinateur. Consultez [l'enregistrement et l'association](docs/cli.md#register-and-connect-a-player), [toutes les commandes avec exemples](docs/cli.md#commands), [la déconnexion et la révocation d'accès](docs/cli.md#disconnect-deregister-or-revoke-a-player) et [le dépannage](docs/cli.md#troubleshooting).

Pour un lecteur toujours allumé, utilisez [standby et go](docs/cli.md#stop-playback-while-keeping-the-player-online) afin d'arrêter la lecture et de la reprendre plus tard sans quitter l'application. `stop` reste un alias de `standby` ; `start` et `wake` restent des alias de `go`. La télécommande doit rester connectée : la veille ne garantit donc pas l'absence de trafic réseau. `ott a1` affiche les opérations annoncées par le lecteur. L'acceptation d'une commande ne confirme pas la fin de l'arrêt ou du démarrage de la lecture.

Les [commandes de format d'image](docs/cli.md#aspect-ratio-fit-and-fill) nécessitent une CLI et un contrôleur à jour, ainsi qu'un lecteur annonçant la capacité `aspect`. La modification est appliquée après accusé de réception ; relisez `aspect` pour confirmer le mode et son enregistrement. Fill recadre proportionnellement au lieu d'étirer l'image.

Les [captures d'écran](docs/cli.md#remote-screenshots) à distance nécessitent une CLI et un contrôleur à jour, ainsi qu'un lecteur doté d'un adaptateur de capture compatible. Activer la connexion Télécommande du lecteur autorise les captures et les diagnostics pour ce contrôleur ; les captures natives ne nécessitent pas d'autorisation distincte. Les navigateurs exigent toujours leur sélecteur local de source. Les images peuvent contenir les paramètres du lecteur, des écrans de code PIN et des identifiants visibles : ne connectez donc que des contrôleurs de confiance. `ott a1` indique la disponibilité et la nécessité de sélectionner une source. Les captures d'écran sont distinctes de la collecte de télémétrie structurée.

Pour mélanger toutes les vidéos VPortal correspondant à un titre et les lire en boucle :

```sh
ott l vpr "wedding"
```

`vpr` mélange une fois toute la sélection, puis répète cette file. Relancez-le pour un nouveau mélange. `vp` lit les résultats dans l'ordre du catalogue ; `vp --list` les énumère sans modifier la lecture. Consultez les [commandes VPortal](docs/cli.md#commands).

Pour une séquence Plex explicite utilisant la configuration Plex enregistrée du lecteur :

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

La CLI attend jusqu'à 45 secondes par défaut. Écrire `ott -t 45 l plex shuffle "Tom and Jerry"` rend explicite ce même délai de réponse ; cela ne limite pas la durée de lecture des vidéos. Le recensement de grandes bibliothèques Plex peut dépasser un délai court de 10–15 secondes. Placez `-t SECONDS` (ou `--timeout SECONDS`) avant l'alias du lecteur. Consultez les [délais des bibliothèques Plex](docs/cli.md#plex-libraries-by-name) pour des exemples de prévisualisation et la marche à suivre lorsqu'une demande de lecture expire.

Les files Plex nécessitent une CLI, un contrôleur et un lecteur implémentant `plex_queue`. `plex play --shuffle ID...` mélange une fois les identifiants fournis. Les files s'arrêtent après le dernier élément, sauf si une politique de kiosque existante les répète. Tant qu'une file existe, les commandes ordinaires `next` et `prev` la ciblent ; `plex stop` la vide. Une prévisualisation ne change jamais de fournisseur et ne remplace jamais la lecture en cours. Consultez les [commandes de file Plex](docs/cli.md#ordered-plex-queues) pour les limites et la disponibilité. La recherche de bibliothèque nécessite en plus un lecteur et un contrôleur à jour annonçant `plex_library` ; elle utilise la connexion Plex enregistrée du lecteur et refuse les sélections ambiguës ou incomplètes. Consultez les [bibliothèques Plex par nom](docs/cli.md#plex-libraries-by-name).

### Enregistrer des profils de vidéos locales

Avec un lecteur et un contrôleur actuels, les fichiers/dossiers locaux utilisent le fournisseur `offline` :

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Connectez d'abord les fichiers/dossiers sur le lecteur. L'enregistrement conserve leur configuration et la vidéo sélectionnée sans démarrer la lecture. L'export ne transfère que les descriptions ; les médias et les autorisations d'accès du système/navigateur restent sur l'appareil. Une autre installation nécessite une association locale, et le chargement ne signifie pas que la lecture est confirmée. Consultez les [profils de vidéos locales](docs/cli.md#local-videos-profiles) pour la configuration, les capacités, les limites du format portable et le dépannage.

### Enregistrer l'élément actuel entre profils

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Les favoris persistent sur le lecteur cible lors des redémarrages normaux. En sélectionner un charge son fournisseur/profil enregistré et résout un lien neuf ; une source modifiée ou un élément absent provoque un échec sans substitution par une autre chaîne ou un autre film. La CLI, le contrôleur et le lecteur doivent prendre en charge `favorites` ; `ott l` affiche les opérations disponibles. Consultez les [favoris entre profils](docs/cli.md#favorites-across-profiles) pour les alias, les limites, la sémantique des accusés de réception et le dépannage.

### Charger une configuration enregistrée

Stockez les configurations nommées dans la section privée `presets` de `~/.config/ottplay-control/cli.json`. Chacune contient des profils M3U numérotés (playlist et heures d'archive), les identifiants Plex et le profil M3U à sélectionner après chargement. Consultez l'[exemple de configuration](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

La CLI valide d'abord toute la configuration, puis attend l'accusé de réception du lecteur avant d'envoyer chaque modification suivante. Elle gère le chargement du fournisseur, enregistre Plex et les emplacements M3U, puis sélectionne le profil M3U configuré. Une erreur interrompt la séquence et indique les étapes confirmées ; une écriture au résultat incertain n'est jamais répétée automatiquement. Aucun service de profils supplémentaire ni redémarrage du contrôleur n'est nécessaire. Gardez cette configuration privée : elle contient les identifiants des fournisseurs.

## Envoyer une commande

Cet exemple lit le jeton administrateur dans la configuration locale privée sans le placer dans l'URL ni dans les arguments de la ligne de commande :

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

D'autres commandes sélectionnent une chaîne par numéro ou nom, choisissent une chaîne aléatoire, changent de fournisseur, affichent un message, modifient une playlist M3U lorsque cela est pris en charge ou quittent le lecteur. Leur disponibilité dépend de la plateforme du lecteur et du fournisseur actuel. Consultez le [contrat API](docs/api.md).

Avec un lecteur prenant en charge le fournisseur VPortal autonome, `ott a1 provider
vportal` sélectionne ses 15 profils propres. Utilisez `profile N vportal LINK`, `profile N
name NAME` ou un fichier privé `profile-config N FILE` contenant `name` et `vportal`, puis sélectionnez l'emplacement configuré avec `profile N`. Les profils M3U existants restent séparés. `profiles` fournit les métadonnées sans révéler les liens du compte ni les clés.

Après le lancement d'un film VPortal ou d'une file d'épisodes, `ott a1 kiosk on` verrouille cette sélection. Le lecteur conserve la file et sa position lors des rechargements et résout de nouveau les URL des médias pour la récupération. Utilisez `kiosk off` pour choisir un autre titre ou modifier les profils, puis `kiosk on` pour verrouiller. Le reçu du kiosque indique le titre et le nombre d'épisodes ; les requêtes du fournisseur et les URL des flux restent sur le lecteur.

Utilisez `ott a1 kiosk on --strict` pour une lecture destinée aux enfants : un toucher local affiche le pied de la vidéo. Les lecteurs core mis à jour permettent de toucher ou de faire glisser sa barre temporelle pour se déplacer dans l'épisode VPortal/Plex actuel ; les autres commandes locales restent verrouillées. Les anciens lecteurs, les kiosques de chaînes et les enveloppes interceptant les gestes de la barre conservent un pied en lecture seule. Un verrou existant conserve sa cible. Libérez-le depuis le contrôleur avec `ott a1 kiosk off`. Sur les sites web Android, la protection contre la sortie système exige un épinglage séparé de l'application avec code PIN ; consultez le [mode kiosque](docs/cli.md#kiosk-mode).

## Diagnostics à distance

Commencez par le [démarrage rapide du workbench en lecture seule](docs/workbench.md#quick-start) pour `doctor`, `inspect` de l'interface/des médias, la recherche de reçus d'opération, les ensembles de preuves, `health`, `media-progress` web/natif et `report verify` hors ligne. Il utilise la configuration CLI administrateur et les alias de lecteurs existants. La voie native est destinée à l'agent de maintenance Android provisionné séparément ; les lecteurs Tauri utilisent l'inspection web. Les rapports distinguent la progression du décodeur de la vérification physique de l'écran et du son. Consultez le [dépannage du workbench](docs/workbench.md#troubleshooting) pour les capacités manquantes, les voies indisponibles et les preuves incomplètes.

Les [diagnostics du protocole 2](docs/remote-diagnostics.md), facultatifs, fournissent des sessions explicitement activées pour chaque environnement d'exécution, avec des identifiants opérateur distincts à portée limitée et une télémétrie structurée bornée. La connexion activée du lecteur fournit l'autorisation locale sans nouvelle demande de confiance ; chaque capture garde une durée de session limitée. L'interrogation du contrôle de diagnostic est indépendante de la file de commandes existante.

Le [guide de la CLI de diagnostic et de MCP](docs/diagnostics-cli.md) couvre l'installation, les identifiants opérateur, la connexion, chaque commande/outil de diagnostic, des exemples de capture et de réparation, la révocation d'un environnement d'exécution et le dépannage. `ott diagnostics --help` énumère cette famille de commandes distincte ; les alias de lecteur ordinaires et les identifiants administrateur ne donnent pas accès aux diagnostics.

## Paquets de déploiement

- [Tablette Android : kiosque Plex strict et démarrage](docs/android-kiosk-setup.md)
- [Gestion USB Android partagée et récupération au démarrage](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS et Windows](docs/deployment.md)
- [Configuration, sémantique de livraison et limites](docs/api.md)
- [Processus de publication et de validation](docs/development.md)

Les files sont bornées, expirent après 60 secondes par défaut et résident en mémoire. Redémarrer le serveur supprime les commandes en attente. Exécutez une seule réplique ; les répliques indépendantes ne partagent pas leurs files. Un lecteur accuse réception d'une tentative de distribution, pas d'une lecture ou d'un volume matériel confirmés. Les nouvelles tentatives réseau sont dédupliquées au sein d'une session active du lecteur, pas au-delà d'un redémarrage de l'application.

Ce serveur suit l'API de commandes de l'exemple facultatif `local_proxy.py` d'[OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), avec des identifiants administrateur/appareil séparés et une livraison avec accusé de réception. L'ancien proxy reste un composant distinct. Ce projet ne relaie pas les médias et ne modifie pas les communications réseau HLS, EPG ou fournisseurs du lecteur.

## Licence

MIT. L'attribution des outils de publication intégrés figure dans `NOTICE` et `LICENSES/`.

## Maintenance du projet

Consultez les [exigences de contribution et de test](CONTRIBUTING.md), la [politique de signalement de sécurité](SECURITY.md), la [conception de la sécurité](docs/security-design.md) et les [preuves OpenSSF et critères restants](docs/openssf-evidence.md).

Les files Plex acceptent jusqu'à 500 identifiants sur les lecteurs actuels. Utilisez `ott f10 plex play --shuffle ID...` pour mélanger une fois, puis `ott f10 kiosk on --strict` lorsque le fournisseur Plex est sélectionné pour boucler et verrouiller cette file. `ott f10 kiosk off` lève le verrou. Les anciens lecteurs conservent leur limite de file annoncée.

## Fuzzing des versions candidates

Les versions candidates publiques exécutent un fuzzing natif borné des frontières d'entrée décrites dans le [guide de fuzzing](docs/fuzzing.md). Les campagnes de mutation ne s'exécutent pas sur les PR, les bêtas ni les versions nocturnes. Le guide décrit la portée, les budgets, les preuves et la reproduction des régressions.

## Communauté et preuves de sécurité

Consultez les [contributions](CONTRIBUTING.md), la [gouvernance](GOVERNANCE.md), le [code de conduite](CODE_OF_CONDUCT.md), la [feuille de route](ROADMAP.md) et les [preuves OpenSSF](docs/openssf-evidence.md). L'index des preuves distingue les profils de sécurité pris en charge, les résultats réels des tests et les exigences Silver restantes.
