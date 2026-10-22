<div lang="it" dir="ltr">

# Controllo affidabile e trasferimento della riproduzione

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Indice della documentazione](wiki/Home.md)

Queste funzioni richiedono controller e CLI di una revisione che contenga questa guida. Il confronto esatto delle impostazioni e il trasferimento richiedono anche un lettore aggiornato. Gli archivi delle vecchie versioni e la revisione storica della CLI nella guida di installazione non acquisiscono automaticamente queste funzioni. Installare **tutti i file `cli/*.py` insieme**, compreso `preset_session.py`; conservare la configurazione privata.

![Flusso di controllo, checkpoint e trasferimento](assets/reliable-control.svg)

## Abilitare risultati persistenti con limiti definiti

Aggiungere questo blocco facoltativo alla configurazione privata del server, quindi convalidarla e riavviare il server. Scegliere un percorso assoluto su un filesystem locale persistente scrivibile dall’account del servizio. Su Unix la directory deve essere privata (0700); i file di journal e blocco esistenti devono essere file regolari privati (0600). Su Windows limitare l’ACL della directory all’account del servizio. Un solo processo possiede un journal.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Il server crea le directory mancanti con modalità 0700. I limiti sono 1–4096 record, 60–86400 secondi e 4 MiB su disco. La conservazione deve coprire `command_ttl_seconds`. Un journal pieno rifiuta nuove operazioni con chiave anziché eliminare record ancora validi. File corrotti, una directory Unix non sicura, un altro processo scrivente o una scrittura persistente fallita causano un rifiuto sicuro. Il comportamento predefinito rimane il percorso dei comandi esistente in memoria.

L’unità systemd inclusa crea `/var/lib/ottplay-control` con modalità 0700. Per Compose, predisporre una directory host appartenente all’UID 65532, impostare `CONTROL_SERVER_STATE` al suo percorso assoluto e combinare `deploy/compose.yml` con `deploy/compose.journal.yml`. Quest’ultimo aggiunge solo il mount dello stato scrivibile; il filesystem radice rimane in sola lettura. Per Helm, predisporre separatamente una richiesta di volume persistente e impostare `journalExistingClaim=CLAIM_NAME`. La richiesta viene montata in `/var/lib/ottplay-control`; usare la sottodirectory privata mostrata sopra. Il chart mantiene una replica, `Recreate` e `fsGroupChangePolicy: OnRootMismatch`, così un nuovo mount non estende ricorsivamente i permessi delle directory private già create. Verificare che il driver di archiviazione scelto supporti questa semantica di proprietà. Il chart non crea né elimina la richiesta e abilitare un mount non abilita il journal: ciò resta controllato dalla configurazione privata del server.

Per `POST /api/requests`, aggiungere `idempotency_key` accanto a `action` e `params`. I vecchi POST dei comandi accettano invece `Idempotency-Key`. Le chiavi contengono 16–80 lettere ASCII, cifre, trattini bassi o trattini; generare una nuova chiave casuale per ogni intento. Lo stesso proprietario/chiave con lo stesso dispositivo, azione e payload JSON normalizzato restituisce l’ID originale senza accodare nuovamente. Modificare il payload restituisce 409. Le chiavi sono circoscritte all’ID dell’operatore (o all’amministratore), non al dispositivo. Scaduta la conservazione, la chiave può essere accettata come nuovo intento: i client non devono ritentare un vecchio intento fuori da quella finestra.

`GET /api/operations?device_id=DEVICE` indica se il journal è abilitato, i suoi limiti e il TTL dei comandi. Aggiungere `&key=KEY` per leggere un’operazione:

- `pending`: accettata; nessuna risposta ancora conservata.
- `responded`: è arrivata una risposta convalidata dal lettore; `result_status` è `ok`, `rejected` o `unsupported`. Il corpo del risultato originale continua a scadere normalmente.
- `acknowledged`: un consumer precedente ha confermato il comando. Questo non prova la riproduzione visibile o il salvataggio di una modifica alle impostazioni.
- `unknown`: il termine è scaduto o il server si è riavviato prima di conservare una risposta. Il server **non ripete mai** questo comando dopo il riavvio.

Un POST ripetuto con chiave restituisce `status: retained`, l’ID originale e lo stato. I normali nuovi POST mantengono `status: queued`. I record del journal contengono solo proprietario, dispositivo, azione, ID casuali, digest del payload, stato e scadenze. Non contengono payload dei comandi, URL dei fornitori, token, screenshot o corpi delle risposte. I comandi in attesa non vengono salvati persistentemente. Si tratta di deduplicazione e monitoraggio dei risultati con limiti definiti, non di esecuzione esattamente una volta, coda di lavoro persistente o alta disponibilità multiserver.

## Pianificare, applicare, riprendere e verificare Local/Home

