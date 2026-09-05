[EN · English](installation.md) · [**IT · Italiano**](installation.it.md)

# Installazione

Su macOS e Windows nativi servono Node.js 18+, Git e Codex o Claude Code.
Docker Desktop serve soltanto per la memoria locale. Dopo una conferma,
l'agente esegue il setup; i collaboratori remoti non richiedono Docker locale.
Vedere [Piattaforme](platform-support.it.md#windows) per i confini di verifica.

Una VPS richiede Linux con systemd, 1 GiB RAM fisica, 1 vCPU, 2 GiB di swap
persistente su disco e 5 GiB liberi nello storage Docker dopo lo swap. Root e
Docker condividono il filesystem; senza swap servono 7 GiB prima del setup.
L'agente autorizzato prepara lo swap mancante; preflight e hosting verificano
le risorse prima di cambiare autorità. Seguire il [runbook VPS](platform-support.it.md).

Il pacchetto Agent Plugins 1.0 condivide `plugin.json`, `skills/` e `mcp.json`.
L'installer aggiunge l'adapter scelto da `it.dduo.client-support/`. Solo Codex
e Claude Code sono supportati: trovare il manifest non abilita altri client.

<a id="select-the-path-from-the-request"></a>
## Scegliere il percorso dalla richiesta

Non c'è un wizard aggiuntivo né una preferenza locale/remoto globale:

- **Nuovo progetto locale:** usare il [prompt README](../README.it.md)
  e completare Setup.
- **Nuovo progetto direttamente su VPS:** creare lì la prima memoria, poi
  collegare il computer. Seguire la [sequenza headless](platform-support.it.md#first-installation-directly-on-a-vps).
- **Progetto remoto esistente:** usare l'invito del gestore nel checkout Git
  autorizzato, senza attivare memoria locale.
- **Progetto esistente da spostare su VPS:** seguire il
  [trasferimento in due fasi](remote-teams.it.md#move-the-authoritative-memory).
  Il primo locale→VPS usa `remote-bind` con il nuovo token gestore; i computer
  già remoti usano `remote-rebind` con i token dispositivo esistenti.

<a id="start-with-the-canonical-prompt"></a>
## Partire dal prompt canonico

I prompt fissati alla release in [README.it.md](../README.it.md) e
[README.md](../README.md) sono la fonte unica da copiare.

Node.js 18+ e Git servono anche ai collaboratori remoti. L'installer prepara
`uv`, verifica il pacchetto e installa l'adapter scelto. Sulla VPS usare
`node bin/install.mjs --headless --project-root <CHECKOUT-SERVER> --yes` per
il runtime senza adapter o browser. Sul computer remoto usare
`--only codex --no-setup` oppure `--only claude --no-setup`, poi `remote-bind`
o `remote-join`. L'installer non installa Docker o Codex CLI.

<a id="instructions-for-the-installing-agent"></a>
## Istruzioni per l'agente che installa

Leggere questa guida e [SECURITY.it.md](../SECURITY.it.md). Spiegare brevemente
cosa aggiunge dDuo e quali prerequisiti mancano, quindi raccogliere una conferma.
Usare un checkout temporaneo della release esterno al progetto dell'utente;
installare soltanto il client supportato in uso con la radice effettiva del
progetto. Preservare memorie e binding esistenti e scegliere il percorso dalla richiesta.

Per il setup locale aprire dDuo Setup e guidare solo nelle azioni richieste,
incluse credenziali private e approvazione nativa degli hook. Non chiedere
segreti o lavoro da terminale in chat. Non aggirare consensi nativi. Dopo
installazione/aggiornamento Codex chiedere riavvio completo dell'app e nuova chat
nella cartella; Claude richiede una nuova sessione. Verificare la readiness e
indicare solo la prossima azione necessaria se manca qualcosa. Rispondere
sinteticamente nella lingua dell'utente.

<a id="what-setup-does"></a>
## Cosa fa Setup

La pagina locale appartiene all'agente host persistente dDuo. Richiede solo
le azioni mancanti:

1. Avviare Docker Desktop per la memoria locale.
2. Salvare la chiave OpenAI embeddings nella directory privata del progetto
   sotto `~/.config/dduo-solo-founder/project-secrets/` (permessi directory
   `0700`, file `0600` sui sistemi POSIX).
3. Completare il login ufficiale Codex per il consolidamento sull'host.
   La fiducia degli hook Codex resta una decisione separata del client.
4. Installare facoltativamente la telemetria Claude.
5. Attivare la cartella e aprire Work.

Le credenziali inserite tramite Setup privato restano fuori da Git e dai
contenuti condivisi. Questo non maschera segreti incollati in chat o Work:
vedere [Privacy](privacy.it.md).
La telemetria Claude preserva la status line precedente e la richiama con il
payload originale. Registra numeri attribuibili, non trascrizioni; non blocca
prompt né modifica il lifecycle.

<a id="join-an-existing-remote-project"></a>
## Entrare in un progetto remoto esistente

Il gestore crea un invito da **Team** o con
`dduo-solo-founder team-invite --display-name "<NOME>" --language it`.
La dashboard usa la lingua EN/IT selezionata. Il prompt indica release, un
progetto, endpoint HTTPS e codice monouso, mai credenziali VPS o provider.
Il descrittore codificato mantiene nomi ed endpoint come dati, non sintassi shell.

L'agente lo usa nel checkout Git autorizzato. `remote-join` crea token privato
e binding remoto senza Docker. Sostituisce un descrittore locale soltanto per
lo stesso identico progetto; altri progetti ed endpoint remoti restano intatti.
L'accesso Git va concesso separatamente.

Il manuale autenticato viene precaricato per i guasti; un errore cache avvisa
senza annullare l'accesso. L'agente apre la dashboard con
`dduo-solo-founder dashboard --tab tasks --project-root .`, usando un ticket
browser monouso, non un bearer nell'URL. Vedere [Progetti remoti e team](remote-teams.it.md)
per comandi di hosting, invito e trasferimento.

L'installazione usa le dipendenze bloccate della distribuzione verificata.
Runtime e registrazioni client tornano insieme allo stato precedente in caso
di errore; le migrazioni database no. Gli stack locali registrati ricevono
snapshot pre-upgrade; tornare al vecchio software può richiedere un ripristino
dati verificato. Vedere [Aggiornamento](update.it.md).

Gli aggiornamenti richiedono richiesta esplicita e revisione scelta. Il server
non può scaricare o attivare codice client. Memoria, Work e credenziali restano
separati dal pacchetto e da `PLUGIN_DATA`, che appartiene al singolo client.

<a id="required-handoff"></a>
## Passaggio alla nuova sessione

Dopo installazione/aggiornamento Codex, chiudere completamente e riaprire l'app,
completare la revisione hook necessaria e aprire una nuova chat del progetto.
La sola nuova chat non basta. Claude richiede una nuova sessione. Attivare un
progetto senza aggiornare il client richiede solo una nuova chat/sessione.

Un profilo vuoto chiede una volta scopo, obiettivi, principi, stato e attività;
l'invito riusa il profilo condiviso. Passi successivi facoltativi: mostrare
contesto e fonti, trasformare una priorità in Plan/Task o aprire Work. Non
vengono creati dati demo o tour obbligatori.

<a id="normal-operation"></a>
## Uso ordinario

SessionStart avvia lo stack locale isolato o contatta l'autorità remota
approvata; i prompt recuperano contesto e Stop salva i turni completati.
I link Work includono l'identità progetto. Non servono operazioni terminale.

Durante guasti di rete/server il client può usare l'ultimo manuale autenticato,
conservato privatamente per progetto, checkout e dispositivo. Memoria e Work
correnti possono mancare; non parte uno stack sostitutivo. Errori di
autorizzazione, revoca o aggiornamento obbligatorio non usano il fallback.

Per diagnosi o riparazione esplicita usare `setup`, `doctor`, `status` e
`memory-status`. La telemetria Claude si ripara da `setup` e rimane sul
computer di ciascun collaboratore.
