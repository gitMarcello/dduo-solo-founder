[EN · English](troubleshooting.md) · [**IT · Italiano**](troubleshooting.it.md)

<a id="troubleshooting"></a>
# Risoluzione dei problemi

Guida per dDuo Solo Founder `0.2.0-beta.1`. Nell'uso normale non dovrebbe
servire una diagnosi tecnica: il lavoro del progetto resta disponibile anche
quando la memoria locale o remota è temporaneamente irraggiungibile.

## MCP funziona ma le nuove conversazioni non vengono registrate

MCP collegato non significa hook autorizzati. Nei progetti configurati
l'assistente chiama `check_memory_connection` prima di lavorare. Se gli hook
Codex richiedono attenzione, propone una sola scelta: rivedere il consenso
nativo nel client attivo, oppure continuare esplicitamente
senza memoria automatica. Le operazioni Work richieste restano ineseguite fino
alla scelta. La conferma vale nella conversazione, non per altri progetti o chat.
Su «fatto» il controllo viene ripetuto; un problema diverso richiede una nuova
scelta. La verifica esplicita è sempre fresca; le altre chiamate usano una cache
di massimo 30 secondi. Il controllo non autorizza hook e non avvia servizi.

`--verify` fallisce se manca l'autorizzazione. L'installazione può invece
terminare con **installato, autorizzazione necessaria**, lasciando completare
il consenso nativo. Dopo un aggiornamento del plugin applica il
[passaggio specifico per superficie](installation.it.md#required-handoff).
Verifica un turno salvato e il successivo sonno prima
di considerare tutto operativo. L'autorizzazione non recupera i turni mai catturati.
Per Claude questa verifica nativa non è disponibile: non significa che la
cattura sia guasta o verificata. Se anche MCP e Skill non vengono caricati,
dDuo non può mostrare l'avviso. La domanda è gestita dall'assistente, non è un
blocco tecnico dell'intero agente di sviluppo.

<a id="setup-did-not-finish"></a>
## Setup non è terminato

Chiedi all'assistente di completare la configurazione: propone di aprirla e ti
guida nella prossima azione necessaria. Puoi rimandare esplicitamente: non
insisterà sullo stesso problema già accettato. Al primo avvio consiglia di
attivare memoria e gestione del lavoro, lasciando l'alternativa di farne a meno.
Se preferisci la dashboard, scegli **Setup > Apri Setup**. La
pagina indica il prerequisito pertinente: Docker Desktop, chiave embedding,
connessione del provider, permesso hook Codex o attivazione del progetto.

Per un VPS Linux nuovo, installa il runtime dal checkout della release con
`./install.sh --headless`, equivalente a `--only core`: non installa adattatori
client e non apre Setup. Per installare un adattatore workstation senza aprire
Setup usa, per esempio, `node bin/install.mjs --only codex --surface cli --no-setup`.
Per l'estensione ufficiale usa `--surface vscode`, per Codex Desktop
`--surface desktop`. Queste opzioni non inizializzano la memoria del progetto.
Le nuove installazioni CLI/VS Code richiedono la CLI standalone ufficiale della
famiglia scelta: se c'è solo l'estensione, preparare il prerequisito durante
l'onboarding autorizzato, senza ripieghi nascosti su un altro runtime.

Sul VPS, dal checkout del progetto, esegui separatamente:

```bash
dduo-solo-founder remote-preflight
dduo-solo-founder init --headless --yes
```

Se manca la chiave embedding, il primo `init` crea il descrittore e termina
con codice `5` e `setup_required`. È il punto previsto per la configurazione
privata, non un errore di ripristino del database. Completa quindi gli input
privati e riprendi:

```bash
dduo-solo-founder configure-openai
dduo-solo-founder login-codex --device-auth
# Oppure usa Claude per il sonno di questo progetto:
# dduo-solo-founder login-claude
dduo-solo-founder init --headless --yes
dduo-solo-founder remote-host --public-ip <PUBLIC-IP>
```

`configure-openai` nasconde la chiave digitata. Scegli un login supportato per
il sonno: Codex usa l'archivio privato del progetto, Claude la sua configurazione
privata. Installa sul server il client scelto. Non
incollare chiavi, codici di login o il token iniziale del gestore in chat, Git
o log. Un progetto non inizializzato non ha una sessione dDuo da mettere off
record. Il secondo `init` crea la riga Project nel database: `start` da solo
non la sostituisce. Dopo l'hosting riuscito, collega la workstation con
`remote-bind` e la credenziale privata del gestore. Vedi
[Progetti remoti e team](remote-teams.it.md).

Per spostare una memoria esistente, segui il
[trasferimento dell'autorità](backup-and-recovery.it.md#move-an-authoritative-project-to-another-vps)
senza creare una nuova identità con `init`.

<a id="the-dashboard-says-connection-required"></a>
## La dashboard mostra Connection required

Non è disponibile un abbonamento verificato per il sonno sull'host della
memoria. Cambiare account nella chat non sostituisce questo login isolato: un
accesso salvato può essere revocato.
Setup verifica anche gli errori effettivi del sonno. Il plugin avvisa **nella
chat corrente**, propone prima di aprire la configurazione per ricollegarti e
lascia esplicitamente l'alternativa di continuare temporaneamente. Memorie e
turni acquisiti restano salvati; il sonno fermo non significa, da solo, che
anche salvataggio e recupero siano interrotti.

Dopo aver scelto di risolvere, premi **Connetti** in Setup e completa l'accesso
ufficiale Codex o Claude nello stato host privato del progetto. Basta un login
concluso e verificato per riprendere i lavori: aprire Setup non li riavvia.
Se la richiesta di ripresa fallisce, puoi ritentarla esplicitamente.
“Programmato” significa in coda, non recuperato: verifica un consolidamento
riuscito. La riconnessione non copia credenziali dell'host e non cambia account
silenziosamente. Su un host senza browser, il Gestore dell'infrastruttura
esegue `dduo-solo-founder login-codex --device-auth` oppure
`dduo-solo-founder login-claude` dal checkout del progetto sul server. I lavori di memoria pendenti restano salvati e il lavoro
interattivo può continuare. I collaboratori remoti chiedono al gestore di
ricollegare il server: non cambiano account locale e non avviano Docker.

<a id="the-dashboard-says-temporary-usage-limit"></a>
## La dashboard mostra Temporary usage limit

L'esecutore ha raggiunto un limite temporaneo dell'abbonamento. Non serve un
nuovo login, non viene cambiato provider e non parte un ripiego su API
generative a pagamento. Il lavoro riprova dopo l'orario indicato; **Sleep now**
consente un tentativo anticipato quando opportuno.

<a id="the-dashboard-says-memory-will-retry"></a>
## La dashboard mostra Memory will retry

Docker, l'agente locale o il processo provider sono stati temporaneamente
indisponibili. Il turno è ancora in PostgreSQL o nello spool locale privato
degli hook e verrà riprodotto al successivo prompt, sessione o tentativo utile.
Puoi continuare a lavorare.

<a id="a-turn-was-created-while-docker-was-unavailable"></a>
## Un turno è stato creato mentre Docker non era disponibile

L'hook Stop salva prompt e risposta completi in uno spool locale privato.
SessionStart e UserPromptSubmit lo riproducono in modo idempotente prima di
aprire un nuovo turno. La dashboard si aggiorna quando lo stack torna sano.

Lo stesso vale per un'interruzione remota: il client conserva il turno per il
binding remoto approvato e non avvia una memoria Docker locale sostitutiva.

<a id="remote-memory-is-unavailable"></a>
## La memoria remota non è disponibile

Continua il lavoro. dDuo non blocca l'assistente: conserva i turni completati
nello spool privato di progetto/binding e riprova lo stesso endpoint HTTPS
autorevole. Non cerca un endpoint loopback, non avvia Docker e non crea una
seconda autorità locale. `dduo-solo-founder status` mostra endpoint e
raggiungibilità; `doctor` offre una diagnosi più completa.

Se il client ha già ricevuto il manuale condiviso da una risposta autenticata,
SessionStart e UserPromptSubmit consegnano l'ultima copia verificata, avvisando
che memoria e Work possono essere assenti o non aggiornati. È disponibile
anche tramite `get_project_manual`. La cache privata autenticata HMAC è legata
a progetto, binding e credenziale esatti: alterazioni, copie su altri binding,
permessi non sicuri, revoca del token o aggiornamenti obbligatori non aggirano
l'autorità del server. Senza una copia valida, dDuo comunica soltanto
l'indisponibilità e non inventa procedure.

Verifica che VPS e IP pubblico siano disponibili e che TCP `443` resti aperta
per emissione e rinnovo ACME. Il primo progetto usa normalmente 443; gli altri
richiedono anche la propria porta tra 24443 e 25442. Il gateway mantiene il
listener delle challenge sulla 443 anche dopo la rimozione del primo progetto.
L'endpoint deve avere un certificato HTTPS valido per l'IP. Non sostituire
`https://` con `http://` e non modificare il descrittore per puntare a un altro
stack.

<a id="a-remote-binding-asks-for-approval-or-has-no-credential"></a>
## Il binding remoto chiede approvazione o non ha credenziali

L'approvazione remota è legata a percorso canonico del repository, ID progetto,
URL API e URL dashboard esatti. Copiare soltanto
`.dduo-solo-founder/project.toml` non basta. Il token del dispositivo deve
restare nel file privato `0600`, fuori da Git.

`remote-join` può promuovere un descrittore locale solo se l'ID coincide con
l'invito; non sostituisce un altro progetto o un endpoint remoto esistente.
Dopo un cambio VPS/IP usa `remote-rebind`: verifica lo stesso progetto sul
nuovo endpoint e riutilizza il token privato senza stamparlo. Riserva
`remote-bind --replace-existing` al gestore che ha verificato un trasferimento
intenzionale dell'autorità.

<a id="an-invitation-is-expired-or-already-consumed"></a>
## L'invito è scaduto o già consumato

Il codice è specifico del progetto, monouso e valido da 1 a 168 ore. Chiedi al
Gestore dell'infrastruttura un nuovo prompt generato con `team-invite`. Un
invito consumato è idempotente solo se riprovato con lo stesso token
dispositivo: non può registrare un altro dispositivo. Nessun invito concede
accesso a Git o al VPS.

<a id="the-remote-dashboard-says-unauthorized"></a>
## La dashboard remota mostra unauthorized

Aprila con `dduo-solo-founder dashboard --tab tasks`. La CLI invia il bearer
permanente solo all'API, ottiene un ticket monouso valido cinque minuti e apre
la pagina. Il ticket diventa un cookie sicuro specifico del progetto. Non
aggiungere manualmente un token dispositivo all'URL. Se l'accesso è stato
revocato, il gestore deve autorizzare nuovamente il membro, senza condividere
il token di un'altra persona.

<a id="the-project-is-read-only-during-transfer"></a>
## Il progetto è in sola lettura durante il trasferimento

`remote-transfer-prepare` imposta `transfer_pending` prima del backup finale:
le normali modifiche ricevono un conflitto. Anche il primo `remote-host` sulla
destinazione ripristinata resta in sola lettura e restituisce una
`activation_receipt`. Se rinunci a quel punto, elimina il clone ed esegui
`remote-transfer-cancel --new-node-not-activated` sulla sorgente. Per
proseguire, passa la ricevuta alla sorgente con
`remote-transfer-retire --activation-receipt '<RECEIPT>' --yes`. La sorgente
restituisce una `finalization_receipt`: usala sulla destinazione con
`remote-host --public-ip <PUBLIC-IP> --finalization-receipt '<RECEIPT>'`
per rendere scrivibile la nuova generazione.

Non annullare dopo la finalizzazione della sorgente. Se il ritiro si interrompe
durante la pulizia Docker, riesegui `remote-transfer-retire --yes`: il
marcatore privato conserva la ricevuta finale e riprende la pulizia senza
contattare PostgreSQL, dopo aver autenticato di nuovo la ricevuta. Il ritiro
elimina soltanto i vecchi volumi isolati del progetto; il recupero resta
possibile con archivio finale e chiave conservata separatamente.

<a id="remote-host-says-the-persistent-user-service-is-unavailable"></a>
## remote-host segnala che il servizio utente persistente non è disponibile

Il VPS deve mantenere l'agente condiviso attivo dopo logout e riavvio. Usa
l'esatto nome utente indicato dal comando:

```bash
sudo loginctl enable-linger <VPS-USER>
systemctl --user status
```

Poi riesegui `remote-host`: non avviare il bridge manualmente e non proseguire
con un hosting parziale. Il controllo avviene prima di rotazione password,
sostituzione database o promozione. Se un aggiornamento ha fermato l'agente,
il successivo avvio del progetto riattiva l'unità ancora abilitata; anche il
riavvio dell'host la avvia automaticamente.

`remote-preflight` verifica servizio utente Linux persistente, accesso Docker
e risorse VPS prima di creare o trasferire memoria. Segui l'errore preciso:
il server richiede almeno 1 vCPU, 1 GiB di RAM fisica, 2 GiB di swap attivo su
disco e 5 GiB liberi nello storage Docker dopo lo swap. Questa Beta richiede
che storage Docker e filesystem root condividano lo stesso filesystem
sottostante. Lo swap deve sopravvivere al riavvio. Sono requisiti del VPS,
non minimi hardware per workstation o Mac;
il client remoto non richiede uno stack Docker locale per la memoria.

<a id="codex-is-not-saving-turns"></a>
## Codex non salva i turni

Codex mantiene la fiducia negli hook come confine di sicurezza nativo.
Identificare prima la superficie attiva; se non è nota, fare una sola domanda
concisa senza dedurre Desktop dal nome client o VS Code dal terminale.

- **Codex Desktop:** apri **Settings > Hooks**, seleziona **dDuo Solo Founder**,
  poi **Review** e **Trust all**. Dopo installazione/aggiornamento chiudi
  completamente e riapri l'app prima di iniziare una nuova chat.
- **CLI Codex:** avvia una nuova sessione CLI e usa `/hooks` se la versione offre
  la revisione nativa. Non è un comando da inviare a una chat grafica.
- **Estensione Codex per VS Code:** ricarica la finestra VS Code e apri una nuova
  conversazione grafica. Il percorso di consenso non è verificato: usa soltanto
  l'approvazione nativa effettivamente offerta dall'estensione. Se manca,
  interrompi l'accettazione IDE e dichiara il limite. Un'approvazione Desktop
  o CLI non verifica l'IDE.

Mantieni `--only`, `--surface`, profilo ed eseguibile scelti quando ripeti
`--verify` dell'installer. Una verifica runtime riuscita non dimostra che l'IDE
abbia consegnato gli eventi lifecycle: usa il
[registro di accettazione](vscode-lifecycle-validation.it.md).

Se Setup segnala un ciclo di vita incompleto anziché chiedere fiducia,
aggiorna o reinstalla dDuo, applica il passaggio pertinente sopra e riapri Setup. Non continuare a
premere Trust. L'installer accetta il pacchetto solo se Codex rileva tutti e
tre gli eventi del ciclo di vita.

Su Windows l'installer corrente usa entrypoint runtime `.exe` nativi e hook
basati su Node. Errori precedenti relativi a `sh` assente o launcher npm/batch
non sono un limite intrinseco dell'adattatore corrente. Reinstalla la release
scelta, verifica che gli eseguibili Node e Codex selezionati siano disponibili,
applica il passaggio pertinente sopra e riapri Setup. Non modificare gli hook installati
con soluzioni shell non verificate. Se il problema resta, conserva l'errore
diagnostico senza credenziali ed esegui `doctor` dal progetto interessato.

<a id="setup-says-the-codex-version-is-incompatible"></a>
## Setup segnala una versione Codex incompatibile

Il manifesto hook usa `additionalContextLimit=0` perché Codex non applichi
un'ulteriore troncatura sotto il budget Founder Brief di 9.000 unità gestito
da dDuo. Serve Codex `0.150.0` o successivo. Aggiorna l'installazione scelta
da Setup, ripeti il controllo e applica il
[passaggio specifico per superficie](installation.it.md#required-handoff).
Non modificare il manifesto installato. Claude non usa questo campo e
mantiene il proprio schema hook.

<a id="founder-brief-context-is-partial-or-omitted"></a>
## Il contesto Founder Brief è parziale o omesso

È un risultato esplicito del budget, non JSON corrotto. Il Founder Brief
automatico è limitato a 9.000 unità sicure per i client, misurate come il
massimo tra punti di codice Unicode e unità UTF-16. Ogni riga emessa resta un
valore JSON completo. Una priorità stabile decide cosa includere integralmente,
parzialmente o omettere; dati meno importanti non riempiono semplicemente uno
spazio lasciato da un elemento prioritario troppo grande.

Una memoria parziale ha un estratto redatto e un riferimento `full_memory`.
Usa `explain_memory` con il suo ID quando servono testo completo, fonti o
revisioni. Memorie e task omessi restano autorevoli in PostgreSQL e possono
essere richiesti direttamente. Non aumentare il limite hook del client:
Codex lo delega già a dDuo e il payload Claude resta sotto 10.000 unità.

Un payload SessionStart identico dopo compattazione nativa è previsto: il
modello non può più fare affidamento sul contesto precedente, quindi dDuo
ricarica il Founder Brief limitato. L'osservabilità registra una nuova
occorrenza mantenendo lo stesso hash del contenuto per confronto.

Gli hook dei prompt ordinari mostrano normalmente **delta**, omettendo manuale,
profilo e Work invariati. **Snapshot** è previsto a SessionStart o quando la
base privata di consegna manca, è corrotta, incompatibile o priva di ID sessione
nativo. **Fallback** indica indisponibilità della fonte o del compositore
sicuro. “Stable context reused” descrive l'esatta rappresentazione JSONL
precedente conservata nella sessione, non token cache dichiarati dal provider.

<a id="codex-asks-to-connect-claude-or-claude-asks-to-connect-codex"></a>
## Codex chiede di collegare Claude o Claude chiede di collegare Codex

Basta un abbonamento supportato. La prima chat supportata del progetto imposta
una preferenza Codex o Claude, mentre `source_client` resta solo provenienza.
Prima del sonno l'host verifica quella preferenza e può usare l'altro abbonamento
già verificato sull'host solo se login o eseguibile preferito non sono disponibili
prima dell'output. Non cambia abbonamento per aggirare limite, timeout o output
non valido. Dopo l'aggiornamento apri una nuova chat se Setup mostra ancora il
vecchio messaggio Codex-obbligatorio; non cancellare lavori pendenti né cambiarne
a mano il provider.

<a id="the-work-dashboard-opens-the-project-picker"></a>
## La dashboard Work apre la scelta del progetto

Usa il link Work restituito da dDuo dopo una modifica: contiene UUID del
progetto e scheda Work. Se hai aperto l'URL base, copia una volta l'UUID da
quel link nel selettore.

<a id="work-appears-empty-after-an-update-or-restore"></a>
## Work sembra vuoto dopo un aggiornamento o ripristino

Controlla progetto e vista: Sprint mostra lo sprint attivo, Backlog i task
incompleti senza sprint e History il lavoro degli sprint archiviati e i task
senza sprint con stato `done` o `cancelled`. Gli sprint pianificati non diventano attivi da soli.
Aggiornamento e ripristino non assegnano il lavoro a un nuovo sprint. Il
backup conserva appartenenza e cronologia immutabile di chiusura; non decide
dove spostare il lavoro successivo.

Se un'azione segnala un conflitto di versione, aggiorna lo sprint o l'anteprima
di chiusura e ripeti l'azione sui dati correnti. Può esistere un solo sprint
attivo per progetto. La chiusura conserva gli esiti completati in History e
sposta il lavoro incompleto nello sprint pianificato scelto o nel Backlog,
senza segnarlo come completato. Verifica ID e filtri prima di duplicare task o
ripristinare nuovamente.

<a id="observability-values-are-unavailable-estimated-mixed-or-empty"></a>
## L'osservabilità mostra valori assenti, stimati, misti o vuoti

| Etichetta | Significato |
| --- | --- |
| **Reported / Riportato** | Contatori utilizzabili restituiti da provider o CLI. |
| **Estimated / Stimato** | Stima deterministica documentata, non consumo esatto del provider. |
| **Unavailable / Non disponibile** | Dato assente, invalido o non correlabile; non significa zero o operazione non eseguita. |
| **API equivalent** | Prezzo API ipotetico dell'uso in abbonamento, non fattura o quota residua. |
| **Mixed/unknown** | La stima client non identifica un solo modello. |
| **System / legacy** | Background o vecchi eventi senza membro attribuibile, non un altro archivio. |

Separare costo attribuibile embedding, equivalenti API interattivi/sleep e righe
provider/modello. Prezzi ignoti restano indisponibili; un subtotale noto non è
una fattura completa. Le stime del client Claude possono usare tariffe
configurate. Vedi [Prezzi](pricing.it.md) per snapshot e semantica cache.

I campioni Claude richiedono l'adapter status-line. Se l'output precedente si
rompe, riparare la telemetria da Setup senza sostituire a mano `statusLine`.
L'adapter conserva il comando precedente e non blocca prompt.

Codex raccoglie a Stop uso numerico correlato alle richieste. Formati ignoti,
correlazione sessione/baseline invalida o turni oltre 512 MiB o 1.000 richieste
rendono indisponibile la misura, non il salvataggio della memoria. Errori
temporanei di file/outbox conservano una sorgente privata di retry priva di
contenuti. I contatori cache indicano quantità e copertura pesata sull'input,
non passaggi di testo esatti. Più badge significano fonti di misura miste.

Non c'è ricostruzione storica. L'assenza di eventi prima dell'avvio della
raccolta è prevista; un evento solo numerico non può ricostruire il testo.
**Inspect emitted context** carica la stringa dDuo hook/MCP esatta e i
riferimenti a turno/recupero. La stima token descrive la consegna dDuo, non
l'intero prompt del modello o il consumo dichiarato dal provider.

Dimensioni candidate/emesse e riferimenti inclusi/parziali/omessi spiegano il
budget di 9.000 unità. Gli elementi omessi restano richiedibili.
`inline_expected` descrive il contratto, non prova che il modello abbia
consumato ogni byte. Il contesto stabile riutilizzato non è cache provider.

La telemetria non blocca il lavoro. Se mancano nuove operazioni effettivamente
eseguite, usare `doctor` e verificare API/worker senza condividere contenuti o
credenziali. Il filtro membro mostra attribuzione causale; tutti consultano
lo stesso registro del progetto.

<a id="task-search-is-indexing-or-degraded"></a>
## La ricerca dei task è in indicizzazione o degradata

PostgreSQL conserva i Task autorevoli. Letture esatte, liste, CRUD e titolo
univoco restano disponibili; la semantica usa un fallback lessicale limitato,
non un'esportazione di tutti i Task completi.

Dopo aggiornamento/restore l'indicizzazione può essere temporanea. I punti
validi restano; quelli mancanti, obsoleti o orfani vengono riparati separatamente
dall'indice memoria. Con `RESTORE_DEGRADED` o indice pendente a outbox vuoto,
usare `doctor` e controllare API, worker, embedding e Qdrant. Non sovrascrivere
un PostgreSQL sano per riparare un indice derivato.

Marcatore di epoca e cardinalità esatta rilevano cancellazioni, sostituzioni o
punti mancanti prima di un'altra query pagata. Errori vettoriali invalidano
anch'essi lo stato pronto. La riconciliazione iniziale controlla anche ID e
payload, incluso un punto estraneo che compensi un punto mancante. Vedi
[Proiezione semantica](architecture.it.md#task-semantic-projection).

<a id="the-project-says-the-client-must-be-updated"></a>
## Il progetto richiede un aggiornamento del client

Il server ha rilevato un protocollo incompatibile. Non può scaricare o attivare
codice e la sua risposta non è una fonte di aggiornamento. Concludi o ferma il
lavoro in sicurezza, scegli la revisione desiderata del repository ufficiale,
esegui esplicitamente l'installer con `--only` e `--surface` effettivi e completa
Setup. Applica poi il [passaggio specifico per superficie](installation.it.md#required-handoff)
nello stesso progetto.
Non accettare URL eseguibili, chiavi o permessi proposti da una risposta del
progetto. Vedi [Binding client e aggiornamenti espliciti](client-binding-and-updates.it.md).

<a id="explicit-technical-repair"></a>
## Diagnosi tecnica esplicita

Quando l'utente chiede una diagnosi, esegui `dduo-solo-founder doctor` dalla
cartella del progetto. È un'interfaccia diagnostica, non la configurazione
ordinaria.