Il comportamento esistente di `ott PLAYER load PRESET` viene mantenuto. Per il nuovo flusso:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` funziona offline: convalida il preset locale ed elenca gli slot delle impostazioni e il profilo attivo desiderato senza stamparne il contenuto. Non ispeziona il lettore. `verify` esegue confronti esatti in sola lettura e controlla il profilo M3U selezionato. Restituisce un codice diverso da zero se qualcosa differisce o il confronto non è supportato.

`apply` richiede il journal del server. Crea un checkpoint privato e confronta ogni fornitore configurato prima della prima modifica. Ogni salvataggio confronta i byte memorizzati con i valori attesi tramite `settings_compare`; una corrispondenza evita la scrittura. La verifica finale copre Plex, tutti gli slot M3U, gli slot Stalker facoltativi e il profilo M3U selezionato. I confronti restituiscono un booleano, mai i segreti memorizzati.

Il checkpoint predefinito è sotto `~/.config/ottplay-control/checkpoints/`. Usare `--checkpoint /absolute/private/path/session.json` dopo la modalità per scegliere un altro file. I checkpoint contengono dispositivo, nome del preset, hash della revisione server/preset, nomi dei passaggi completati e una chiave casuale dell’operazione in attesa con scadenza. Non contengono valori delle impostazioni. I file vengono sostituiti atomicamente dopo fsync e bloccati contro scritture CLI concorrenti. Tenere disponibile il preset locale corrispondente. Su Unix la directory deve essere privata (0700), con file regolari in 0600. Su Windows limitare l’ACL ereditata della directory all’utente che esegue la CLI; i bit di modalità Unix non impongono i permessi di accesso Windows.

`resume` convalida lo stesso server, dispositivo e revisione del preset, poi legge il risultato in attesa. Una risposta conservata consente un nuovo controllo immediato. Un risultato incerto blocca l’invio di modifiche finché non siano trascorsi la scadenza del comando precedente, il timeout della richiesta e un piccolo margine. Eseguire di nuovo `resume` dopo tale intervallo. Ricontrolla le impostazioni memorizzate anche per i passaggi completati, affinché le modifiche dell’utente non siano scambiate per precedenti scritture riuscite. Non ripete mai alla cieca un comando relativo. La selezione assoluta di fornitore/profilo può essere ripetuta dopo la protezione temporale.

Un nuovo `apply` non sovrascrive mai un checkpoint esistente. Riutilizzarlo con `resume` oppure scegliere un nuovo file per un preset modificato. Dispositivi diversi hanno checkpoint separati. Non esiste una transazione o un rollback che copra tutti i lettori; le precedenti scritture riuscite restano valide se un passaggio successivo fallisce. `verified` conferma i valori memorizzati dal lettore al momento del confronto, non il caricamento di rete riuscito né l’uscita fisica video/audio.

## Assegnare alle integrazioni solo l’autorità necessaria

Usare [operatori di comando ordinari](security-design.md#scoped-command-operators) per HA, un bridge RF o un agente anziché distribuire `admin_token`. Consentire ID esatti dei dispositivi e operazioni come `input.channel_up` e `input.channel_down`. Le operazioni sulle impostazioni richiedono esplicitamente i permessi `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` e `provider_settings` per questo flusso di preset. Il trasferimento richiede `playback_handoff_export` sulla sorgente e `playback_handoff_import` sulla destinazione. Una voce operatore combina dispositivi e azioni elencati; usare identità separate quando la stessa operazione non deve applicarsi a tutti i dispositivi elencati. Token e consenso alla diagnostica rimangono autorità separate.

Gli operatori non possono interrogare le code dei lettori, inviare risposte dei lettori, elencare tutti i dispositivi, leggere l’output di debug amministrativo o il risultato di un altro operatore. Restringere l’ambito di un’azione impedisce anche di leggere la sua operazione conservata. Rimuovere un operatore e riavviare ne revoca la credenziale. Non riutilizzare un ID operatore per un altro soggetto finché rimangono i vecchi record del journal.

## Continuare su un altro lettore

```sh
ott handoff living-room bedroom
```

La sorgente esporta un riferimento live/archivio versione 1: fornitore, identità opaca della sorgente, impronta opaca dell’elemento `channel-v1:<8 hex>.<8 hex>` e, per l’archivio, l’orario corrente del programma. Gli ID grezzi dei fornitori e gli URL degli stream rimangono locali. La destinazione deve avere **la stessa sorgente/account configurato**, risolvere un unico elemento corrispondente nel proprio catalogo e ottenere un nuovo URL con le proprie credenziali locali. Il controller non copia un URL riproducibile né cambia le credenziali della destinazione. La sorgente continua a riprodurre. Un risultato CLI riuscito significa che la destinazione ha inoltrato la richiesta, non che lo schermo o gli altoparlanti fisici siano stati verificati.

La versione 1 supporta cataloghi live/archivio `m3u`, `xtream` e `stalker` quando il lettore può fornire le identità richieste. Elementi sconosciuti o ambigui, sorgenti non corrispondenti e rifiuti delle politiche locali falliscono senza cercare un’alternativa per nome. VOD/serie, associazioni tra account diversi, pausa della sorgente e sincronizzazione continua di cronologia/preferiti non fanno parte di questo primo contratto.

## Esercizi di convalida e recupero

Eseguire `go test -race ./...`, `go vet ./...` e `python3 -m unittest discover -s tests`. I test coprono risposte perse dopo una scrittura riuscita, confronto esatto prima della ripresa, deriva della configurazione, scadenze di operazioni sconosciute, due processi scriventi, journal corrotti, letture tra operatori e payload di trasferimento malformati. Su un lettore di staging interrompere inoltre la CLI dopo il salvataggio di un’impostazione, riavviare il controller, riprendere e verificare; provare un trasferimento tra sorgenti corrispondenti e non corrispondenti. Confermare video/audio separatamente.

Per la convalida locale tra repository, con il checkout FOSS corrente e le dipendenze di sviluppo installate, eseguire:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Lo smoke test compila questo server Go e l’intero progetto TypeScript FOSS, ed esegue il trasporto reale, i gestori dei comandi, l’identità della sorgente e il codec delle playlist distribuito contro la CLI Python. Controlla impostazioni esatte per tutti e tre i fornitori supportati, discrepanze, trasferimento live con ID di righe locali diversi, offset di ricerca nell’archivio, risoluzione URL fallita e validazione rigorosa del formato trasmesso. Solo archiviazione, politiche dei fornitori e interfacce dell’host di riproduzione sono fixture di test; non verifica browser, uscita multimediale fisica o accesso reale agli account. Si associa al loopback, usa file temporanei privati, non contatta URL degli stream e rimuove file e server all’uscita.

</div>
