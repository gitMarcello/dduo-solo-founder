[EN · English](remote-teams.md) · [**IT · Italiano**](remote-teams.it.md)

# Progetti remoti e team

Ogni progetto mantiene un volume PostgreSQL, uno Qdrant, API, worker e dashboard
propri, sia in locale sia su VPS. Più progetti sullo stesso server non fondono
stack, database, credenziali o memoria. Sono comuni il gateway Caddy e l'agente
host, che autentica separatamente ogni progetto. Caddy termina HTTPS e inoltra
ogni porta pubblica alla dashboard loopback corretta.

Il primo progetto usa normalmente HTTPS `443`; i successivi ricevono una porta
stabile libera fra `24443` e `25442`. `remote-host` stampa le porte da aprire.
TCP `443` resta aperta per la challenge TLS-ALPN ACME e il rinnovo dei
certificati, anche se il progetto usa un'altra porta o quello inizialmente
assegnato a `443` viene rimosso. Serve un IPv4 o IPv6 pubblico; i binding HTTP
non cifrati vengono rifiutati.

Gestore e collaboratore usano lo stesso pacchetto Agent Plugins 1.0. I client
supportati sono Codex e Claude Code. L'invito collega quel client a un solo
progetto remoto; non installa altri client o una memoria locale sostitutiva.
Pacchetto e `PLUGIN_DATA` restano sul computer, mentre la memoria è sulla VPS.

<a id="one-authoritative-binding-per-checkout"></a>
## Un solo binding autorevole per checkout

Ogni checkout ha un solo binding in `.dduo-solo-founder/project.toml`:

- `local` usa soltanto le porte loopback riservate e può avviare il proprio
  stack Docker isolato;
- `remote` usa soltanto API e dashboard HTTPS dichiarate, senza avviare o
  interrogare un Docker locale sostitutivo.

La scelta è per progetto: uno può essere remoto e un altro locale sullo stesso
computer. Il descrittore non contiene segreti. Il token
progetto/dispositivo vive fuori dal repository in un file privato `0600`;
l'approvazione riguarda radice canonica, ID progetto e impronta degli endpoint.

Se il servizio non risponde, il lavoro continua. I turni completati restano
nello spool privato del progetto per un replay idempotente quando torna
l'autorità. Non viene creata una seconda memoria.

Il client conserva l'ultimo manuale ottenuto tramite risposta autenticata sotto
`~/.config/dduo-solo-founder/manual-cache/`. Il file `0600` è autenticato HMAC
con il bearer e legato a progetto e binding: copie alterate o di altri binding
vengono rifiutate. In caso di rete assente o errore 5xx gli hook possono
consegnarlo avvisando che memoria e Work correnti mancano o sono obsoleti;
`get_project_manual` lo indica come `last_verified_cache`. Revoca, errori di
autorizzazione e aggiornamento obbligatorio non vengono aggirati. Non è una
replica della memoria o del Work e non può diventare autorità.

<a id="host-a-project-on-a-vps"></a>
## Ospitare un progetto su VPS

