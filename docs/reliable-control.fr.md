<div lang="fr" dir="ltr">

# Commande fiable et transfert de lecture

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Index de la documentation](wiki/Home.md)

Ces fonctions nécessitent le contrôleur et la CLI d’une révision contenant ce guide. La comparaison exacte des réglages et le transfert nécessitent aussi un lecteur à jour. Les anciennes archives de versions et la révision historique de la CLI du guide d’installation n’acquièrent pas ces fonctions automatiquement. Installez **tous les fichiers `cli/*.py` ensemble**, y compris `preset_session.py` ; conservez la configuration privée.

![Flux de commande, de point de contrôle et de transfert](assets/reliable-control.svg)

## Activer des résultats durables à capacité limitée

Ajoutez ce bloc facultatif à la configuration privée du serveur, puis validez-la et redémarrez le serveur. Choisissez un chemin absolu sur un système de fichiers local persistant accessible en écriture au compte du service. Sous Unix, son répertoire doit être privé (0700) ; les fichiers de journal et de verrou existants doivent être des fichiers ordinaires privés (0600). Sous Windows, limitez l’ACL du répertoire au compte du service. Un seul processus possède un journal.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Le serveur crée les répertoires manquants avec le mode 0700. Les limites sont de 1–4096 enregistrements, 60–86400 secondes et 4 MiB sur disque. La rétention doit couvrir `command_ttl_seconds`. Un journal plein refuse les nouvelles opérations avec clé au lieu d’évincer les enregistrements encore valides. Un fichier corrompu, un répertoire Unix non sécurisé, un autre processus d’écriture ou l’échec d’une écriture durable provoquent un refus sûr. Le fonctionnement par défaut reste le chemin de commande existant en mémoire.

L’unité systemd fournie crée `/var/lib/ottplay-control` avec le mode 0700. Pour Compose, préparez un répertoire hôte appartenant à l’UID 65532, définissez `CONTROL_SERVER_STATE` avec son chemin absolu et combinez `deploy/compose.yml` avec `deploy/compose.journal.yml`. Ce dernier ajoute uniquement le montage d’état accessible en écriture ; le système de fichiers racine reste en lecture seule. Pour Helm, préparez séparément une demande de volume persistant et définissez `journalExistingClaim=CLAIM_NAME`. Elle est montée sur `/var/lib/ottplay-control` ; utilisez le sous-répertoire privé indiqué ci-dessus. Le chart conserve une réplique, `Recreate` et `fsGroupChangePolicy: OnRootMismatch`, afin qu’un remontage n’élargisse pas récursivement les droits des répertoires privés déjà créés. Vérifiez que le pilote de stockage choisi respecte cette sémantique de propriété. Le chart ne crée ni ne supprime la demande, et activer un montage n’active pas le journal : cela reste contrôlé par la configuration privée du serveur.

Pour `POST /api/requests`, ajoutez `idempotency_key` à côté de `action` et `params`. Les anciens POST de commande acceptent plutôt `Idempotency-Key`. Les clés contiennent 16–80 lettres ASCII, chiffres, traits de soulignement ou traits d’union ; générez une nouvelle clé aléatoire pour chaque intention. Le même propriétaire/la même clé avec le même appareil, la même action et la même charge JSON normalisée renvoient l’ID d’origine sans nouvelle mise en file. Une charge modifiée renvoie 409. Les clés sont délimitées par l’ID d’opérateur (ou l’administrateur), pas par appareil. Après expiration de la rétention, une clé peut être acceptée comme nouvelle intention : les clients ne doivent pas réessayer une ancienne intention hors de cette période.

`GET /api/operations?device_id=DEVICE` indique si le journal est activé, ses limites et le TTL des commandes. Ajoutez `&key=KEY` pour lire une opération :

