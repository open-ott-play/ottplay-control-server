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

[Documentazione / Wiki](docs/wiki/Home.it.md)

Un piccolo server da ospitare autonomamente che consegna comandi remoti ai lettori OTT-play. I lettori effettuano richieste HTTP in uscita, quindi il televisore non deve esporre una porta in ascolto. Ogni lettore ha una coda e un token di accesso propri; i comandi vengono inviati con un token amministratore separato.

Il [controllo affidabile](docs/reliable-control.md) opzionale aggiunge credenziali di integrazione con ambito limitato, conservazione persistente ma limitata dei risultati dei comandi, applicazione riprendibile delle configurazioni Local/Home con verifica esatta delle impostazioni e passaggio esplicito tra riproduzione in diretta e da archivio. Queste funzioni richiedono revisioni del controller e della CLI contenenti questa guida. La verifica esatta e il passaggio richiedono anche un lettore aggiornato. La revisione di installazione precedente indicata sotto non comprende queste funzioni. Il [diagramma di architettura](docs/assets/reliable-control.svg) mostra dove rimangono impostazioni, credenziali e stato delle operazioni.

Il server viene eseguito come unico eseguibile Go portabile, con una libreria DNS a versione fissata per la ricerca facoltativa dei servizi. I pacchetti coprono Linux amd64, arm64 e ARMv7; macOS Intel e Apple Silicon; Windows amd64 e arm64. Le immagini container supportano Linux amd64 e arm64. Kubernetes e k3s utilizzano il chart Helm incluso.

## Avviare un server

Scaricare l'archivio appropriato dalle [release](https://github.com/open-ott-play/ottplay-control-server/releases), verificarlo rispetto al manifesto della release ed estrarlo. In Windows, usare `ottplay-control-server.exe` nei comandi seguenti.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` crea credenziali casuali e distinte in un nuovo file accessibile solo al proprietario su Unix. Rifiuta di sovrascrivere file o collegamenti simbolici esistenti e non stampa mai le credenziali. Mantenere privato il file; in Windows limitarne l'ACL all'account che esegue il server. `config.example.json` documenta i campi e contiene intenzionalmente token segnaposto non validi.

L'indirizzo predefinito è `0.0.0.0:8081` (tutte le interfacce IPv4, inclusa la LAN). Collegare i televisori usando indirizzo LAN e porta del server. Le configurazioni esistenti conservano il valore esplicito di `listen`; modificarlo in `0.0.0.0:8081` o usare `--listen 0.0.0.0:8081` per rendere raggiungibile un'installazione prima limitata al loopback. Aggiungere l'**origine esatta della pagina** del lettore a `allowed_origins`, per esempio `http://192.168.1.20:8443`. Schema, nome host e porta devono corrispondere; non aggiungere percorsi. Un elenco di origini vuoto consente solo client non browser. Le modifiche alla configurazione diventano effettive dopo il riavvio. Le credenziali del dispositivo e dell'amministratore restano obbligatorie.

HTTP è supportato per i vecchi browser TV in una LAN affidabile. Usare HTTPS quando la connessione attraversa una rete non affidabile: `serve --config config.json --tls-cert server.crt --tls-key server.key`, oppure terminare TLS su un reverse proxy. Le pagine del lettore servite via HTTPS non possono collegarsi nel browser a un server HTTP non cifrato. Anche le applicazioni native mantengono la politica di trasporto del sistema operativo; questa impostazione non la allenta.

HTTPS diretto verifica la catena di certificati fornita prima di aprire il listener: le chiavi RSA devono essere di almeno 2048 bit e quelle ECDSA di almeno 224 bit; è supportato anche Ed25519. Sostituire i certificati con chiavi troppo piccole prima dell'aggiornamento. Vedere la [politica dei certificati TLS e il suo ambito](docs/security-design.md#direct-https-certificate-policy).

## Collegare il lettore

