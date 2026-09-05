[EN · English](complete-guide.md) · [**IT · Italiano**](guida-completa-dduo-solo-founder.md)

# dDuo Solo Founder: guida completa

Panoramica per `0.2.0-beta.1`. Le guide collegate contengono comandi e procedure
di recupero; il [README](../README.it.md) contiene i prompt da copiare.

## 1. Il risultato per il founder

dDuo offre a Codex e Claude Code contesto persistente, Plan, Epic, Task e
Sprint, un manuale operativo e una dashboard consultabile. Ogni progetto ha
database, indici, credenziali, Work e backup separati. Un progetto può essere
locale mentre un altro usa una VPS condivisa.

Il percorso deriva dalla richiesta: memoria locale nuova, prima memoria su VPS,
invito a memoria remota esistente o trasferimento di memoria già attiva.
Non inizializzare una memoria sostitutiva. Vedere [Installazione](installation.it.md).

## 2. Cosa viene chiesto davvero

Su macOS e Windows servono Node.js 18+, Git e il client scelto; per la memoria
locale serve anche Docker Desktop. Il gestore fornisce chiave OpenAI embeddings
e login Codex in abbonamento per consolidare, anche quando si usa Claude.
L'installer prepara `uv`, non Docker o le CLI dei client.

Setup mostra solo le azioni mancanti e rispetta l'approvazione nativa degli
hook. Dopo installazione/aggiornamento Codex, chiudere completamente e riaprire
l'app, poi avviare una nuova chat; con Claude basta una nuova sessione.
Attivare un progetto senza aggiornare il client richiede solo una nuova
sessione. Un profilo vuoto richiede obiettivi e lavoro attuale, senza dati demo.
Il rifiuto dell'attivazione viene rispettato.

Le credenziali restano fuori da Git. I collaboratori ricevono token dispositivo
revocabili, non credenziali server. I backup di recupero includono volutamente
i segreti del progetto sotto cifratura; la recovery key va tenuta separata.
Vedere [Sicurezza](../SECURITY.it.md) e [Privacy](privacy.it.md).

## 3. Ambiti e isolamento

Agent Plugins 1.0 fornisce `plugin.json`, `skills/` e `mcp.json`. Gli adapter
nativi Codex/Claude sono in `it.dduo.client-support/`; gli altri client non sono
supportati. `PLUGIN_ROOT` individua il pacchetto; `PLUGIN_DATA` è storage del
singolo client, mai memoria autorevole del progetto.

<a id="4-architettura-locale"></a>
Ogni progetto ha API, worker, dashboard, PostgreSQL e Qdrant propri. PostgreSQL
è autorevole; gli indici di task e memorie sono derivati e ricostruibili.
Un agente host gestisce setup, credenziali ed esecuzione in abbonamento
limitate al progetto; il bridge autenticato non viene esposto pubblicamente.
Vedere [Architettura](architecture.it.md).

<a id="4-bis-architettura-remota-e-team"></a>
## 4. VPS diretta e progetti condivisi

Per un progetto poco carico servono Linux con systemd, 1 vCPU, 1 GiB RAM,
2 GiB di swap persistente su disco e 5 GiB liberi nello storage Docker dopo
lo swap. Root e Docker condividono il filesystem; senza swap servono 7 GiB
prima del setup. Ogni progetto aggiuntivo richiede un altro stack completo.