- `pending` : acceptée ; aucune réponse conservée pour le moment.
- `responded` : une réponse validée du lecteur est arrivée ; `result_status` vaut `ok`, `rejected` ou `unsupported`. Le corps du résultat initial expire toujours normalement.
- `acknowledged` : un ancien consommateur a acquitté la commande. Cela ne prouve ni une lecture visible ni l’enregistrement d’un réglage.
- `unknown` : le délai a expiré ou le serveur a redémarré avant de conserver une réponse. Le serveur **ne rejoue jamais** cette commande après un redémarrage.

Un POST répété avec clé renvoie `status: retained`, l’ID d’origine et l’état. Les nouveaux POST ordinaires conservent `status: queued`. Les entrées du journal contiennent uniquement le propriétaire, l’appareil, l’action, des ID aléatoires, une empreinte de la charge, l’état et les échéances. Elles ne contiennent ni charges de commande, ni URL de fournisseur, ni jetons, ni captures d’écran, ni corps de réponse. Les commandes en attente ne sont pas persistées. Il s’agit de déduplication et de suivi des résultats à capacité limitée, pas d’une exécution exactement une fois, d’une file de travail durable ou d’une haute disponibilité multiserveur.

## Planifier, appliquer, reprendre et vérifier Local/Home

Le comportement existant de `ott PLAYER load PRESET` est préservé. Pour le nouveau flux :

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` fonctionne hors ligne : il valide le préréglage local et liste les emplacements des réglages ainsi que le profil actif souhaité sans afficher leur contenu. Il n’inspecte pas le lecteur. `verify` effectue des comparaisons exactes en lecture seule et vérifie le profil M3U sélectionné. Il renvoie un code non nul en cas de différence ou de comparaison non prise en charge.

`apply` nécessite le journal du serveur. Il crée un point de contrôle privé et compare chaque fournisseur configuré avant la première modification. Chaque enregistrement compare les octets stockés aux valeurs attendues via `settings_compare` ; une correspondance évite l’écriture. Une vérification finale couvre Plex, tous les emplacements M3U, les emplacements Stalker facultatifs et le profil M3U sélectionné. Les comparaisons renvoient un booléen, jamais les secrets stockés.

Le point de contrôle par défaut se trouve sous `~/.config/ottplay-control/checkpoints/`. Utilisez `--checkpoint /absolute/private/path/session.json` après le mode pour choisir un autre fichier. Les points de contrôle contiennent l’appareil, le nom du préréglage, l’empreinte de la révision serveur/préréglage, les noms des étapes terminées et une clé d’opération aléatoire en attente avec son échéance. Ils ne contiennent aucune valeur de réglage. Les fichiers sont remplacés atomiquement après fsync et verrouillés contre les écritures simultanées de CLI. Gardez le préréglage local correspondant disponible. Sous Unix, le répertoire doit être privé (0700), avec des fichiers ordinaires en 0600. Sous Windows, limitez l’ACL héritée du répertoire à l’utilisateur exécutant la CLI ; les bits de mode Unix n’imposent pas les permissions d’accès Windows.

`resume` valide le même serveur, le même appareil et la même révision de préréglage, puis lit le résultat en attente. Une réponse conservée permet une nouvelle vérification immédiate. Un résultat incertain bloque les modifications jusqu’à l’écoulement de l’échéance de la commande précédente, du délai de requête et d’une petite marge. Relancez `resume` après cette période. Il revérifie les réglages stockés même pour les étapes terminées, afin de ne pas confondre des changements de l’utilisateur avec des écritures antérieures réussies. Il ne rejoue jamais aveuglément une commande relative. La sélection absolue du fournisseur/profil peut être répétée après cette garde.

Un point de contrôle existant n’est jamais écrasé par un nouvel `apply`. Réutilisez-le avec `resume`, ou choisissez un nouveau fichier pour un préréglage modifié. Chaque appareil possède son propre point de contrôle. Aucune transaction ni restauration ne couvre tous les lecteurs ; les écritures antérieures réussies restent en place si une étape ultérieure échoue. `verified` confirme les valeurs stockées du lecteur au moment de la comparaison, pas le chargement réseau réussi ni la sortie physique vidéo/audio.

## N’accorder aux intégrations que l’autorité nécessaire

Utilisez des [opérateurs de commandes ordinaires](security-design.md#scoped-command-operators) pour HA, un pont RF ou un agent au lieu de distribuer `admin_token`. Autorisez des ID d’appareil exacts et des opérations comme `input.channel_up` et `input.channel_down`. Pour ce flux de préréglage, les opérations de réglage exigent explicitement les permissions `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` et `provider_settings`. Le transfert exige `playback_handoff_export` sur la source et `playback_handoff_import` sur la cible. Une entrée d’opérateur combine ses appareils et actions listés ; utilisez des identités distinctes si la même opération ne doit pas s’appliquer à chaque appareil listé. Les jetons et le consentement aux diagnostics restent des autorisations distinctes.

Les opérateurs ne peuvent pas interroger les files des lecteurs, soumettre leurs réponses, lister tous les appareils, lire les sorties de débogage administrateur ou le résultat d’un autre opérateur. Réduire le périmètre d’une action empêche aussi de lire son opération conservée. Supprimer un opérateur puis redémarrer révoque son identifiant d’accès. Ne réutilisez pas un ID d’opérateur pour un autre acteur tant que ses anciennes entrées de journal subsistent.

## Continuer sur un autre lecteur

```sh
ott handoff living-room bedroom
```

La source exporte une référence direct/archive de version 1 : fournisseur, identité opaque de source, empreinte opaque d’élément `channel-v1:<8 hex>.<8 hex>` et, pour l’archive, l’heure courante du programme. Les ID bruts des fournisseurs et les URL de flux restent locaux. La cible doit avoir **la même source/le même compte configuré**, résoudre un élément correspondant unique dans son propre catalogue et obtenir une nouvelle URL avec ses propres identifiants locaux. Le contrôleur ne copie aucune URL lisible et ne modifie pas les identifiants de la cible. La source continue à jouer. Un succès de la CLI signifie que la cible a envoyé la requête, pas que son écran ou ses haut-parleurs ont été vérifiés physiquement.

La version 1 prend en charge les catalogues direct/archive `m3u`, `xtream` et `stalker` lorsque le lecteur peut fournir les identités requises. Les éléments inconnus ou ambigus, les sources incompatibles et le refus par une politique locale échouent sans recherche de secours par nom. La VOD/les séries, la correspondance entre comptes différents, la mise en pause de la source et la synchronisation continue de l’historique/des favoris ne font pas partie de ce premier contrat.

## Exercices de validation et de récupération

Exécutez `go test -race ./...`, `go vet ./...` et `python3 -m unittest discover -s tests`. Les tests couvrent les réponses perdues après une écriture réussie, la comparaison exacte avant reprise, la dérive de configuration, les échéances d’opérations inconnues, deux processus d’écriture, les journaux corrompus, les lectures entre opérateurs et les charges de transfert mal formées. Sur un lecteur de préproduction, interrompez aussi la CLI après l’enregistrement d’un réglage, redémarrez le contrôleur, reprenez et vérifiez ; testez un transfert entre sources correspondantes et non correspondantes. Confirmez séparément la vidéo et l’audio.

Pour une validation locale entre dépôts, avec la copie de travail FOSS actuelle et ses dépendances de développement installées, exécutez :

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Le test de fumée construit ce serveur Go, compile l’ensemble du projet TypeScript FOSS et exécute son transport réel, ses gestionnaires de commandes, son identité de source et son codec de playlist fourni face à la CLI Python. Il vérifie les réglages exacts des trois fournisseurs pris en charge, les divergences, le transfert en direct avec des ID de lignes locaux différents, les décalages de recherche dans l’archive, l’échec de résolution des URL et la validation stricte du format transmis. Seuls le stockage, la politique du fournisseur et les interfaces de l’hôte de lecture sont des doublures de test ; il ne vérifie ni navigateur, ni sortie multimédia physique, ni accès à un compte réel. Il écoute sur l’interface de bouclage, utilise des fichiers temporaires privés, ne contacte aucune URL de flux et supprime ses fichiers et son serveur à la sortie.

</div>