Usare una release del lettore che includa la connessione al server di comandi. Aprire **Impostazioni → Controllo remoto** e inserire:

1. **Indirizzo server:** IP o nome host, eventualmente con porta, oppure un indirizzo completo `http://` / `https://`. Un host senza schema usa per impostazione predefinita HTTP sulla porta 8081.
2. **Codice di accesso:** il `token` della voce del lettore in `devices`, mai `admin_token`.
3. **Connetti:** attivare il polling e controllare lo stato mostrato nella stessa finestra.

Il lettore memorizza la connessione soltanto nell'installazione corrente e la esclude dai backup delle impostazioni e dal trasferimento cloud. Non richiede il listener HTTP locale separato. I browser web richiedono un'origine CORS consentita; le pagine TV impacchettate con `Origin: null` possono usare l'impostazione esplicita `allow_null_origin` per le route previste di dispositivo/bootstrap. Ciò non abilita le route amministrative o dell'operatore diagnostico.

## Telecomando da terminale

La [guida illustrata Android OTA](docs/android-ota.md) distingue i normali aggiornamenti Capacitor via Wi-Fi (`ott DEVICE update --variant api22|full`, ultima beta per impostazione predefinita; `--version` seleziona una release) dall'agente separato con accesso root. Spiega gli aggiornamenti firmati dell'agente e di APK/helper, il ripristino e la verifica del risultato installato.

La [guida Android ADB](docs/android-adb.md) tratta accesso USB tramite workstation o Synology, controllo di schermo e tastiera, screenshot, log, installazione APK e riavvio normale, comprese le limitazioni dei tablet meno recenti.

Il [rilevamento e abbinamento DNS-SD](docs/discovery.md) opzionale consente ai lettori di trovare il controller tramite il proprio dominio di rete e ricevere un token dispositivo individuale dopo `ott approve NAME CODE`. DNS contiene soltanto metadati del servizio. Le connessioni manuali esistenti continuano a funzionare senza attivare il rilevamento.

Usare la CLI Python per alias dei lettori, volume in tempo reale, ricerca canali, EPG corrente, selezione del provider e configurazione dei provider supportati: [guida CLI](docs/cli.md). Server e lettore devono entrambi includere l'estensione richiesta/risposta.

La CLI Python viene installata da una copia dei sorgenti, separatamente dall'archivio del server nativo. Le [istruzioni di installazione e configurazione](docs/cli.md#installation-and-connection) coprono macOS/Linux e Windows, configurazione di Python/PATH e moduli CLI richiesti. Fissano un commit di sviluppo 0.1.1 verificato con workbench e verifica offline; la CLI stabile v0.1.0 e gli archivi del server nativo non contengono questi comandi. [Aggiornare o ripristinare la CLI](docs/cli.md#update-select-or-roll-back-the-cli) senza sostituire una copia di lavoro, un launcher o una configurazione privata esistenti. I launcher locali possono usare il [risolutore di playlist](docs/cli.md#local-playlist-resolver) in sola lettura per cercare canali, programmi correnti e archivi riproducibili senza controller. Dopo installazione e configurazione:

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

