[EN · English](backup-and-recovery.md) · [**IT · Italiano**](backup-and-recovery.it.md)

<a id="backup-and-recovery"></a>
# Backup e ripristino

Guida al ripristino di dDuo Solo Founder `0.2.0-beta.1`.

<a id="recovery-contract"></a>
## Garanzie di ripristino

PostgreSQL è la fonte autorevole. Qdrant contiene due proiezioni derivate,
separate per memoria semantica e ricerca dei task. Full Recovery Bundle v2
salva entrambe quando Qdrant è abilitato, ma ciascuna può essere ricostruita da
PostgreSQL. Uno snapshot vettoriale mancante o incompatibile rallenta il
ripristino senza compromettere lo stato autorevole del progetto.

Lo stesso backup PostgreSQL protegge tutte le viste di Work: Sprint, Backlog e
History. Include definizioni e stato degli sprint, appartenenza dei task,
snapshot immutabili di chiusura con i relativi esiti, revisioni dei task e
ricevute delle operazioni sugli sprint. Il ripristino conserva queste
informazioni: non avvia o chiude sprint e non riassegna automaticamente i task
incompleti. Un archivio precedente agli sprint riceve lo schema corrente con
le migrazioni, senza inventare appartenenze storiche.

dDuo non salva i volumi Docker o le immagini disco di Docker Desktop: esporta
i dati applicativi in un file portabile `.dduobackup`, ripristinabile in nuovi
volumi isolati su un altro host supportato.

Eventi di osservabilità e snapshot hook/MCP esatti sopravvivono al restore.
Conservano contesto non mascherato, anche segreti se inseriti in quel contenuto.
Diagnostica CLI grezza, chiavi di autenticazione runtime e percorsi estranei
non vengono raccolti come campi tecnici. Registrarli non pianifica da solo un backup e gli
snapshot storici mancanti non vengono inventati.

<a id="archive-format"></a>
## Formato dell'archivio

Il file esterno usa AES-256-GCM in streaming, con nonce casuale da 96 bit e una
chiave indipendente da 256 bit per progetto. Lo ZIP interno autenticato contiene:

```text
manifest.json
checksums.sha256
postgres.dump
project.toml
runtime-settings.json
secrets/dduo.env
secrets/codex/auth.json                  # se disponibile come credenziale su file
binding/project.toml                     # identità portabile, non approvazione locale
binding/remote-credential.token          # solo per un binding esplicitamente remoto
host-state/hooks/*.json                  # consegne pendenti del progetto
host-state/mcp-observability.json        # telemetria pendente, facoltativa
history/backup-records.json               # cronologia completata, se disponibile
qdrant/memory.snapshot                    # acceleratore derivato facoltativo
qdrant/tasks.snapshot                     # acceleratore derivato facoltativo
```

Impostazioni e segreti seguono elenchi espliciti di elementi ammessi. dDuo non
archivia file di ambiente arbitrari, directory home, chiavi SSH, portachiavi
del sistema operativo, sorgenti del repository, immagini Docker, token del
bridge, PID, log o spool di altri progetti. Sono esclusi anche il pacchetto
Agent Plugins installato e le registrazioni client Codex/Claude: ripristinare
un progetto non sostituisce il software client degli altri progetti. Voci di
installazione non supportate nei vecchi bundle vengono ignorate.
Manifesto e impostazioni autenticano le versioni del runtime e la
versione minima compatibile di dDuo.

La credenziale Codex viene copiata soltanto dal `CODEX_HOME` privato del
progetto, configurato con `cli_auth_credentials_store = "file"`. Un login
revocato o scaduto richiede comunque una nuova autenticazione dopo il ripristino.

La cache derivata del manuale è esclusa e si aggiorna da risposte autenticate
basate su PostgreSQL. Sono esclusi anche adapter status-line Claude e metadati
di ripristino della workstation: sul nuovo computer si installano da Setup,
senza sovrascrivere la status line esistente durante il restore del progetto.

Il manifesto registra formato, versioni applicative, identità del progetto,
punto del backup, dimensioni, digest SHA-256, identità delle due collezioni
Qdrant, copertura delle credenziali e avvisi. Segreti e metadati in chiaro non
sono esposti all'esterno del contenuto cifrato. La chiave di recupero non è
mai inclusa nell'archivio.

