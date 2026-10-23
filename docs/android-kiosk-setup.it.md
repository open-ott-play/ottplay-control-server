# Tablet Android: chiosco Plex rigoroso e avvio

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Guida ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Gestore USB](android-usb-manager.md)

Un tablet dedicato richiede tre impostazioni verificate separatamente: coda dei contenuti, politica chiosco rigorosa di OTT e avvio Android. Installare un APK o verificare spegnimento/ripresa dello schermo non dimostra l’avvio dopo un riavvio del sistema.

## Identificare e installare

1. Registrare tablet fisico, alias/UUID OTT, alias del gestore USB, seriale, modello, ID Android e SDK. Gli alias OTT e USB possono differire; non dedurre l’identità dall’alias né riutilizzare il token di un altro tablet.
2. Usare la variante APK ufficiale compatibile con lo SDK, verificare SHA-256 pubblicato e identità di pacchetto/firma, installare con `install -r`. Preservare i dati. Usare il [gestore condiviso](android-usb-manager.md) quando possiede USB; non avviare un server ADB concorrente né comandi `adb` senza dispositivo specificato.
3. Associare il lettore con le proprie credenziali del controller e verificare `ott TABLET`. Caricare il preset denominato desiderato ed eseguire `ott TABLET load PRESET verify`. I preset domestici e quello localhost di una macchina di sviluppo non sono intercambiabili.

## Verificare Plex dal tablet

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Verificare ID, titolo e numero di elementi della libreria. Una richiesta riuscita dal computer dell’amministratore non prova la connettività del tablet. In caso di errore controllare sul tablet indirizzo salvato, instradamento, supporto TLS e credenziali. Preferire l’endpoint HTTPS valido del server. Se un vecchio firmware richiede HTTP sulla LAN privata, limitarlo a questa installazione, registrare che token e contenuti transitano senza cifratura e non modificare preset condivisi né disabilitare globalmente la verifica dei certificati. Non pubblicare token o URL di contenuti privati in diagnostica o documentazione.

Nei lettori più recenti che espongono `categories` in `caps`, Plex apre tutti i video senza richiedere la selezione delle cartelle. Usare `ott TABLET categories` e `ott TABLET category all`; vedere [categorie multimediali](cli.md#media-categories-and-stable-video-selection). I passaggi di selezione delle cartelle riportati sotto riguardano la versione precedente indicata esplicitamente.

Cambiare l’indirizzo del server Plex cancella le radici delle librerie selezionate. Scegliere il fornitore **Plex**, aprire **Plex settings → Folders**, selezionare la riga della libreria desiderata (non solo **Browse folders**), scegliere **Ok**, poi **Save and open library**. Nel lettore `1.1.58-beta.19` le sole credenziali possono aprire il selettore mentre `ready` resta false; completare la selezione prima dei comandi della coda. Non passare a M3U solo per ottenere `ready: true`: una coda Plex può riprodursi lì, ma `kiosk on --strict` può salvare una politica M3U in attesa senza media associati. Non blocca né ripristina la coda Plex.

## Avviare e bloccare la coda

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

Per una coda ordinata usare `plex library 'LIBRARY NAME'` invece di `plex shuffle`. Shuffle sceglie un ordine una volta; il chiosco lo ripete. Confermare gli elementi desiderati, `enabled: true`, `strict: true` e una politica associata alla coda Plex corretta. Una politica `waiting` senza canale o media non è un chiosco accettato. Osservare almeno due campioni con posizione/conteggio fotogrammi crescenti e controllare lo schermo. Un riscontro o l’etichetta `playing` senza avanzamento non basta. Provare una transizione naturale a fine elemento; se un ciclo intero è impraticabile, registrare esplicitamente il limite.

Il chiosco rigoroso controlla l’interfaccia del lettore. Non è registrazione come proprietario del dispositivo Android né prova che Home, Recents, notifiche e Settings siano bloccati. Mantenere il launcher di sistema e una via di manutenzione supervisionata.

## Abilitare separatamente l’avvio

Compilare e verificare [OTT Home 0.2.0](../native/android-home/README.md), mantenendo la chiave di firma esistente per gli aggiornamenti. Installare `play.ott.home` accanto a `play.ott.foss`. Aprire `play.ott.home/.StartupActivity` e abilitare **Start OTT after reboot**. L’opzione è disattivata per impostazione predefinita. Non cambia binario ufficiale, firma OTA, profili o assegnazione del tasto Home.

Se il firmware offre la scelta di Home, è possibile scegliere anche **OTT Home → Always**. Dove manca (incluso il vecchio firmware Fire testato), l’opzione di avvio funziona indipendentemente. Le installazioni già configurate come Home predefinita mantengono il comportamento. Disabilitare entrambi i meccanismi per il rollback completo.

`recover_on_boot` del gestore USB condiviso è un terzo meccanismo indipendente: richiede NAS e cavo USB, gestisce solo un nuovo avvio e non riavvia continuamente un’app bloccata. Non sostituisce l’avvio autonomo. Durante la sua prova lasciare la politica di recupero disattivata affinché non nasconda un mancato lancio dell’app complementare. Rispettare i blocchi sicuri; sbloccare normalmente quando richiesto.

## Accettazione e consegna

Conservare le prove privatamente, senza credenziali nelle ricevute pubbliche:

- Versioni e hash APK, corrispondenza delle identità fisica/OTT/USB e preset selezionato.
- Libreria Plex, numero/ordine degli elementi, politica rigorosa e riproduzione iniziale in avanzamento.
- ID di avvio prima e dopo un vero riavvio del sistema; successivamente `sys.boot_completed=1`.
- Nessun lancio manuale del lettore o recupero USB tra riavvio e osservazione.
- Attività OTT in primo piano, riconnessione al controller, coda/politica rigorosa ripristinata e riproduzione in avanzamento. Il risultato `requested` del ricevitore di avvio indica solo invio.
- Prova separata del pulsante di alimentazione: schermo spento, audio fermo, sblocco normale e ripresa.
- Limiti precisi: sblocco sicuro richiesto, recupero assistito USB, uscite fisiche non testate, errori di flusso o memoria esaurita. Non dichiarare tali casi completamente accettati.

Prima di consegnare il tablet successivo, ripetere la stessa lista invece di copiare l’esito del precedente. I vecchi Fire possono richiedere `hls.js` per M3U perché l’HLS nativo del produttore può bloccarsi; ciò non dimostra stabilità Plex.

## Manutenzione e rollback

```sh
ott TABLET kiosk off
```

Aprire **OTT Startup**, disabilitare **Start OTT after reboot** e ripristinare la scelta Home originale se modificata. Se necessario, disinstallare solo `play.ott.home` tramite il trasporto USB verificato; preservare `play.ott.foss` e i suoi dati. Disattivare l’avvio non cambia la coda né disabilita automaticamente il chiosco.