`a1` è un alias di esempio di un lettore registrato, non il nome host di un computer. Vedere [registrazione e abbinamento](docs/cli.md#register-and-connect-a-player), [tutti i comandi con esempi](docs/cli.md#commands), [disconnessione e revoca dell'accesso](docs/cli.md#disconnect-deregister-or-revoke-a-player) e [risoluzione dei problemi](docs/cli.md#troubleshooting).

Per un lettore sempre acceso, usare [standby e go](docs/cli.md#stop-playback-while-keeping-the-player-online) per interrompere la riproduzione e riattivarla più tardi senza uscire dall'app. `stop` resta un alias di `standby`; `start` e `wake` restano alias di `go`. Il controllo remoto deve rimanere connesso, quindi standby non garantisce traffico di rete nullo. `ott a1` mostra le operazioni dichiarate dal lettore. Un comando accettato non conferma che lo spegnimento o l'avvio della riproduzione siano terminati.

I [comandi del rapporto d'aspetto](docs/cli.md#aspect-ratio-fit-and-fill) richiedono CLI e controller aggiornati e un lettore che dichiari la capacità `aspect`. La modifica viene applicata dopo la conferma; leggere nuovamente `aspect` per verificare modalità e salvataggio. Fill ritaglia proporzionalmente anziché deformare l'immagine.

Gli [screenshot](docs/cli.md#remote-screenshots) remoti richiedono CLI e controller aggiornati e un lettore con adattatore di acquisizione supportato. Attivare la connessione Controllo remoto del lettore autorizza screenshot e diagnostica per quel controller; gli screenshot nativi non richiedono un interruttore di autorizzazione separato. I browser richiedono ancora il selettore locale della sorgente. Le immagini possono includere impostazioni, schermate PIN e credenziali visibili: collegarsi solo a controller affidabili. `ott a1` indica disponibilità ed eventuale necessità di selezionare la sorgente. Gli screenshot sono separati dalla raccolta di telemetria strutturata.

Per mescolare tutti i video VPortal corrispondenti a un titolo e riprodurli in ciclo:

```sh
ott l vpr "wedding"
```

`vpr` mescola una volta l'intera selezione e poi ripete quella coda. Eseguirlo nuovamente per un nuovo ordine casuale. `vp` riproduce le corrispondenze nell'ordine del catalogo; `vp --list` le elenca senza cambiare la riproduzione. Vedere i [comandi VPortal](docs/cli.md#commands).

Per una sequenza Plex esplicita usando la configurazione Plex salvata del lettore:

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

La CLI attende fino a 45 secondi per impostazione predefinita. Scrivere `ott -t 45 l plex shuffle "Tom and Jerry"` rende esplicito lo stesso timeout di risposta; non limita la durata di riproduzione dei video. Raccogliere grandi librerie Plex può richiedere più di un breve timeout di 10–15 secondi. Inserire `-t SECONDS` (o `--timeout SECONDS`) prima dell'alias del lettore. Vedere i [timeout delle librerie Plex](docs/cli.md#plex-libraries-by-name) per esempi di anteprima e indicazioni quando una richiesta di riproduzione scade.

Le code Plex richiedono CLI, controller e lettore che implementino `plex_queue`. `plex play --shuffle ID...` mescola una volta gli ID forniti. Le code si fermano dopo l'ultimo elemento, salvo ripetizione prevista da una politica kiosk esistente. Finché esiste una coda, i normali `next` e `prev` agiscono su di essa; `plex stop` la svuota. Un'anteprima non cambia mai provider né sostituisce la riproduzione corrente. Vedere i [comandi delle code Plex](docs/cli.md#ordered-plex-queues) per limiti e disponibilità. La ricerca di librerie richiede inoltre lettore e controller aggiornati che dichiarino `plex_library`; usa la connessione Plex salvata del lettore e rifiuta selezioni ambigue o incomplete. Vedere le [librerie Plex per nome](docs/cli.md#plex-libraries-by-name).

### Salvare profili di video locali

Con lettore e controller attuali, file/cartelle locali usano il provider `offline`:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Collegare prima file/cartelle sul lettore. Il salvataggio registra configurazione e video selezionato senza avviare la riproduzione. L'esportazione trasferisce solo descrizioni; contenuti e autorizzazioni di accesso del sistema/browser restano sul dispositivo. Un'altra installazione richiede un'associazione locale e il caricamento non implica una riproduzione confermata. Vedere i [profili di video locali](docs/cli.md#local-videos-profiles) per configurazione, capacità, limiti del formato portabile e risoluzione dei problemi.

### Salvare l'elemento corrente tra profili

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

I preferiti rimangono sul lettore di destinazione durante i normali riavvii. Selezionarne uno carica provider/profilo salvato e risolve un collegamento nuovo; una sorgente cambiata o un elemento mancante causa un errore senza sostituirlo con un altro canale o film. CLI, controller e lettore devono supportare `favorites`; `ott l` mostra le operazioni disponibili. Vedere i [preferiti tra profili](docs/cli.md#favorites-across-profiles) per alias, limiti, semantica delle conferme e risoluzione dei problemi.

### Caricare una configurazione salvata

Memorizzare configurazioni con nome nella sezione privata `presets` di `~/.config/ottplay-control/cli.json`. Ogni configurazione contiene profili M3U numerati (playlist e ore di archivio), credenziali Plex e il profilo M3U da selezionare al termine del caricamento. Vedere l'[esempio di configurazione](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

La CLI verifica prima l'intera configurazione, poi attende la conferma del lettore prima di inviare ogni modifica successiva. Gestisce il caricamento del provider, salva Plex e gli slot M3U e seleziona il profilo M3U configurato. Un errore interrompe la sequenza e riporta i passi confermati; una scrittura dall'esito incerto non viene mai ripetuta automaticamente. Non servono servizi profili aggiuntivi né il riavvio del controller. Mantenere privata questa configurazione: contiene credenziali dei provider.

## Inviare un comando

Questo esempio legge il token amministratore dalla configurazione locale privata senza inserirlo nell'URL o negli argomenti della riga di comando:

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

Altri comandi selezionano un canale per numero o nome, scelgono un canale casuale, cambiano provider, mostrano un messaggio, modificano una playlist M3U dove supportato o chiudono il lettore. La disponibilità dipende dalla piattaforma del lettore e dal provider corrente. Vedere il [contratto API](docs/api.md).

Con un lettore che supporta il provider VPortal autonomo, `ott a1 provider
vportal` seleziona i suoi 15 profili dedicati. Usare `profile N vportal LINK`, `profile N
name NAME` o un file privato `profile-config N FILE` contenente `name` e `vportal`, poi selezionare lo slot configurato con `profile N`. I profili M3U esistenti restano separati. `profiles` restituisce metadati senza esporre collegamenti dell'account o chiavi.

Dopo aver avviato un film o una coda di episodi VPortal, `ott a1 kiosk on` blocca quella selezione. Il lettore conserva coda e posizione tra i ricaricamenti e risolve nuovamente gli URL dei contenuti per il ripristino. Usare `kiosk off` per scegliere un altro titolo o cambiare profili, poi `kiosk on` per bloccare. La ricevuta kiosk mostra titolo e numero di episodi; richieste al provider e URL dei flussi restano sul lettore.

Usare `ott a1 kiosk on --strict` per la riproduzione destinata ai bambini: un tocco locale mostra la barra inferiore del video. I lettori core aggiornati consentono di toccare o trascinare la linea temporale per spostarsi nell'episodio VPortal/Plex corrente; gli altri controlli locali restano bloccati. Lettori precedenti, kiosk di canali e wrapper che intercettano i gesti della linea temporale mantengono una barra in sola lettura. Un blocco esistente conserva il proprio obiettivo. Rimuoverlo dal controller con `ott a1 kiosk off`. Sui siti web Android, impedire l'uscita tramite il sistema richiede il blocco separato dell'app con PIN; vedere la [modalità kiosk](docs/cli.md#kiosk-mode).

## Diagnostica remota

Iniziare dalla [guida rapida del workbench in sola lettura](docs/workbench.md#quick-start) per `doctor`, `inspect` di interfaccia/media, ricerca delle ricevute delle operazioni, pacchetti di evidenze, `health`, `media-progress` web/nativo e `report verify` offline. Usa la configurazione CLI amministratore e gli alias esistenti. Il percorso nativo è destinato all'agente di manutenzione Android predisposto separatamente; i lettori Tauri usano l'ispezione web. I rapporti distinguono l'avanzamento del decoder dalla verifica fisica di schermo e audio. Vedere la [risoluzione dei problemi del workbench](docs/workbench.md#troubleshooting) per capacità mancanti, percorsi non disponibili ed evidenze incomplete.

La [diagnostica del protocollo 2](docs/remote-diagnostics.md) opzionale fornisce sessioni attivate esplicitamente per ogni ambiente di esecuzione, con credenziali operatore separate ad ambito limitato e telemetria strutturata limitata. La connessione attiva del lettore fornisce l'autorizzazione locale senza un'altra richiesta di fiducia; ogni acquisizione mantiene comunque una durata di sessione limitata. Il polling del controllo diagnostico è indipendente dalla coda di comandi esistente.

La [guida CLI diagnostica e MCP](docs/diagnostics-cli.md) tratta installazione, credenziali operatore, connessione, ogni comando/strumento diagnostico, esempi di acquisizione e riparazione, revoca degli ambienti di esecuzione e risoluzione dei problemi. `ott diagnostics --help` elenca questa famiglia separata di comandi; alias ordinari dei lettori e credenziali amministratore non concedono accesso diagnostico.

## Pacchetti di distribuzione

- [Tablet Android: kiosk Plex rigoroso e avvio](docs/android-kiosk-setup.md)
- [Gestione USB Android condivisa e ripristino all'avvio](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS e Windows](docs/deployment.md)
- [Configurazione, semantica di consegna e limiti](docs/api.md)
- [Processo di rilascio e verifica](docs/development.md)

Le code sono limitate, scadono dopo 60 secondi per impostazione predefinita e risiedono in memoria. Riavviare il server elimina i comandi in attesa. Eseguire una sola replica; repliche indipendenti non condividono le code. Un lettore conferma un tentativo di consegna, non la riproduzione effettiva o il volume hardware. I tentativi di rete ripetuti vengono deduplicati nella sessione del lettore in esecuzione, non tra riavvii dell'applicazione.

Questo server segue l'API comandi dell'esempio opzionale `local_proxy.py` in [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), con credenziali amministratore/dispositivo separate e consegna confermata. Il vecchio proxy rimane un componente distinto. Questo progetto non fa da proxy ai contenuti e non modifica la rete HLS, EPG o dei provider del lettore.

## Licenza

MIT. Le attribuzioni degli strumenti di rilascio inclusi si trovano in `NOTICE` e `LICENSES/`.

## Manutenzione del progetto

Vedere i [requisiti di contributo e test](CONTRIBUTING.md), la [politica di segnalazione della sicurezza](SECURITY.md), il [progetto della sicurezza](docs/security-design.md) e le [evidenze OpenSSF e i criteri rimanenti](docs/openssf-evidence.md).

Le code Plex accettano fino a 500 ID nei lettori attuali. Usare `ott f10 plex play --shuffle ID...` per mescolare una volta, poi `ott f10 kiosk on --strict` con il provider Plex selezionato per ripetere e bloccare la coda. `ott f10 kiosk off` rimuove il blocco. I lettori precedenti mantengono il limite di coda dichiarato.

## Fuzzing delle release candidate

Le release candidate pubbliche eseguono fuzzing nativo limitato dei confini di input descritti nella [guida al fuzzing](docs/fuzzing.md). Le campagne di mutazione non vengono eseguite su PR, beta o build notturne. La guida copre ambito, budget, evidenze e riproduzione delle regressioni.

## Comunità ed evidenze di sicurezza

Vedere [contributi](CONTRIBUTING.md), [governance](GOVERNANCE.md), [codice di condotta](CODE_OF_CONDUCT.md), [roadmap](ROADMAP.md) ed [evidenze OpenSSF](docs/openssf-evidence.md). L'indice distingue profili di sicurezza supportati, risultati effettivi dei test e requisiti Silver rimanenti.
