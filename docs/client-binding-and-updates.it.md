[EN · English](client-binding-and-updates.md) · [**IT · Italiano**](client-binding-and-updates.it.md)

# Binding client e aggiornamenti espliciti

<a id="one-checkout-one-project-authority"></a>
## Un checkout, una sola autorità di progetto

Ogni repository ha un solo binding in `.dduo-solo-founder/project.toml`:
`binding = "local"` usa esclusivamente le porte loopback e lo stack Docker
isolato del progetto; `binding = "remote"` usa gli endpoint HTTPS dichiarati e
non avvia né interroga una memoria locale sostitutiva.

Il binding è per checkout, non globale al client o al computer. Progetti diversi
mantengono ID, volumi PostgreSQL, collection Qdrant, segreti e backup distinti,
anche se condividono il plugin. Il registro locale lega l'identità alla radice
canonica e a un'impronta del proprietario. I comandi verificano questa titolarità
prima di avviare, fermare, configurare, leggere o salvare il progetto. Copiare il
descrittore non clona l'autorità: serve uno spostamento, restore o rebind
esplicito. Due radici non possono rivendicare silenziosamente lo stesso progetto.

Il descrittore remoto non contiene segreti. L'approvazione riguarda esattamente
radice, ID e endpoint API/dashboard. Il bearer dispositivo è conservato fuori
da Git con permessi `0600`; il server ne conserva solo l'hash. Spostare o
ripristinare il repository richiede l'approvazione del nuovo binding.

Il bearer permanente viaggia solo nell'header `Authorization`. Il comando
dashboard lo scambia con un ticket monouso di cinque minuti e poi una sessione
browser sicura del progetto. Revocare un membro revoca dispositivi e sessioni.

<a id="agent-plugins-package-and-native-adapters"></a>
## Pacchetto Agent Plugins e adapter nativi

La distribuzione contiene tre livelli:

1. Pacchetto portabile Agent Plugins 1.0: `plugin.json`, `skills/`, `mcp.json`.
2. Applicazione/runtime dDuo: MCP, API, database, retrieval, Work, osservabilità,
   backup e Setup.
3. Adapter sottili Codex e Claude sotto `it.dduo.client-support/`.

Il launcher stdio individua il runtime attraverso `PLUGIN_ROOT` e avvia il
server MCP condiviso. L'inizializzazione verifica l'identità reale del client;
client non supportati o identità incongruenti vengono rifiutati. Non è DRM:
Agent Plugins standardizza Skill e MCP, ma non il lifecycle automatico dei turni
che dDuo richiede. Un altro client necessita del proprio adapter verificato.

L'installer materializza piccoli pacchetti nativi separati. Codex riceve
manifest, Skill, hook e licenza; Claude riceve manifest, Skill, launcher MCP
locale al pacchetto e licenza. I manifest portabili e i file dell'altro client
sono esclusi dalle proiezioni per non oscurare il discovery nativo. Il percorso
assoluto del dispatcher viene verificato nell'adapter Codex, ma il progetto non
è fissato lì: ogni chiamata MCP passa e valida il proprio `workspace_root`.

`PLUGIN_ROOT` individua risorse della revisione installata. `PLUGIN_DATA` può
contenere cache eliminabili del client, non stato autorevole. Memoria e Work
devono essere condivisi tra Codex e Claude e possono risiedere su VPS; lo stato
rimane quindi nello stack del progetto e nella configurazione host privata.

<a id="compatibility-response"></a>
## Risposta di compatibilità

Hook, MCP e launcher dichiarano release e protocollo. Il server accetta versioni
release diverse quando il protocollo è compatibile. In caso contrario risponde
HTTP `426`, indicando di installare una versione ufficiale compatibile e
ricaricare il client: riavvio completo Codex e nuova chat, oppure nuova sessione
Claude. La risposta non contiene URL eseguibili, chiavi di fiducia, checksum o
permessi e non può avviare un aggiornamento.

Il bundle dashboard negozia separatamente il traffico browser. Una release
plugin nuova o sconosciuta ma con protocollo valido non viene costretta a
tornare a una versione precedente.

<a id="explicit-installation-and-update"></a>
## Installazione e aggiornamento espliciti

L'utente o l'agente autorizzato sceglie una revisione del repository ed esegue
esplicitamente l'installer. Non serve una chiave aggiuntiva per gli aggiornamenti;
l'API del progetto non può scaricare o attivare software sul client.

L'installer verifica sorgenti e checksum, acquisisce un lock macchina con
controllo del proprietario, protegge i progetti tramite backup/snapshot,
costruisce il runtime con dipendenze bloccate e installa l'adapter scelto.
`--headless` o `--only core` installano solo il runtime e non aprono Setup.
`--no-setup` mantiene l'adapter esplicito ma evita Setup, per esempio prima di
`remote-bind` o `remote-join`. Nessuno di questi flag attiva una memoria locale.

Dopo verifica, tutti i puntatori vengono confermati insieme; solo allora sono
rimossi gli artefatti obsoleti dell'installazione.
Un fallimento ripristina runtime, stato client, registro e configurazione.
Database, Qdrant e backup cifrati non sono artefatti dell'installazione. Windows usa
eseguibili nativi, wrapper `.cmd` e hook Node con lo stesso runtime verificato:
vedere [Piattaforme](platform-support.it.md).

<a id="alpha-secret-migration"></a>
## Migrazione da installazioni precedenti

L'uso ordinario legge un file privato per progetto, mai un ambiente globale.
Migrando un'installazione precedente, l'installer valida le destinazioni,
congela atomicamente il file precedente e copia solo segreti ammessi nei file
privati dei progetti. I valori già presenti prevalgono. Un errore ripristina
i file originali; scritture concorrenti vengono conservate separatamente per
il recupero esplicito. Il file congelato viene ritirato solo dopo il commit.

<a id="client-reload-boundary"></a>
## Ricaricamento del client

Codex Desktop conserva il pacchetto al di fuori della singola chat. Dopo
installazione o aggiornamento occorre chiudere completamente e riaprire l'app,
completare le azioni Setup necessarie e aprire una nuova chat nella cartella.
Claude richiede una nuova sessione. Prima attivazione o rebind senza
aggiornamento richiedono solo una nuova chat/sessione; uso normale e problemi
temporanei della memoria non richiedono riavvii.

Full Recovery Bundle v2 è limitato al progetto: include requisiti runtime,
code e segreti propri, non pacchetti plugin o registrazioni del computer.
Ripristinare un progetto non cambia la revisione plugin usata dagli altri.