Il [runbook VPS](platform-support.it.md#first-installation-directly-on-a-vps)
copre installazione headless, preflight, inizializzazione, credenziali private,
HTTPS e collegamento del computer. Solo Caddy è condiviso tra stack. TCP 443
resta aperta per i certificati insieme alle eventuali porte HTTPS dei progetti.

Il gestore controlla inviti, revoche, manuale, backup e trasferimenti. I membri
usano memoria e Work senza accesso VPS. Gli inviti sono monouso, specifici del
progetto e fissati alla release; l'accesso Git è separato.
Vedere [Progetti remoti e team](remote-teams.it.md).

## 5. Il ciclo di ogni conversazione

<a id="sessionstart"></a>
SessionStart risolve il binding approvato, riproduce turni completati in coda
e consegna il Founder Brief.

<a id="userpromptsubmit"></a>
UserPromptSubmit recupera memoria pertinente. Il brief finale rispetta 9.000
unità compatibili con il client e distingue elementi completi, estratti con
puntatori e omissioni. Avvio, clear e compattazione nativa ricevono snapshot
completi; turni ordinari e resume fidati ricevono modifiche, non istruzioni
operative ripetute. Vedere [Motore memoria](memory-engine.it.md).

<a id="stop"></a>
Stop salva il turno completato o lo accoda privatamente. Un guasto remoto non
crea memoria locale sostitutiva: una cache valida può fornire solo l'ultimo
manuale autenticato, indicato come obsoleto. Revoche, errori di autorizzazione
e incompatibilità non possono usare questo fallback.

## 6. Il contratto ricevuto dal modello

L'assistente usa il contesto senza appesantire la chat, risponde sinteticamente
su evidenze, propone Work proporzionato e mostra link leggibili. Esplorare non
obbliga a creare task; iniziative estranee richiedono una scelta sul perimetro.

<a id="6-bis-manuale-operativo-del-progetto"></a>
Il manuale contiene procedure stabili, come branch, review e deploy; lo stato
corrente appartiene a Work. Solo proprietario locale o gestore remoto
pubblicano revisioni dopo richiesta diretta o proposta accettata; i membri
possono proporre. La compattazione produce una bozza da verificare, mai una
sostituzione automatica. Obiettivo: 4.000 caratteri; limite editor: 100.000.
Vedere [Manuale operativo](remote-teams.it.md#project-operating-manual).

<a id="7-work-piano-epic-task-label-e-allegati"></a>
## 7. Work: Sprint, Backlog e Storico

Lo Sprint corrente contiene i task assegnati; il Backlog quelli non conclusi
senza Sprint. Lo Storico contiene Sprint archiviati, task non assegnati
conclusi/annullati e Plan completati/superati. Gli Epic restano globali al progetto.

Un solo Sprint può essere attivo. La chiusura richiede anteprima corrente e
destinazione degli incompleti; esiti immutabili, versioni e idempotenza
proteggono storico e modifiche concorrenti. Riaprire non annulla spostamenti
precedenti. I task esistenti non vengono assegnati a Sprint storici inventati.
Gli elementi noti si aprono direttamente; liste e ricerca hanno paginazione.
Vedere [Work](work.it.md).

## 8. Memoria e sonno

Il recupero seleziona memorie pertinenti alla richiesta e verifica le revisioni
attive in PostgreSQL. Una query storica aiuta richieste ellittiche o ricerche
riuscite senza risultati; non esiste una quota fissa di ricordi da consegnare.

Un esecutore Codex del progetto usa `gpt-5.6-terra` con reasoning medio.
Turni Codex e Claude condividono la coda mantenendo il client d'origine.
I batch contengono massimo otto turni e 40.000 caratteri. Otto turni pendenti,
venti minuti d'inattività, un cambio argomento o una richiesta manuale possono
attivare il consolidamento. Non c'è fallback API generativa né cambio
automatico di abbonamento.

<a id="9-stati-di-memoria-leggibili"></a>
I limiti provider usano retry; gli errori di autenticazione richiedono login.
Vedere [Risoluzione dei problemi](troubleshooting.it.md).

<a id="10-osservabilità-del-progetto"></a>
## 9. Osservabilità e costi

Contesto, embeddings, uso interattivo, consolidamento e affidabilità restano
categorie separate. Il contesto dDuo esatto è ispezionabile con fonti e
dimensioni: non è l'intero prompt né prova che il client lo abbia consumato tutto.

Input, letture/scritture cache, output e reasoning restano specifici per
provider/modello. Un dato assente è non disponibile, uno zero misurato resta
zero. API equivalente è una stima versionata, non una fattura. La telemetria
numerica non archivia trascrizioni. I filtri team attribuiscono operazioni,
non creano memorie separate. Vedere [Prezzi](pricing.it.md).

<a id="11-esempio-di-turno"></a>
Una richiesta di verifica riusa il task pertinente, riceve contesto e fonti e
conserva l'esito; durante un guasto il turno viene accodato per il replay.

<a id="12-aggiornamenti-backup-e-rollback"></a>
## 10. Aggiornamenti, backup e trasferimenti

Gli aggiornamenti installano una revisione scelta dall'utente con checksum e
dipendenze bloccate. Il rollback di runtime e registrazioni non annulla le
migrazioni database. Backup cifrati verificati e snapshot locali proteggono
l'upgrade; le snapshot non sostituiscono il recovery fuori dispositivo.
Vedere [Aggiornamento](update.it.md).

Full Recovery Bundle v2 include PostgreSQL, snapshot Qdrant facoltative,
credenziali progetto, stato pendente e storico backup. Esclude checkout,
installazione plugin, SSH, keychain, file home arbitrari, log, immagini Docker
e recovery key. Il restore verifica l'archivio prima di sostituire dati.
Vedere [Backup e ripristino](backup-and-recovery.it.md).

Il trasferimento congela la sorgente, ripristina una destinazione in sola
lettura, verifica la disponibilità, ritira la sorgente e solo dopo attiva la
destinazione. Non annullare dopo la finalizzazione della sorgente. Le ricevute
prevengono split-brain accidentali fra host fidati, non un host malevolo con
il segreto condiviso. Seguire l'esatta [sequenza](remote-teams.it.md#move-the-authoritative-memory).

<a id="13-cosa-dimostra-la-qualita-del-runtime"></a>
## 11. Verifiche e limiti

La CI copre backend, frontend, browser, Compose, distribuzione e installazione,
aggiornamento, rollback, MCP e hook nativi. L'autenticazione sintetica non prova
consenso reale, TLS esterno o quote provider. Verificare la readiness effettiva
e dichiarare i passaggi non testati. Vedere [Piattaforme](platform-support.it.md)
e [Contribuire](../CONTRIBUTING.it.md).

<a id="14-limiti-onesti-della-v1"></a>
Guasti o limiti provider possono ritardare la memoria; lo spool ha limiti.
Disinstallare il plugin ed eliminare dati sono operazioni separate:
vedere [Disinstallazione](uninstall.it.md).

## Riferimenti

[Installazione](installation.it.md), [VPS e team](remote-teams.it.md),
[Work](work.it.md), [Architettura](architecture.it.md),
[Motore memoria](memory-engine.it.md), [Backup](backup-and-recovery.it.md),
[Privacy](privacy.it.md), [Sicurezza](../SECURITY.it.md).