Gli archivi con schema v1 restano leggibili. I nuovi backup sono v2 e vengono
pubblicati solo quando l'agente host autenticato restituisce il supplemento
limitato e filtrato per progetto necessario al recupero integrale. L'assenza
dell'agente non produce un backup falsamente dichiarato completo.

Dopo la scrittura, il runtime decifra l'archivio, verifica l'intero inventario
ZIP, rifiuta percorsi non sicuri, link, membri duplicati, file host inattesi e
stato di altri progetti, controlla dimensioni e digest ed esegue
`pg_restore --list`. Solo dopo queste verifiche l'archivio entra nella
rotazione e la generazione inclusa viene considerata protetta.

<a id="configure"></a>
## Configurazione

Per un progetto locale, il Gestore dell'infrastruttura apre **Backup** nella
dashboard e sceglie **Abilita backup**. Setup configura la cartella predefinita
`~/Documents/dDuo Solo Founder Backups`, aggiorna i due servizi che richiedono
il nuovo mount, avvia il primo archivio quando dovuto e offre la chiave di
recupero con un download disponibile una sola volta.

Per un progetto remoto, la scheda Backup riservata al gestore offre una
richiesta priva di credenziali per una chat autorizzata. L'agente configura la
VPS autorevole, non Setup della workstation, e chiede solo l'accesso mancante.
Usa input privato per le credenziali: off record non lo sostituisce né cancella
l'audit. I membri non possono leggere metadati backup o creare, scaricare o
configurare archivi completi e non devono operare sulla VPS.

La cartella predefinita è locale. Prima di considerare il backup una protezione
contro la perdita del computer, scarica gli archivi verificati su disco
esterno, cartella cloud sincronizzata, NAS o altra destinazione protetta in
modo indipendente. Per scegliere una destinazione diversa è disponibile:

```bash
dduo-solo-founder backup configure /path/to/off-device-folder
```

Ogni progetto ha una sottocartella e una chiave proprie. La configurazione è in:

```text
~/.config/dduo-solo-founder/backups.json
~/.config/dduo-solo-founder/backup-keys/<project-id>.key
```

Il file della chiave è accessibile solo all'utente. La chiave viene mostrata una
volta alla prima configurazione: conservala in un password manager recuperabile
anche senza il computer protetto. L'archivio non contiene la propria chiave.
Cambiare solo la destinazione conserva la chiave esistente e la leggibilità dei
vecchi backup. Il ripristino rifiuta di sostituire una chiave locale diversa
senza la scelta esplicita di `--force`, dopo conferma della sostituzione della
memoria del progetto.

<a id="automatic-behavior"></a>
## Comportamento automatico

Dopo il salvataggio durabile del turno, `Stop` chiama un endpoint rapido di
pianificazione; l'API cerca inoltre progetti con backup dovuto ogni cinque
minuti. Il backup parte in background quando sono vere tutte le condizioni:

- destinazione e chiave sono disponibili;
- i dati sono cambiati dopo l'ultima generazione inclusa;
- sono passate almeno 24 ore dall'ultimo backup verificato;
- non esiste già un backup pianificato o in esecuzione.

Le scritture concorrenti al dump avanzano la generazione del progetto: l'archivio
completato resta valido, ma il progetto rimane modificato e le nuove scritture
saranno incluse nel backup successivo.

La creazione è serializzata per progetto. PostgreSQL fornisce una vista
autorevole coerente; l'agente host legge atomicamente i file spool del progetto;
Qdrant è una proiezione riconciliabile. Non si tratta di una transazione
distribuita tra database, Qdrant e filesystem. Le chiavi di idempotenza rendono
sicuro il replay di un elemento spool già presente in PostgreSQL.

La conservazione predefinita mantiene l'archivio più recente per ogni fascia:

- 7 fasce giornaliere;
- 4 settimane ISO;
- 6 mesi.

Più backup manuali dello stesso giorno occupano le stesse fasce: viene
conservato solo il più recente. Il nuovo archivio verificato resta sempre,
anche se l'orologio del computer è indietro rispetto a un nome file precedente.
La rotazione avviene solo dopo le verifiche crittografiche, strutturali, dei
checksum e di PostgreSQL.

<a id="manual-operations"></a>
## Operazioni manuali

