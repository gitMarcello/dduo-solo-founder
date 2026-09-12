[EN · English](update.md) · [**IT · Italiano**](update.it.md)

# Aggiornamento e rollback

L'utente sceglie una revisione verificata e ne chiede l'installazione. Server,
dashboard e hook non possono scaricare o attivare release. Dopo una conferma,
l'installer protegge i progetti, prepara il runtime affiancato e aggiorna
l'adapter in transazione. Apre Setup salvo `--no-setup` o `--headless`.
Codex richiede riavvio completo e nuova chat; Claude solo una nuova sessione.

<a id="agent-plugins-update-boundary"></a>
## Confine di aggiornamento Agent Plugins

La revisione contiene `plugin.json`, `skills/`, `mcp.json`, launcher condiviso e
adapter nativi sotto `it.dduo.client-support/`. L'installer li materializza in
pacchetti client separati. `PLUGIN_ROOT` cambia con la revisione installata;
`PLUGIN_DATA` non viene migrato come memoria e non è autorità per database,
Qdrant, Work, credenziali o backup. Codex e Claude Code restano gli unici client
Beta supportati; aggiornare il pacchetto standard non abilita altri client.

<a id="data-safety"></a>
## Protezione dei dati

La migrazione è additiva: conserva identità, profilo e revisioni, Plan, Task,
artefatti, turni, segmenti, memorie, job, osservabilità, team, outbox e backup.
La versione 0.2 aggiunge Sprint, appartenenza e storico di chiusura senza
assegnare task esistenti a Sprint inventati. I task non conclusi senza Sprint
restano nel Backlog; quelli `done`/`cancelled` senza Sprint nello Storico
precedente. Gli Epic restano globali al progetto e i Plan completati sono
consultabili nello storico. Il testo delle memorie non viene riscritto.

Gli indici semantici sono derivati da PostgreSQL. Avvio e restore riconciliano
punti mancanti, obsoleti o orfani; letture esatte e fallback PostgreSQL limitato
restano disponibili durante il recupero.

Prima di sostituire il runtime, l'installer esegue i backup cifrati verificati
configurati con trigger `update`. Ferma brevemente i progetti locali registrati
e salva snapshot private con checksum di PostgreSQL/Qdrant sotto
`~/.config/dduo-solo-founder/upgrade-snapshots/`. Sono una protezione locale per
rollback, non un sostituto del backup cifrato fuori dispositivo.

Gli archivi sono creati dall'utente di sistema che installa, con permessi `0600`.
Docker legge il volume fermo in sola lettura e invia l'archivio a quel file;
non crea file di proprietà root nella directory degli snapshot sul computer.
Verifica tar e checksum restano obbligatorie. Se uno snapshot fallisce, il
recupero tenta di riavviare tutti i progetti coinvolti anche se fallisce la
pulizia temporanea. Uno stop non riuscito blocca lo snapshot, anche con `--force`.

I segreti vivono in `~/.config/dduo-solo-founder/project-secrets/<project-id>/`.
Le migrazioni di compatibilità preservano i valori specifici e tornano allo
stato precedente in caso di errore. Registrazioni software e cache obsolete
vengono rimosse solo dopo l'installazione riuscita, mai configurazione,
database, backup o volumi del progetto.

<a id="update-procedure"></a>
## Procedura di aggiornamento

1. Conservare il lavoro corrente nel repository tramite commit o altro mezzo.
2. Scegliere la revisione dDuo esatta e fidata.
3. Chiedere l'aggiornamento all'agente attivo e approvare l'azione complessiva.
4. Completare soltanto le azioni ancora mostrate da Setup.
5. Fare verificare all'agente `check_setup` prima di dichiarare il successo.
6. Chiudere completamente e riaprire Codex, poi aprire una nuova chat nella
   stessa cartella; con Claude aprire una nuova sessione.
7. Verificare memoria e Work prima di eliminare snapshot di rollback.

La risposta di incompatibilità API non sceglie una revisione né esegue questi
passaggi. Per aggiornare il runtime VPS usare dalla release scelta
`node bin/install.mjs --headless --yes` con `--project-root` del server. Per il
computer remoto usare l'adapter scelto con `--no-setup`, conservare il binding e
verificare l'accesso remoto autenticato. Il computer remoto non necessita di
Docker locale. Un cambio endpoint server richiede `remote-rebind`, non `init`.

<a id="roll-back-safely"></a>
## Eseguire il rollback

1. Fare diagnosticare l'errore e confermare esplicitamente ogni sostituzione
   dei dati del progetto.
2. Reinstallare la revisione precedente scelta mediante l'installer esplicito.
3. Se non basta, ripristinare l'ultimo archivio `.dduobackup` verificato con la
   recovery key separata.
4. Per un rollback immediato dei volumi locali, usare la snapshot verificata:

   ```bash
   node bin/install.mjs --restore-upgrade-snapshot <DIRECTORY-SNAPSHOT> \
     --project-root <DIRECTORY-PROGETTO> --yes
   ```

5. Aprire una nuova chat nella cartella ripristinata, applicando il riavvio
   Codex quando il pacchetto è stato reinstallato.

Il comando verifica manifest e checksum prima di sostituire soltanto i volumi
del progetto indicato. Se l'host è incerto o cambia computer/VPS, usare Full
Recovery Bundle. Il bundle v2 include segreti ammessi, credenziale Codex
file-backed quando disponibile, token remoto quando esplicitamente pertinente
e stato pendente hook/MCP del progetto. Esclude pacchetto plugin, registrazioni,
`~/.ssh`, file home arbitrari, keychain e recovery key. Un restore non cambia
l'installazione plugin degli altri progetti.

<a id="why-codex-restart-and-a-fresh-session-are-mandatory"></a>
## Perché servono riavvio Codex e nuova sessione

Codex Desktop mantiene la cache del pacchetto oltre la singola chat: il riavvio
completo e la nuova chat caricano la revisione scelta. Claude la carica nella
nuova sessione. Così una conversazione non mescola due release e registrazione
dei turni e osservabilità restano coerenti. Vedere
[Binding e aggiornamenti](client-binding-and-updates.it.md).
