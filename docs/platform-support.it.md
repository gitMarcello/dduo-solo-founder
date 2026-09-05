[EN · English](platform-support.md) · [**IT · Italiano**](platform-support.it.md)

# Piattaforme e prima installazione su VPS

La release `0.2.0-beta.1` supporta Codex e Claude Code su macOS e Windows
nativi, con memoria locale o su VPS Linux. Sul computer servono Node.js 18+,
Git e il client; Docker Desktop serve solo per la memoria locale. Sul server
servono Docker/Compose, Git, Node.js 18+ e Codex CLI 0.150.0+ per consolidare.

<a id="windows"></a>
## Windows

Da PowerShell si può invocare direttamente lo stesso installer:

```powershell
node "<CARTELLA-RELEASE-DDUO>/bin/install.mjs" --only codex --project-root "<CARTELLA-PROGETTO>" --yes
```

Usare `--only claude` per Claude Code. Per un computer da collegare alla VPS
aggiungere `--no-setup`, poi collegare il progetto o accettare l'invito.
`install.ps1` delega a questo installer. Il runtime usa la cartella nativa
`Scripts`, gli eseguibili `.exe` e i wrapper `.cmd`; gli hook Codex usano Node
e non richiedono `/bin/sh`. WSL è un ambiente Linux separato.

La CI copre installazione bloccata, aggiornamento/rollback, MCP e hook con
autenticazione e consolidamento sintetici: non prova abbonamenti reali né
consensi nativi. Setup verifica versione, login, autorizzazione hook, Docker
o endpoint remoto effettivi. I percorsi non verificati vanno dichiarati.

<a id="first-installation-directly-on-a-vps"></a>
## Prima installazione direttamente su VPS