```bash
dduo-solo-founder backup create
dduo-solo-founder backup status
dduo-solo-founder backup verify /path/to/archive.dduobackup
dduo-solo-founder backup drill /path/to/archive.dduobackup
```

`verify` controlla integrità crittografica, struttura, checksum e leggibilità
del dump. `drill` avvia anche un container PostgreSQL temporaneo, ripristina il
dump, verifica l'esistenza del progetto e conta progetti, task, memorie e turni,
quindi rimuove il container. Senza percorso usa il backup configurato più
recente. Non tocca i volumi del progetto attivo e segnala entrambe le proiezioni
semantiche come ricostruibili da PostgreSQL.

La dashboard offre al gestore stato, download diretto degli archivi verificati
conservati e creazione manuale. Un errore resta visibile nell'attività e nello
stato senza dichiarare protette le modifiche.

Prima di aggiornare o disinstallare, l'installer legge il registro privato ed
esegue l'equivalente di questi comandi per ciascun progetto registrato con una
destinazione configurata:

```bash
dduo-solo-founder backup create --trigger update --project-root /exact/project/root
dduo-solo-founder backup create --trigger uninstall --project-root /exact/project/root
```

Il percorso esplicito impedisce a registrazioni obsolete di spostare il backup
su un altro checkout. Un backup configurato che fallisce blocca l'installer.
`./install.sh --force` è l'eccezione esplicita e non elimina gli archivi
precedenti o i volumi del progetto.

<a id="move-to-another-computer"></a>
## Passare a un altro computer

1. Installa una release dDuo compatibile e Docker sul nuovo host: Docker Desktop
   per una workstation locale, Docker Engine con Compose per un VPS Linux. Sul
   VPS usa `./install.sh --headless` dal checkout della release ed esegui
   `dduo-solo-founder remote-preflight` prima del ripristino.
2. Clona o copia il repository del progetto.
3. Rendi disponibile localmente il file `.dduobackup`.
4. Recupera la chiave dal password manager protetto separatamente.
5. Esegui dal repository:

```bash
dduo-solo-founder backup restore /path/to/archive.dduobackup
```

Un archivio v2 ripristina con permessi riservati all'utente la chiave OpenAI,
i segreti dDuo e la credenziale Codex portabile su file. Con schema v1 serve
prima `dduo-solo-founder configure-openai`.

Il comando autentica e controlla l'archivio, valida il dump e lo prova in un
container PostgreSQL temporaneo prima di chiedere conferma distruttiva. Se la
destinazione possiede già memoria, richiede un nuovo backup di sicurezza
verificato: un errore non elimina silenziosamente l'unica copia disponibile.
Conserva l'UUID, crea una nuova autorità di binding per percorso ed endpoint,
riserva porte libere, ripristina in volumi nuovi e isolati, applica le
migrazioni e aggiorna il percorso del progetto. Le approvazioni locali non
vengono copiate: accesso remoto e fiducia negli hook richiedono l'approvazione
nativa del nuovo client.

Nel ripristino sullo stesso host, il bundle di sicurezza viene autenticato,
estratto, verificato semanticamente e provato prima della prima modifica. I
digest di archivio e manifesto restano vincolati all'operazione. Se dopo
l'inizio delle modifiche falliscono PostgreSQL, configurazione, avvio,
migrazione o salute API, dDuo ripristina automaticamente il bundle verificato,
configurazione del progetto, impostazioni, segreti, chiave e registro backup.
Un errore limitato agli indici Qdrant avviene dopo il commit della parte
autorevole e produce `RESTORE_DEGRADED`, senza annullare un ripristino sano di
PostgreSQL.

Se lo snapshot della memoria è compatibile, viene riconciliato con PostgreSQL:
i punti sconosciuti vengono eliminati, quelli mancanti o obsoleti accodati.
Altrimenti la collezione viene azzerata e tutte le memorie attive vengono
accodate per la reindicizzazione, segnalando il recupero alternativo.

