<div lang="it" dir="ltr">

# OTA Android: lettori normali e agente root separato

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Per **k8a/fire8 e altri lettori Capacitor**, usare l’aggiornamento Wi-Fi del lettore normale, non il comando dell’agente root:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Omettendo `--version` si seleziona l’ultima beta pubblicata. Usare `full` invece di `api22` su Android 7+. La CLI individua la versione ufficiale, controlla il manifesto, prepara l’APK e ne interroga lo stato prima di richiedere una sola volta l’installazione. Android può richiedere conferma sul tablet. Disabilitare esplicitamente il chiosco e sbloccare le impostazioni protette prima della manutenzione; il comando non modifica mai queste politiche. Vedere la [procedura CLI completa](cli.md#capacitor-apk-updates-over-wi-fi).

Servono un APK già installato che supporti `AppUpdate`, Wi-Fi e un lettore in esecuzione collegato al controller. I normali aggiornamenti successivi non richiedono USB, ma non possono raggiungere un tablet offline o una WebView arrestata. Prima installazione, aggiornatore non disponibile, app danneggiata o rete persa possono ancora richiedere recupero locale. Non si implica alcun pianificatore automatico dei rilasci né un installatore silenzioso generale.

Il resto della guida descrive un **diverso agente di manutenzione a1 con root**, limitato all’installazione Matricom/API19 predisposta `play.ott.kitkat`. Non è già installato né utilizzabile sui normali dispositivi Fire/Capacitor.

Dopo la configurazione iniziale USB con root, l’agente Android indipendente riceve aggiornamenti firmati tramite la propria connessione HTTPS in uscita. Non gli serve una WebView funzionante per aggiornarsi. L’agente 0.1.2 supporta anche un pacchetto coordinato APK/helper tattile di sistema, con file di recupero salvati e verifica prima della conferma definitiva.

La guida descrive l’intero percorso. Per campi esatti del manifesto, limiti e requisiti hardware, usare il [riferimento dell’agente nativo](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Componenti aggiornati

![Architettura OTA: i byte della versione revisionata vengono firmati, pubblicati tramite HTTPS diretto, verificati dall’agente indipendente e controllati dopo l’installazione. Il deployment web è separato.](images/ota-architecture.svg)

- **Agente nativo:** `kind: agent` sostituisce il proprio binario ARMv7. Funziona anche quando OTT è disabilitato e si usa un’altra applicazione HOME.
- **Solo APK:** `kind: apk` sostituisce il wrapper mantenendone i dati. OTT deve essere abilitato e in primo piano. L’helper integrato nel candidato deve corrispondere esattamente all’helper di sistema installato.
- **APK e helper tattile:** `kind: bundle` aggiorna entrambi i componenti come coppia. Un agente vecchio deve prima ricevere un aggiornamento del solo agente che supporti i bundle.
- **Lettore ospitato:** distribuire l’artefatto web verificato all’origine esistente, quindi ricaricare e controllare un lettore attivo. L’aggiornamento APK non distribuisce il JavaScript ospitato. Vedere il [flusso di rilascio](release-workflow.md).
- **Controller e CLI:** distribuire il controller e installare separatamente l’intero insieme di moduli CLI. Aggiornare uno dei due non sostituisce il codice sul tablet.

Firmware, kernel, WebView di sistema, broker root e script di avvio sono fuori da questo formato OTA. Il [riferimento nativo](../native/android-agent/README.md#bootstrap) descrive i confini di inizializzazione e recupero. Le operazioni OTA supportate non richiedono ADB di rete né una shell remota arbitraria.

## Dalla versione revisionata all’installazione confermata

1. **Selezionare byte verificati.** Integrare modifiche revisionate dopo il superamento della CI. Scaricare l’artefatto voluto e verificarne revisione sorgente, evidenze immutabili di rilascio e checksum. Estrarre il binario `agent` dall’archivio nativo; l’archivio stesso non è un payload agente installabile.
2. **Preparare e firmare.** Usare la chiave privata Ed25519 esistente la cui chiave pubblica è stata fissata durante l’inizializzazione. Mantenere privati chiave e credenziali del dispositivo.
3. **Pubblicare e controllare il trasporto.** Pubblicare soltanto payload e `signed.json` su percorsi HTTPS immutabili. Entrambi devono restituire direttamente i byte con HTTP 200; l’aggiornatore rifiuta i reindirizzamenti. Eseguire il comando `verify` dello strumento di creazione pacchetti.
4. **Inviare un aggiornamento.** Registrare l’ID della richiesta e controllare lo stato dopo la conferma. Una risposta persa o incerta non giustifica la ripetizione cieca di un’operazione.
5. **Confermare l’effetto.** Confrontare hash e versioni installati rilevati nuovamente con il rilascio preparato. Per un lettore attivo verificare anche destinazione del chiosco, coda e avanzamento della riproduzione. Immagine e suono fisici richiedono osservazione.

Esempio per un rilascio dell’agente; sostituire versione scelta e percorsi reali:

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

`ENVELOPE_SHA256` è l’`envelope_sha256` dello strumento di creazione pacchetti, non l’hash del payload. `accepted` indica ammissione, non installazione riuscita. L’autosostituzione può lasciare una vecchia ricevuta di operazione su `unknown`; nuovo runtime, versione agente prevista e hash installato corrispondente costituiscono la prova di installazione. Il [riferimento CLI](cli.md#independent-android-agent) descrive instradamento nativo indipendente e gestione delle ricevute.

## Recupero della transazione APK/helper

![Transazione APK/helper: controlli preliminari, salvataggio della coppia precedente, applicazione, verifica e conferma; i guasti ripristinano la coppia precedente e un recupero incompleto mantiene bloccate le modifiche all’app.](images/ota-transaction.svg)

Il bundle contiene esattamente `app.apk` e `ott-touch-guard`. Lo strumento può crearlo direttamente dal wrapper APK firmato usando `--kind bundle --apk`. Estrae l’helper integrato, così le due copie distribuite corrispondono. Vedere [comando e requisiti del bundle](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Prima di modificare file, l’agente richiede una coppia installata corrispondente, spazio libero sufficiente, lettore OTT reattivo in primo piano, partizione di sistema in sola lettura e supporto dichiarato al downgrade APK. Per un chiosco bloccato verifica lo stesso ID del canale TV o gli ID VPortal ordinati; la sola uguaglianza della lunghezza delle code non basta. Le code bloccate non supportate sono rifiutate. Viene rifiutata anche un’app OTT disabilitata intenzionalmente, preservando l’attuale applicazione HOME dell’utente.

File di recupero e transazione in attesa vengono salvati persistentemente prima di fermare l’app. L’agente attende che app e vecchio helper si fermino, sostituisce l’helper atomicamente, ripristina `/system` in sola lettura, installa l’APK e controlla il risultato. Un errore di applicazione o verifica attiva il ripristino della coppia precedente. Il recupero all’avvio ripristina i file senza avviare OTT né assumere il controllo di HOME.

Se il recupero non può terminare, `updates.recovery_failed` è vero. Diagnostica e riparazione firmata del solo agente restano disponibili; modifiche all’app e watchdog di riproduzione sono bloccati. Non eliminare la transazione salvata per forzare l’installazione. Il recupero dei binari non annulla migrazioni dei dati dell’app e il firmware può ancora rifiutare un downgrade. Vedere i [limiti del recupero](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Risultati e registro di accettazione

![Grafico di validazione: 488 test CLI, 576 test degli strumenti di rilascio e 6 test del pacchettizzatore OTA; il tablet di riferimento usa il nuovo agente verificato, mentre l’accettazione fisica APK/helper è ancora in attesa.](images/ota-validation.svg)

L’audit del 14 ottobre 2026 ha superato 488 test CLI (uno saltato), 576 test degli strumenti di rilascio, sei test del pacchettizzatore OTA, controlli nativi Go race/statici e una build ARMv7. Questi conteggi descrivono la dimensione delle suite, non copertura o prestazioni. I test con iniezione di guasti esercitano archivi non validi, transazioni interrotte, ripristino parziale, cambi di destinazione e blocchi di recupero. La revisione indipendente ha anche individuato e corretto un controllo insufficiente dell’identità della coda e una validazione APK/helper differente tra host e dispositivo.

Situazione verificata il 15 ottobre 2026:

- L’implementazione OTA è integrata nella [PR del controller #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) e inclusa in [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- I pacchetti firmati attuale e precedente dell’agente sono pubblicati sull’[host di distribuzione OTA](https://android-updates.ottplay.here.now/). I byte di payload e busta sono stati scaricati e confrontati con successo tramite HTTPS diretto.
- Il tablet di riferimento è passato da `0.1.1-beta.21` a `0.1.2-beta.4` tramite il canale OTA indipendente. Versione dell’agente in esecuzione e SHA-256 installato corrispondevano al rilascio scelto; il runtime è cambiato e l’ID di avvio del sistema è rimasto invariato. OTT continuava a non avere un processo app attivo e non è stata inviata alcuna operazione di avvio app o selezione HOME.
- Lo stato nativo riportava l’hash dell’helper tattile di sistema, nessun bundle in attesa e nessun errore di recupero. I metadati APK non erano disponibili, quindi l’osservazione non dimostra versione o hash dell’APK installato.
- La transazione coordinata APK/helper ha validazione lato host; installazione fisica, recupero da downgrade, immagine e audio non sono ancora accettati sul tablet di riferimento.

Per ogni distribuzione successiva conservare sorgente/checksum del rilascio, hash della busta preparata, ID richiesta, stato nativo prima/dopo e risultato separato dell’accettazione fisica. Un download riuscito o una ricevuta del comando non sostituiscono queste osservazioni.

</div>
