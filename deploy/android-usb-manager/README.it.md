# Immagine di trasporto USB Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Questa directory contiene i modelli di distribuzione e il [`Dockerfile`](Dockerfile) del contenitore di trasporto del gestore condiviso. Il gestore Python viene eseguito sull’host USB. L’immagine fornisce ADB, Python e gli strumenti dell’adattatore di manutenzione atomica; non contiene un secondo registro di dispositivi o demone di ripristino. La [guida operativa](../../docs/android-usb-manager.md) descrive predisposizione, comandi, migrazione, limiti del ripristino e rollback.

Senza un checkout Git, scaricare `ottplay-control-server-deployment.tar.gz` e `release-manifest.json` dalla stessa [versione](https://github.com/open-ott-play/ottplay-control-server/releases). Verificare il digest dell’archivio ed estrarlo in una nuova directory come descritto in [Ottenere il pacchetto di distribuzione](../../docs/android-usb-manager.md#get-the-deployment-package). Il pacchetto contiene questa ricetta e i sei script locali/remoti in `tools/`; conservarne la struttura. I pacchetti precedenti, incluso `v0.1.5-beta.6`, non contengono tali script e richiedono l’archivio sorgente corrispondente. Non è inclusa un’immagine di trasporto USB già compilata. L’artefatto OCI del control server ha un altro scopo.

## Provenienza del sorgente e degli artefatti

La base è `python:3.12.14-slim-trixie`, fissata a `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. La base, l’istruzione APT e l’installazione di `websocket-client==1.9.0` bloccata tramite hash sono state esaminate nella [PR 128, revisione b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Il testo delle istruzioni è mantenuto per riutilizzare una cache di compilazione disponibile. Il pacchetto WebSocket facoltativo supporta i client generici di manutenzione esistenti; gli adattatori CDP migrati possono usare anche soltanto la libreria standard di Python.

Questa ricetta rimuove il pacchetto `pip` installato dopo l’installazione del wheel bloccato, esclude il codice di gestione e ripristino della PR 128, avvia ADB in primo piano e disabilita i controlli di integrità ereditati. Rimuovere l’installatore a runtime riduce la superficie di attacco disponibile; non dimostra una scansione senza vulnerabilità.

L’artefatto esaminato della PR 128 era `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Contiene un punto di ingresso diverso e un controllo di integrità heartbeat incompatibile. È un artefatto di riferimento, non una compilazione di questo Dockerfile. Non attribuire a questa ricetta il suo ID immagine, la provenienza del sorgente o i risultati della scansione.

I repository APT non sono fissati a snapshot. Una futura ricompilazione pulita può quindi selezionare pacchetti del sistema più recenti anche con lo stesso digest della base e hash del wheel Python. Durante la qualificazione annotare revisione esatta del sorgente, builder/piattaforma, ID immagine risultante, versioni dei pacchetti e scansione. La configurazione di produzione deve usare quell’ID immagine immutabile qualificato o il digest.

## Compilare senza disturbare un host USB occupato

Preferire compilazione e scansione su un builder separato. All’host di destinazione serve soltanto l’immagine qualificata, non download di pacchetti o strumenti di compilazione.

Prima di riutilizzare un builder locale predisposto, verificare che l’immagine base esatta sia disponibile e che i livelli di installazione APT e wheel siano nella cache. Caricare soltanto l’immagine finale della PR 128 non dimostra nessuna delle due condizioni. Dalla radice del repository o dalla directory di distribuzione estratta, un tentativo deliberatamente offline è:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` riguarda i passaggi di compilazione; non garantisce in generale che il builder non contatti mai un registro per risolvere la base. Confermare la risoluzione locale della base prima di usare questa procedura su un host con risorse limitate. Se manca un livello APT o wheel, fermarsi invece di consentire silenziosamente una ricompilazione via rete sull’host USB.

BuildKit include la modalità di rete del passaggio nella chiave della cache. Una cache creata con la modalità predefinita può quindi non essere riutilizzata nel tentativo offline precedente. Riutilizzarla solo dopo aver controllato base esatta e catena di istruzioni; fermarsi se un passaggio di installazione viene eseguito invece di segnalare `CACHED`. La rimozione finale di `pip` usa esplicitamente `RUN --network=none`; non richiede rete anche se i passaggi precedenti nella cache conservano la modalità originale.

Prima di modificare la configurazione di produzione, controllare le versioni di Python e ADB, scansionare l’immagine risultante esatta, verificare il trasferimento dei file binari e la preparazione degli APK, quindi eseguire i controlli di accettazione della guida per identità del dispositivo, autorizzazione delle chiavi, sospensione e avvio. ADB 34 deve essere provato con un vecchio dispositivo Android; il numero di versione non dimostra compatibilità KitKat o Fire. Conservare l’immagine attuale e il backup privato della distribuzione finché il rollback non è più necessario.

## Budget di runtime

Il gestore host, non questo Dockerfile, impone 256 MiB di memoria, 256 MiB di memoria più swap e CPU shares 128, senza quote rigide CPU o PID. Impone `--no-healthcheck`, `--network none`, filesystem radice in sola lettura, capability rimosse e mappature esatte dei nodi USB. `/tmp` è un tmpfs limitato che consuma lo stesso budget di memoria. L’adattatore di preparazione scrive blocchi di 1 MiB e limita un singolo payload a 128 MiB; le operazioni condividono il blocco del gestore.

Docker documenta la distinzione tra [controlli di memoria, swap e CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) e [l’opzione `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