La prima memoria può nascere sulla VPS senza una precedente memoria sul computer
né archivio di trasferimento. Il [runbook VPS](platform-support.it.md#first-installation-directly-on-a-vps)
prevede installer `--headless`, `remote-preflight`, `init --headless`,
configurazione privata delle credenziali, completamento di `init` e infine
`remote-host`. Il preflight non crea progetti; hosting richiede il database già
inizializzato o ripristinato. Una memoria esistente usa il trasferimento sotto.

Qualsiasi provider è accettabile: servono Linux con systemd, 1 GiB di RAM
fisica, 1 vCPU, 2 GiB di swap persistente su disco e 5 GiB liberi nello storage
Docker dopo lo swap. Root e Docker condividono il filesystem; senza swap
servono 7 GiB liberi prima della preparazione. L'agente autorizzato configura
lo swap e ne conferma la persistenza. `remote-host` verifica lo swap attivo e
lo spazio, interrompendosi prima di cambiare autorità se le risorse mancano.
I membri del progetto non amministrano la VPS.

Dal checkout sulla VPS, dopo inizializzazione o restore:

```bash
dduo-solo-founder remote-host \
  --public-ip <IP-PUBBLICO> \
  --owner-name "<NOME-VISIBILE>" \
  --acme-email <EMAIL-ACME>
```

`--acme-email` è facoltativo. Acquisire privatamente l'output perché il primo
bootstrap contiene il token iniziale del gestore. Il comando:

1. Verifica Linux/systemd, CPU, RAM, swap attivo su disco e spazio Docker.
2. Importa se disponibile il login Codex file-backed del progetto e verifica
   l'abbonamento sul server prima di cambiare la modalità di deployment.
3. Installa l'agente comune `systemd --user`, lo abilita all'avvio e verifica
   che risponda con autenticazione prima di modificare progetto o database.
4. Crea i segreti remoti di autenticazione, sessione e autorità.
5. Preserva lo stack isolato e ruota la password PostgreSQL prima che i servizi
   adottino il nuovo segreto.
6. Attiva la modalità remota autenticata per API e worker e verifica da entrambi
   i container il collegamento autenticato all'agente host, prima del claim.
7. Assegna l'autorità alla VPS oppure produce una ricevuta di disponibilità in
   sola lettura per un trasferimento preparato.
8. Crea il primo **Gestore dell'infrastruttura** solo quando l'autorità è
   scrivibile e il bootstrap è necessario.
9. Registra il progetto in Caddy e stampa API, dashboard, porta HTTPS e regole
   firewall: `443` permanente e l'eventuale porta aggiuntiva del progetto. Per
   un trasferimento preparato verifica certificato TLS pubblico e percorso
   HTTPS completo fino al progetto esatto in sola lettura, prima di riportare
   `https_verified`. All'avvio ritenta per una finestra massima di 60 secondi
   gli errori temporanei di connessione/gateway; certificati non validi,
   autorizzazioni errate e identità non corrispondenti restano bloccanti.

Il primo gestore non deve già esistere nel progetto locale ripristinato. Il suo
host legge `POST /projects/{id}/authority/status` con il segreto d'autorità
isolato del progetto prima dell'attivazione. Questo controllo circoscritto non
registra membri, non concede accesso alle normali API e non rende scrivibile la
destinazione: il bootstrap del gestore attende comunque il completamento.

L'account VPS deve poter eseguire servizi utente persistenti. Se viene richiesto
linger, l'agente autorizzato esegue l'operazione amministrativa indicata, per
esempio `sudo loginctl enable-linger <UTENTE-VPS>`, verifica
`systemctl --user status` con quell'account e ripete `remote-host`. Senza il
prerequisito non c'è promozione. Token bridge e porta dinamica sono solo nel
file ambiente `0600`, non nella unit o negli argomenti del processo.
`bridge-stop` ferma la unit senza disabilitarne l'avvio automatico; il prossimo
avvio dDuo la riattiva.

Il firewall deve consentire il traffico **dalla rete Docker effettiva del
progetto alla porta TCP dell'agente host**, non da Internet. La porta corrente
è nel file privato `~/.config/dduo-solo-founder/bridge/port`; non stampare
`agent.env`, che contiene anche il token. Un gestore autorizzato verifica rete,
interfaccia e porta prima di aggiungere una regola limitata e persistente,
senza disabilitare il firewall o rimuovere regole di altri progetti. Ricontrollare
la rete dopo un restore che la ricrea e la persistenza dopo il riavvio VPS.
Il plugin non modifica automaticamente il firewall.

Il controllo dai container legge soltanto lo stato autenticato del bridge:
non esegue il sonno e non modifica dati. `container_bridge_verified: true`
conferma quel collegamento, non un consolidamento riuscito. Se fallisce,
`remote-host` indica container e categoria (rete, credenziali, configurazione
o protocollo) e non procede al claim. Riparare la causa e ripetere il comando.

Il primo token gestore viene stampato una volta. Un record privato pendente
viene salvato prima della modifica server e rimosso dopo la stampa: crash o
problemi Caddy permettono di riproporre lo stesso token al tentativo successivo.
Conservarlo privatamente e collegare il checkout del gestore sul computer:

```bash
dduo-solo-founder remote-bind \
  --project-id <ID-PROGETTO> \
  --name "<NOME-PROGETTO>" \
  --api-url <URL-API-HTTPS> \
  --dashboard-url <URL-DASHBOARD-HTTPS>
```

Il prompt nascosto evita di mettere il token nella cronologia shell. Un binding
diverso non viene sostituito senza `--replace-existing`, da usare soltanto dopo
uno spostamento deliberato. Un primo binding non ha bisogno del flag.

Il consolidamento remoto usa il bridge server. La prima chat supportata imposta
una preferenza Codex o Claude; prima di ogni passaggio la VPS la verifica e, se
login o eseguibile non sono disponibili prima dell'output, può usare l'altro
abbonamento già verificato sulla VPS. Non cambia abbonamento per limiti, timeout
o output non valido. I collaboratori non ricevono credenziali VPS o provider.
Il processo è effimero, senza shell/exec, ricerca web o checkout: il testo non
fidato della memoria non può diventare un agente che legge credenziali locali.
Se nessun login host è disponibile, il gestore autentica di nuovo Codex o Claude.

Se in una chat di progetto già autorizzata arrivano credenziali infrastrutturali,
il Founder Brief fornisce l'ID sessione esatto. L'agente abilita subito e
verifica off-record, esegue l'operazione e lo disabilita alla fine. Il turno già
aperto resta escluso da consolidamento e recupero futuro della cronologia;
le credenziali non entrano in manuale, Work, artefatti, memoria o osservabilità.
Un progetto nuovo non ha ancora una sessione dDuo: usare input privato e
`login-codex --device-auth` oppure `login-claude` ufficiale, senza inventare sessioni o dichiarare
protezioni non presenti. Codici login e token gestore restano fuori dalla chat.

<a id="invite-a-project-member"></a>
## Invitare un membro

Il **Gestore dell'infrastruttura** gestisce inviti, revoche, manuale, backup e
trasferimenti. Il **Membro del progetto** usa memoria, Work e dashboard senza
credenziali VPS. Entrambi vedono team e osservabilità. Ogni membro ha uno o più
dispositivi revocabili; il server conserva soltanto gli hash dei token.

Da un checkout autenticato del gestore, creare un invito di 24 ore:

```bash
dduo-solo-founder team-invite --display-name "<NOME-MEMBRO>" --language it
```

`--language` accetta `en` o `it`; la dashboard usa la lingua attiva.
`--expires-in-hours` accetta da 1 a 168 ore. Il prompt generato contiene release
immutabile, repository ufficiale, identità progetto, endpoint HTTPS e codice
monouso, mai SSH o password. Il collaboratore lo usa nel checkout Git
autorizzato; l'operazione incorporata equivale a:

```bash
dduo-solo-founder remote-join --invite-payload <DESCRITTORE-BASE64URL>
```

Il JSON versionato in base64url mantiene nomi ed endpoint come dati, senza
trasformarli in sintassi shell. `remote-join` verifica la radice del vero
worktree Git, crea il token privato sul dispositivo, scambia l'invito una volta
e collega solo quel checkout, senza Docker locale. Un binding locale viene
promosso solo se l'ID coincide; un altro progetto o endpoint remoto non viene
sostituito. Ripetere l'invito è accettato solo con lo stesso token registrato;
un altro dispositivo riceve conflitto. L'accesso memoria non concede Git.

Apri dashboard, Task e Piani direttamente dai link restituiti da dDuo in chat.
Ogni link remoto contiene un token browser privato valido **sette giorni**,
riutilizzabile in visite e browser diversi, senza altri login o conferme.
Chiunque lo possieda può accedere a quel progetto con i permessi del membro
che lo ha generato: non pubblicarlo. Il bearer permanente del dispositivo non
compare nel link. L'agente può anche aprirlo tramite la CLI:

```bash
dduo-solo-founder dashboard --tab tasks --project-root .
```

La dashboard scambia automaticamente il token con un cookie di sessione del
progetto `HttpOnly`, `Secure`, `SameSite=Strict`, poi toglie il token dalla barra
degli indirizzi. Il link originale in chat resta riutilizzabile fino alla
scadenza; il cookie non dura oltre. Revocare il membro o il dispositivo
invalida i suoi link e accessi browser. Alla scadenza chiedi a dDuo un nuovo
link tramite `get_dashboard_link`, senza riconfigurare il progetto. I vecchi
ticket monouso rimangono compatibili per i client precedenti. Ogni
modifica richiede anche il valore CSRF di quella sessione in `X-DDUO-CSRF`,
conservato solo nello storage browser dell'origine e rimosso a logout o errore
di autenticazione. Questo separa dashboard su porte diverse dello stesso host,
dato che i cookie non sono isolati per porta.
Il logout chiude la sessione browser, non il link riutilizzabile. Il link rimane
nella chat in cui è stato condiviso; cronologie e log di proxy esterni non sono
archivi garantiti privi di credenziali.

L'agente esegue i comandi dell'invito; il collaboratore non opera nel terminale.
Il manuale autenticato viene precaricato; un errore cache è un avviso e viene
riprovato al prossimo recupero valido. Dopo installazione/aggiornamento Codex
servono riavvio completo dell'app e nuova chat. Claude, o un nuovo binding senza
aggiornamento client, richiede una nuova sessione.

**Setup** appartiene al controllo locale fidato. La dashboard remota non apre
browser sulla VPS. Se manca la configurazione backup, la scheda **Backup** del
gestore copia una richiesta senza credenziali per la chat autorizzata. L'agente
gestisce l'operazione server; i membri non ricevono segreti o comandi terminale.

<a id="project-operating-manual"></a>
## Manuale operativo

Ogni progetto, anche individuale locale, ha un solo manuale versionato nella
scheda **Team**. Tutti i client e membri leggono la stessa versione. Contiene
procedure stabili: ramo autorevole, policy PR, gate release, deploy,
ambienti e QA ricorrente. Solo il proprietario locale o il gestore remoto
pubblica revisioni.

L'agente legge prima la versione corrente e usa `update_project_manual` dopo
richiesta diretta del gestore o conferma esplicita della modifica proposta.
I membri possono proporre, non pubblicare. Stato temporaneo e cronaca non
appartengono al manuale.

Ogni snapshot di sessione include il manuale completo se entra nel budget di
9.000 unità; altrimenti consegna un estratto esplicito con testa, coda e
puntatore `get_project_manual`. I turni ordinari riusano quello già presente e
lo ricevono di nuovo dopo una revisione o reidratazione da avvio, clear o compact
nativo. Un resume con baseline valida usa il delta.

Oltre 4.000 caratteri la dashboard suggerisce una compattazione. **Crea bozza
compatta** chiede all'esecutore di consolidamento una bozza di massimo 4.000
caratteri; se non disponibile usa un fallback deterministico. La bozza non
diventa attiva finché il gestore non la accetta come nuova versione. Il limite
di editing è 100.000 caratteri. L'uso provider registra consumo e durata propri;
il fallback non inventa consumo.

<a id="observability-in-a-team"></a>
## Osservabilità del team

Sessioni, turni, retrieval, embeddings, modifiche Work e consolidamenti
conservano l'identità autenticata del membro. Tutti vedono la scheda
Observability e possono filtrare per persona. Attività non attribuite o
precedenti all'introduzione dell'attribuzione appaiono come **System / legacy**,
senza assegnazioni retroattive inventate.

La status line Claude e lo Stop Codex forniscono solo campioni attribuibili.
Provider/modello e contatori input, cache, output e reasoning restano separati.
**API equivalente** è una stima, non un addebito; sonno ed embeddings sono
categorie distinte. Modelli sconosciuti o misure incomplete restano
**Non disponibili**, mai zero inventato. Vedere [Prezzi](pricing.it.md) per
fonti e listini. L'attribuzione spiega chi ha causato l'operazione,
senza creare memorie separate per membro.

<a id="move-the-authoritative-memory"></a>
## Spostare la memoria autorevole

Un progetto locale o remoto esistente si sposta tramite full recovery:

1. Sulla VPS di destinazione eseguire `dduo-solo-founder remote-node-id`.
   Sulla vecchia autorità eseguire
   `dduo-solo-founder remote-transfer-prepare --target-node-id <ID-NODO>`.
   Il progetto diventa `transfer_pending`, le modifiche ordinarie sono bloccate
   e viene creato il Full Recovery Bundle v2 finale. Un progetto locale riceve
   il solo segreto d'autorità necessario; nessuna password database viene ruotata.
2. Ripristinare esattamente quell'archivio sulla destinazione con la recovery
   key separata, poi eseguire `remote-host`. Il database resta in sola lettura
   e il comando restituisce `activation_receipt`, firmata e legata a progetto,
   generazione sorgente, nodo destinatario e nonce monouso, soltanto dopo il
   superamento della verifica HTTPS pubblica.
3. Verificare che `remote-host` riporti `destination_ready` e
   `https_verified: true`. Il solo avvio di Docker/Caddy o una ricevuta firmata
   non dimostrano che HTTPS pubblico funzioni. Il controllo automatico valida
   il certificato per l'host/IP configurato e raggiunge l'API attraverso il
   gateway pubblico, verificando progetto, generazione sorgente, nodo
   destinatario e ricevuta firmata, con destinazione ancora in sola lettura.
   La verifica TLS non viene mai disabilitata e i redirect non vengono seguiti.
   Un team già remoto può usare le credenziali ripristinate per leggere la dashboard;
   il primo gestore di un progetto locale nasce solo dopo il completamento.
   Se HTTPS fallisce, correggere certificato, firewall o routing e riprovare:
   la sorgente non è ritirata e la destinazione ripristinata resta in sola lettura.
   Un errore temporaneo non impone un nuovo backup né l'annullamento: mantenere
   sorgente congelata e clone in sola lettura, quindi ripetere `remote-host`.
   Non proseguire senza una verifica positiva e non aggirare errori d'identità.
   Per rinunciare ora, eliminare il clone in sola lettura e sulla sorgente usare
   `dduo-solo-founder remote-transfer-cancel --new-node-not-activated`.
   La sorgente torna scrivibile e la ricevuta precedente non è più valida.
   Dopo un annullamento servono nuovo prepare, nuovo backup finale e nuovo
   restore; non riutilizzare l'archivio o le ricevute del freeze annullato.
4. Per confermare lo spostamento eseguire sulla vecchia sorgente:

   ```bash
   dduo-solo-founder remote-transfer-retire \
     --activation-receipt '<RICEVUTA-ATTIVAZIONE>' \
     --destination-api-url '<URL-API-HTTPS>' \
     --yes
   ```

   Usare l'URL API esatto stampato da `remote-host`. `--destination-api-url` è
   obbligatorio per il primo ritiro: la sorgente ripete autonomamente i
   controlli di certificato, percorso HTTPS e identità del trasferimento subito
   prima della finalizzazione. Un errore impedisce ritiro e pulizia dei volumi;
   prima della finalizzazione è ancora possibile annullare. Un marker locale
   di ritiro verificato consente di riprovare la sola pulizia senza ripetere il
   trasferimento già finalizzato.
   Superate le verifiche, la sorgente diventa irrevocabilmente `transferred`,
   salva durevolmente `finalization_receipt`, poi elimina soltanto i propri
   vecchi volumi e la route gateway. La pulizia si può ripetere senza database.
5. Sulla destinazione ripetere `remote-host` con
   `--finalization-receipt '<RICEVUTA-FINALE>'`. Solo questa seconda prova
   avanza la generazione e rende scrivibile il progetto. Bootstrap del gestore
   e riconciliazione degli indici avvengono solo ora.
6. Collegare i computer. Il primo passaggio locale→VPS usa il token gestore del
   punto 5 con `remote-bind --replace-existing` sul checkout originale, dopo
   aver verificato che l'ID progetto coincida. Il prompt privato mantiene il
   token fuori dalla cronologia shell. Un computer già remoto riusa il token
   esistente con:

   ```bash
   dduo-solo-founder remote-rebind \
     --project-id <ID-PROGETTO> \
     --api-url https://<NUOVO-HOST>/api \
     --dashboard-url https://<NUOVO-HOST>
   ```

   Il comando conferma il cambio endpoint e verifica lo stesso membro sul nodo
   ripristinato. Un checkout locale senza token remoto non può usarlo. I nuovi
   collaboratori ricevono un invito monouso.

Non annullare dopo la finalizzazione della sorgente. La sola disponibilità
della destinazione non rende scrivibile il clone. Le ricevute HMAC usano il
segreto d'autorità del bundle cifrato e impediscono errori di progetto,
generazione, nodo, nonce o corruzione tra host dello stesso gestore fidato.
Non proteggono contro una destinazione malevola che già possiede quel segreto.
Conservare archivio finale e chiave finché il nuovo host supera i controlli di
accesso e ripristino.

Vedere [Backup e ripristino](backup-and-recovery.it.md) e
[Binding e aggiornamenti espliciti](client-binding-and-updates.it.md).