Lo snapshot dei task viene ripristinato indipendentemente. Quando PostgreSQL e
l'applicazione sono pronti, la collezione dei task viene sempre riconciliata:
i punti correnti restano senza nuovi embedding; quelli mancanti, obsoleti, con
renderer diverso o più recenti del database vengono ricreati dalle righe
autorevoli. Un marcatore di epoca e il confronto esatto delle cardinalità
consentono alle ricerche successive di rilevare sostituzioni o perdite
parziali senza trasferire tutto l'inventario. Letture esatte, liste e modifiche
dei task restano disponibili durante la ricostruzione. Un errore nel recupero
di uno dei due indici produce `RESTORE_DEGRADED` e codice di uscita `8`.

Dopo il recupero verifica sprint attivi e pianificati, Backlog, History, esiti
completati e allegati. Riconciliare gli indici non cambia appartenenza agli
sprint o stato dei task. Ogni spostamento di lavoro richiede un'operazione
Work esplicita sullo stato ripristinato.

Il record del backup in esecuzione è escluso da `postgres.dump` per evitare
un record circolare perennemente attivo. La cronologia completata viaggia in
JSON portabile, viene reinserita in modo idempotente e riceve la provenienza
dell'archivio. I vecchi percorsi host non diventano destinazioni attive. Le
successive modifiche a percorso e indici restano da proteggere con un nuovo
backup.

<a id="move-an-authoritative-project-to-another-vps"></a>
## Spostare un progetto autorevole su un altro VPS

Non copiare un database attivo lasciando due nodi scrivibili. Usa lo stato
dell'autorità e Full Recovery Bundle v2 insieme. Installa il runtime sul VPS
di destinazione con `./install.sh --headless`, prepara i prerequisiti Linux ed
esegui `dduo-solo-founder remote-preflight` prima di congelare la sorgente.
Il ripristino fornisce l'identità esistente: non inizializzare un progetto
diverso sulla destinazione. [Creare la prima memoria direttamente su VPS](remote-teams.it.md)
non richiede un archivio di trasferimento.

1. Leggi l'identità della destinazione con `dduo-solo-founder remote-node-id`.
   Sulla sorgente esegui
   `dduo-solo-founder remote-transfer-prepare --target-node-id <NODE_ID>`.
   L'autorità passa da `active` a `transfer_pending`, rifiuta le normali
   scritture e crea il backup finale alla generazione congelata. Serve un
   bundle v2 verificato il cui manifesto cifrato inventari e identifichi il
   segreto dell'autorità del nodo: un archivio precedente o soltanto dichiarato
   completo senza tale attestazione non può ritirare la sorgente.
2. Ripristina l'archivio sulla destinazione usando la chiave separata, poi
   esegui `dduo-solo-founder remote-host --public-ip <PUBLIC-IP>`. Il clone
   resta in sola lettura e restituisce una `activation_receipt` firmata. Non
   avanza ancora la generazione e non diventa autorevole.
3. Verifica `destination_ready` e `https_verified: true`. `remote-host` valida
   il certificato pubblico e il percorso HTTPS completo fino al progetto esatto
   in sola lettura: la sola ricevuta firmata o il gateway avviato non bastano. Le
   credenziali remote esistenti possono aprire la dashboard in sola lettura;
   per un progetto prima locale, il primo gestore viene creato solo al
   completamento. Se interrompi qui il trasferimento, elimina il clone ed
   esegui sulla sorgente
   `dduo-solo-founder remote-transfer-cancel --new-node-not-activated`.
4. Per confermare il passaggio, sulla sorgente usa la ricevuta della destinazione:

   ```bash
   dduo-solo-founder remote-transfer-retire \
     --activation-receipt '<ACTIVATION_RECEIPT>' \
     --destination-api-url '<URL-API-HTTPS>' \
     --yes
   ```

   Usa l'URL API esatto stampato da `remote-host`. Prima della finalizzazione,
   la sorgente verifica autonomamente certificato, percorso HTTPS e identità
   del trasferimento, senza disabilitare TLS né seguire redirect. Se la verifica
   fallisce, ritiro e pulizia dei volumi non iniziano.
   La ricevuta lega crittograficamente progetto, generazione congelata,
   sorgente, destinazione e nonce. La sorgente diventa irreversibilmente
   `transferred`, salva una `finalization_receipt` prima della pulizia ed
   elimina soltanto i vecchi volumi Docker del progetto e la sua route Caddy.
   Una pulizia interrotta riprende dal marcatore privato anche senza database,
   ma verifica nuovamente HMAC e corrispondenza di progetto, generazione e nodi.
   Un marcatore modificato o solo sintatticamente plausibile non autorizza
   l'eliminazione dei volumi.