Questo percorso crea la prima memoria del progetto sulla VPS. Controllare prima
i checkout autorizzati sul computer e sul server per individuare binding e
memoria esistenti. Una memoria esistente va preservata: usare il suo invito o
il [trasferimento dell'autorità](remote-teams.it.md#move-the-authoritative-memory).
Un repository da solo non implica che esista già una memoria; copiare un
descrittore configurato non equivale a inizializzare un progetto nuovo.

L'agente esegue i passaggi con l'autorizzazione del gestore all'installazione e
all'accesso al server. I membri ordinari non amministrano la VPS. Il
[prompt nel README](../README.it.md) è la richiesta breve da incollare in chat.

## 1. Preparare il server

Usare un account Linux persistente con servizi utente systemd, accesso a
Docker/Compose, Git, Node.js 18+ e Codex CLI 0.150.0+. L'installer dDuo installa
`uv`; non installa Docker, Node.js, Git o Codex CLI.

Qualsiasi provider VPS è accettabile. Per un progetto poco carico servono
1 vCPU, 1 GiB di RAM fisica, 2 GiB di swap persistente su disco e 5 GiB liberi
nello storage Docker dopo lo swap. Root e Docker devono condividere il
filesystem. Senza swap servono 7 GiB liberi prima di crearne 2 GiB. L'agente
configura lo swap mancante e ne verifica attivazione e persistenza. Ogni
progetto aggiuntivo richiede risorse per un altro stack completo.

Se manca, abilitare linger per l'account di servizio tramite l'operazione
amministrativa `loginctl enable-linger <UTENTE-VPS>`, poi verificare
`systemctl --user status` da quell'account. Conservare l'accesso SSH durante la
configurazione del firewall. Esporre soltanto SSH e le porte HTTPS necessarie,
non PostgreSQL, Qdrant, API interna o bridge host.

## 2. Installare soltanto il runtime server

Eseguire dal checkout autorizzato del progetto sulla VPS:

```bash
dduo_install_dir="$(mktemp -d)"
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git "$dduo_install_dir"
node "$dduo_install_dir/bin/install.mjs" --headless --project-root "$PWD" --yes
```

`--headless` o `--only core` installa il runtime senza adapter né browser.
Se serve, aggiungere al PATH la directory eseguibili indicata. Tenere il
checkout temporaneo della release separato dal progetto.

Eseguire il controllo dei prerequisiti:

```bash
dduo-solo-founder remote-preflight
```

Verifica Linux/systemd, linger, Docker, RAM, CPU, swap attivo su disco e spazio
Docker. Non crea un progetto e non configura lo swap. Risolvere ogni errore
prima di inizializzare; il successo non certifica ancora progetto o HTTPS.

## 3. Creare l'identità e configurare privatamente le credenziali

Nel checkout del progetto sulla VPS:

```bash
dduo-solo-founder init --headless --yes --project-root .
```

Se manca la chiave embeddings, il comando crea identità e archivio privato dei
segreti, restituisce `setup_required` con l'ID progetto e termina con codice
`5`. È uno stato intermedio previsto: non apre un browser e non ha completato
l'inizializzazione del database. Non nascondere errori diversi né dichiarare
pronto il progetto.

Proseguire con input privato e login ufficiale del dispositivo:

```bash
dduo-solo-founder configure-openai --project-root .
dduo-solo-founder login-codex --device-auth --project-root .
dduo-solo-founder init --headless --yes --project-root .
```

Il primo comando chiede la chiave senza mostrarla e la salva fuori da Git.
Il secondo autentica Codex nella directory privata del progetto; il gestore
completa l'autorizzazione ufficiale indicata da quel processo. Chiave, codici
di login e output di autenticazione restano fuori dalla chat e dai log
condivisi. Un progetto nuovo non ha una sessione dDuo da rendere off-record:
usare input privato senza inventare un ID. Per sessioni già inizializzate che
trattano credenziali infrastrutturali vale la procedura off-record verificata
in [Progetti remoti e team](remote-teams.it.md#host-a-project-on-a-vps).

L'ultimo `init` verifica le credenziali, costruisce lo stack VPS isolato e crea
la riga Project. “Servizi locali” significa locali alla VPS, non al computer.
`start` riprende uno stack ma non sostituisce l'inizializzazione.

## 4. Attivare e verificare HTTPS

Acquisire privatamente l'output seguente: il primo avvio stampa il token iniziale
del gestore.

```bash
dduo-solo-founder remote-host --project-root . --public-ip <IP-PUBBLICO> --owner-name "<NOME-VISIBILE>"
```

È facoltativo `--acme-email <EMAIL>`. Il comando ripete i controlli dei
prerequisiti e del login Codex, installa e verifica l'agente utente persistente,
protegge database e servizi, assegna l'autorità al nodo e crea il primo gestore.
Seguire le istruzioni firewall stampate: TCP 443 resta aperta per emissione e
rinnovo dei certificati, insieme alla porta HTTPS assegnata al progetto se
diversa. I progetti successivi usano normalmente una porta stabile fra 24443
e 25442.

Verificare HTTPS pubblico e accesso autenticato alla dashboard. Il risultato di
`remote-host` da solo non verifica firewall esterni, emissione del certificato
e instradamento. Controllare la persistenza dopo un riavvio controllato quando
autorizzato. Configurare, creare e verificare un
[Full Recovery Bundle](backup-and-recovery.it.md), con chiave recuperabile
separatamente. Un progetto nuovo sulla VPS non richiede ricevute di trasferimento
o dismissione di una vecchia autorità.

## 5. Collegare il computer

Installare la stessa release da un checkout temporaneo separato su macOS o
Windows, con `--only codex --no-setup` oppure `--only claude --no-setup` e la
radice effettiva del progetto sul computer. Poi eseguire:

```bash
dduo-solo-founder remote-bind --project-root . --project-id <ID-PROGETTO> --name "<NOME-PROGETTO>" --api-url <URL-API-HTTPS> --dashboard-url <URL-DASHBOARD-HTTPS>
dduo-solo-founder dashboard --tab tasks --project-root .
```

Fornire il token iniziale nel prompt privato, mai come argomento, messaggio di
chat o file Git. Un primo binding non richiede `--replace-existing` né
`remote-rebind`. I binding esistenti vanno preservati usando il percorso di
invito o trasferimento corretto.

Verificare identità, stato autenticato della memoria, Work, dashboard e backup.
Il computer remoto non richiede Docker locale né il login Codex del server.
Dopo installazione o aggiornamento Codex, chiudere completamente e riaprire
l'app, quindi aprire una nuova chat nel progetto; con Claude basta una nuova
sessione. Verificare gli eventuali consensi nativi ancora richiesti. Gli inviti
si creano con il [flusso Team](remote-teams.it.md#invite-a-project-member).
