<div lang="fr" dir="ltr">

# OTA Android : lecteurs ordinaires et agent root séparé

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Pour **k8a/fire8 et les autres lecteurs Capacitor**, utilisez la mise à jour Wi-Fi du lecteur normal, pas la commande de l’agent root :

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Omettre `--version` sélectionne la dernière bêta publiée. Utilisez `full` au lieu de `api22` sur Android 7+. La CLI retrouve la version officielle, vérifie son manifeste, prépare l’APK et interroge son état avant de demander une seule fois l’installation. Android peut exiger une confirmation sur la tablette. Désactivez explicitement le kiosque et déverrouillez les réglages protégés avant la maintenance ; la commande ne modifie jamais ces politiques. Voir la [procédure CLI complète](cli.md#capacitor-apk-updates-over-wi-fi).

Cela nécessite un APK déjà installé prenant en charge `AppUpdate`, le Wi-Fi et un lecteur en fonctionnement connecté au contrôleur. Les mises à jour ordinaires suivantes n’exigent pas d’USB, mais ne peuvent atteindre une tablette hors ligne ni une WebView arrêtée. Une première installation, un programme de mise à jour indisponible, une application endommagée ou une perte de réseau peuvent encore exiger une récupération locale. Aucun planificateur automatique de versions ni installateur silencieux général n’est sous-entendu.

Le reste du guide décrit un **autre agent de maintenance root pour a1**, limité à l’installation Matricom/API19 provisionnée `play.ott.kitkat`. Il n’est ni déjà installé ni utilisable sur les appareils Fire/Capacitor ordinaires.

Après l’amorçage USB root initial, l’agent Android indépendant peut recevoir des mises à jour signées par sa connexion HTTPS sortante. Il n’a pas besoin d’une WebView fonctionnelle pour se mettre à jour. L’agent 0.1.2 prend aussi en charge un paquet coordonné APK/helper tactile système, avec fichiers de récupération sauvegardés et vérification avant validation définitive.

Ce guide décrit le parcours complet. Pour les champs exacts du manifeste, les limites et les exigences matérielles, consultez la [référence de l’agent natif](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Éléments mis à jour

![Architecture OTA : les octets de version examinés sont signés, publiés via HTTPS direct, vérifiés par l’agent indépendant et contrôlés après installation. Le déploiement web est séparé.](images/ota-architecture.svg)

- **Agent natif :** `kind: agent` remplace son propre binaire ARMv7. Ce parcours fonctionne aussi lorsque OTT est désactivé et qu’une autre application HOME est utilisée.
- **APK seul :** `kind: apk` remplace l’enveloppe en conservant ses données. OTT doit être activé et au premier plan. Le helper intégré du candidat doit correspondre exactement au helper système installé.
- **APK et helper tactile :** `kind: bundle` met à jour les deux composants ensemble. Un ancien agent doit d’abord recevoir une mise à jour de l’agent seul prenant en charge les bundles.
- **Lecteur hébergé :** déployez l’artefact web vérifié à l’origine existante, puis rechargez et inspectez un lecteur actif. Une mise à jour APK ne déploie pas le JavaScript hébergé. Voir le [processus de publication](release-workflow.md).
- **Contrôleur et CLI :** déployez le contrôleur et installez séparément l’ensemble complet des modules CLI. Mettre l’un à jour ne remplace pas le code de la tablette.

Le micrologiciel, le noyau, la WebView système, le courtier root et les scripts de démarrage sont hors de ce format OTA. La [référence native](../native/android-agent/README.md#bootstrap) décrit les limites d’amorçage et de récupération. Les opérations OTA prises en charge n’exigent ni ADB réseau ni shell distant arbitraire.

## D’une version examinée à une installation confirmée

1. **Choisir des octets vérifiés.** Fusionnez les modifications examinées après réussite de la CI. Téléchargez l’artefact voulu et vérifiez sa révision source, ses preuves de publication immuables et sa somme de contrôle. Extrayez le binaire `agent` de l’archive native ; l’archive elle-même n’est pas une charge d’agent installable.
2. **Préparer et signer.** Utilisez la clé privée Ed25519 existante dont la clé publique a été épinglée pendant l’amorçage. Gardez cette clé et les identifiants des appareils privés.
3. **Publier et vérifier le transport.** Publiez uniquement la charge et `signed.json` à des chemins HTTPS immuables. Les deux doivent renvoyer directement leurs octets avec HTTP 200 ; le programme refuse les redirections. Exécutez la commande `verify` de l’outil de création de paquets.
4. **Envoyer une mise à jour.** Notez l’ID de requête et inspectez l’état après l’accusé de réception. Une réponse perdue ou incertaine ne justifie pas de répéter aveuglément une opération.
5. **Confirmer l’effet.** Comparez les hachages et versions installés fraîchement relevés avec la version préparée. Pour un lecteur actif, vérifiez aussi la cible du kiosque, la file et la progression de lecture. L’image et le son physiques nécessitent une observation.

Exemple pour une version d’agent ; remplacez la version et les chemins par ceux réellement choisis :

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

`ENVELOPE_SHA256` est le `envelope_sha256` de l’outil de création de paquets, pas le hachage de la charge. `accepted` signifie admission, pas installation réussie. L’auto-remplacement peut laisser un ancien reçu d’opération à `unknown` ; le nouveau runtime, la version attendue de l’agent et le hachage installé correspondant prouvent l’installation. La [référence CLI](cli.md#independent-android-agent) décrit le routage natif indépendant et le traitement des reçus.

## Récupération de la transaction APK/helper

![Transaction APK/helper : précontrôle, sauvegarde de la paire précédente, application, vérification, validation ; les échecs restaurent la paire précédente et une récupération incomplète maintient les modifications de l’application bloquées.](images/ota-transaction.svg)

Le bundle contient exactement `app.apk` et `ott-touch-guard`. L’outil peut le créer directement depuis l’APK d’enveloppe signé avec `--kind bundle --apk`. Il extrait le helper intégré, garantissant que les deux copies livrées correspondent. Voir la [commande bundle et ses exigences](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Avant de modifier les fichiers, l’agent exige une paire installée correspondante, assez d’espace libre, un lecteur OTT réactif au premier plan, une partition système en lecture seule et une prise en charge annoncée de la rétrogradation APK. Pour un kiosque verrouillé, il vérifie le même ID de chaîne TV ou les ID VPortal dans l’ordre ; des longueurs de file égales ne suffisent pas. Les files verrouillées non prises en charge sont refusées. Une application OTT volontairement désactivée est aussi refusée, ce qui conserve l’application HOME actuelle de l’utilisateur.

Les fichiers de récupération et la transaction en attente sont persistés avant l’arrêt de l’application. L’agent attend l’arrêt de l’application et de l’ancien helper, remplace le helper atomiquement, remet `/system` en lecture seule, installe l’APK et vérifie le résultat. Un échec d’application ou de vérification déclenche la restauration de la paire précédente. La récupération au démarrage restaure les fichiers sans lancer OTT ni prendre le contrôle de HOME.

Si la récupération ne peut aboutir, `updates.recovery_failed` est vrai. Les diagnostics et les réparations signées de l’agent seul restent disponibles ; les modifications de l’application et le watchdog de lecture sont bloqués. Il ne faut pas supprimer la transaction sauvegardée pour forcer une installation. La récupération des binaires n’annule pas les migrations de données applicatives et le micrologiciel peut encore refuser une rétrogradation. Voir les [limites de récupération](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Résultats et procès-verbal de réception

![Graphique de validation : 488 tests CLI, 576 tests des outils de publication et 6 tests de création OTA ; la tablette de référence exécute le nouvel agent vérifié, mais la réception physique APK/helper reste en attente.](images/ota-validation.svg)

L’audit du 14 octobre 2026 a réussi 488 tests CLI (un ignoré), 576 tests des outils de publication, six tests de création de paquets OTA, les vérifications natives Go de concurrence/statiques et une compilation ARMv7. Ces nombres décrivent la taille des suites, pas leur couverture ni leurs performances. Les tests par injection de pannes exercent les archives invalides, les transactions interrompues, la restauration partielle, les changements de cible et le blocage pendant la récupération. La revue indépendante a aussi détecté et corrigé une vérification insuffisante de l’identité de la file et une validation APK/helper différente entre hôte et appareil.

État vérifié le 15 octobre 2026 :

- L’implémentation OTA est fusionnée dans la [PR du contrôleur #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) et incluse dans [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Les paquets signés actuel et précédent de l’agent sont publiés sur l’[hôte de distribution OTA](https://android-updates.ottplay.here.now/). Leurs octets de charge et d’enveloppe ont été téléchargés et comparés avec succès via HTTPS direct.
- La tablette de référence est passée de `0.1.1-beta.21` à `0.1.2-beta.4` via le canal OTA indépendant. La version de l’agent en cours et le SHA-256 installé correspondaient à la version choisie ; son runtime a changé, l’ID de démarrage du système est resté identique. OTT n’avait toujours aucun processus applicatif actif, et aucune opération de lancement d’application ni de sélection HOME n’a été envoyée.
- L’état natif indiquait le hachage du helper tactile système, aucun bundle en attente et aucun échec de récupération. Les métadonnées APK n’étaient pas disponibles ; cette observation n’établit donc ni version ni hachage de l’APK installé.
- La transaction coordonnée APK/helper est validée côté hôte ; l’installation physique, la récupération après rétrogradation, l’image et le son ne sont pas encore réceptionnés sur la tablette de référence.

Pour chaque déploiement ultérieur, conservez source et sommes de contrôle de la version, hachage de l’enveloppe préparée, ID de requête, état natif avant/après et résultat distinct de réception physique. Un téléchargement réussi ou un reçu de commande ne remplace pas ces observations.

</div>