5. Sulla destinazione riesegui `remote-host --public-ip <PUBLIC-IP>
   --finalization-receipt '<FINALIZATION_RECEIPT>'`. Solo ora il clone avanza
   di generazione, diventa scrivibile, crea il gestore quando necessario e
   riconcilia gli indici. La stessa transazione segna come interrotti eventuali
   backup pianificati o in esecuzione ereditati, evitando che blocchino il
   primo backup reale della destinazione.
6. Per il primo passaggio da locale a VPS, collega il checkout originale con
   il token del gestore restituito al punto 5 e `remote-bind --replace-existing`,
   verificando che l'ID coincida con il binding locale ritirato. Le workstation
   già remote usano `remote-rebind`, che riutilizza il token privato del
   dispositivo senza stamparlo o copiarlo. I nuovi collaboratori usano il
   proprio invito monouso. Vedi le
   [istruzioni di collegamento](remote-teams.it.md#move-the-authoritative-memory).

Non annullare dopo che la sorgente ha restituito la ricevuta finale. Le
ricevute HMAC proteggono dallo split brain accidentale tra host dello stesso
gestore fidato. Il segreto dell'autorità è deliberatamente incluso nel bundle
cifrato: questo protocollo Beta non protegge da un host di destinazione
malevolo che possiede quel segreto. Tale scenario richiede firme asimmetriche
riservate alla sorgente o un coordinatore esterno. Conserva archivio finale e
chiave finché il nuovo nodo non supera tutte le verifiche di accesso e
ripristino. Vedi [Progetti remoti e team](remote-teams.it.md).

<a id="device-loss"></a>
## Perdita del dispositivo

Per recuperare dopo la perdita totale del dispositivo servono due elementi
indipendenti:

- almeno un archivio `.dduobackup` sincronizzato o conservato fuori dall'host;
- la sua chiave in un password manager recuperabile separatamente.

Perdere uno dei due rende impossibile il recupero. Esegui periodicamente
`backup drill` su un archivio sincronizzato: prova il recupero del database
senza sostituire il progetto attivo. Prova anche un ripristino completo su un
altro host prima di affidare al sistema l'unica memoria operativa.

Chi possiede archivio e chiave può decifrare le conversazioni e usare le
credenziali OpenAI/Codex copiate finché non vengono revocate. Tratta la coppia
come accesso agli account, affidala solo all'operatore di recupero autorizzato e
ruota le credenziali se l'archivio esce dal perimetro fidato. La cancellazione
sicura dei blocchi temporanei in chiaro su SSD non è garantita: il ripristino
usa directory temporanee private e ne limita la durata.

<a id="failure-behavior"></a>
## Comportamento in caso di errore

- Destinazione non disponibile: nessun archivio; le modifiche restano da proteggere.
- Chiave errata o contenuto cifrato alterato: autenticazione fallita prima dell'estrazione.
- Inventario ZIP, percorso, checksum o dimensione non validi: verifica fallita.
- Dump PostgreSQL non valido: `pg_restore --list` fallisce e la rotazione non parte.
- Agente host assente, supplemento eccessivo o malformato, file inattesi,
  spool di altri progetti o segreto dDuo mancante: il bundle v2 non viene pubblicato.
- Qdrant assente durante il backup: PostgreSQL viene salvato con un avviso.
- Snapshot memoria incompatibile: PostgreSQL viene ripristinato e la
  reindicizzazione della memoria viene accodata.
- Indice task: sempre riconciliato da PostgreSQL, conservando i punti correnti
  e ricostruendo le differenze indipendentemente dallo snapshot memoria.
- Reindicizzazione non disponibile: PostgreSQL resta ripristinato; la CLI
  segnala stato degradato e richiede la riparazione della ricerca semantica.
- Processo interrotto: il backup pianificato/in esecuzione viene segnato come
  fallito al successivo avvio API.
- Login del sonno scaduto o revocato: dati ed embedding vengono ripristinati;
  la consolidazione richiede una nuova autenticazione Codex o Claude nello
  stato host privato del progetto. Le credenziali Claude su file sono incluse
  quando disponibili; quelle nel Portachiavi macOS richiedono un nuovo login.
