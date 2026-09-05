[EN · English](CONTRIBUTING.md) · [**IT · Italiano**](CONTRIBUTING.it.md)

# Contribuire

dDuo Solo Founder è una memoria Beta isolata per progetto. Limitare il perimetro
delle modifiche, spiegare le scelte e aggiungere test per i rami che riguardano
persistenza, lifecycle client, installazione, recovery o isolamento.

<a id="development-setup"></a>
## Ambiente di sviluppo

```bash
uv sync --extra dev --extra benchmark
uv run ruff check backend tests benchmarks scripts
uv run pytest
cd frontend && npm ci && npm run lint && npm test && npm run build
docker compose config
```

I test Python impongono almeno il 95% di coverage con misurazione dei branch.
Il frontend ha soglie indipendenti per statement, funzioni, righe e branch.
Le modifiche installer devono preservare dry-run, install/uninstall in home
isolata, rollback transazionale, migrazione delle installazioni precedenti e checksum.
`node scripts/generate-checksums.mjs` si esegue dopo l'ultima modifica sorgente.

La matrice OS esercita installer con dipendenze bloccate, aggiornamento e
rollback, MCP, hook e autenticazione/consolidamento sintetici su Linux, macOS
e Windows. Mantenere copertura nativa `.exe`/`.cmd` e hook Node: un dry-run
POSIX non sostituisce l'installazione Windows. I test VPS senza browser coprono
`remote-preflight`, `init --headless`, login Codex privato e separazione tra
bootstrap server e binding del computer remoto.

Le modifiche Sprint devono preservare un solo Sprint attivo, collocazione
esplicita dei task, versioni ottimistiche, ricevute idempotenti e snapshot
compatte immutabili di chiusura. Non assegnare automaticamente task precedenti
e non riscrivere gli esiti passati. Verificare filtri e isolamento nelle letture
esatte, paginate e semantiche, compresi Plan conclusi e storico versionato.

Le modifiche agli embeddings esterni devono superare anche
`./scripts/e2e-live.sh` con una chiave OpenAI del maintainer. Il workflow
`Live OpenAI E2E` usa un segreto repository protetto per lo stesso controllo.

I test backup coprono cifratura autenticata, archivi malformati, hash completi,
catalogo PostgreSQL, destinazioni fallite, race di generazione e retention.
Non usare archivi di progetti reali come fixture. Il restore deve dimostrare
che le sostituzioni distruttive seguono autenticazione e consenso esplicito.

Il consolidamento deve preservare il singolo batch strutturato, gli ID fonte,
l'unica revisione corrente, l'isolamento task e l'assenza di fallback API
generativa. I test distinguono il client d'origine dall'esecutore del progetto
e non consumano abbonamenti reali nella suite ordinaria. Prima del tag, il
controllo manuale previsto esercita una chat Codex e una Claude tramite lo
stesso esecutore di progetto.

<a id="agent-plugins-contract"></a>
## Contratto Agent Plugins

`plugin.json`, `skills/` e `mcp.json` sono la superficie portabile Agent Plugins
1.0. Applicazione Python e stack Docker sono il runtime condiviso. Non spostare
dati progetto nel packaging. `PLUGIN_ROOT` individua risorse installate in
lettura; `PLUGIN_DATA` non deve diventare memoria, Work, segreti o coordinamento
autorevole fra client.

Codex e Claude Code sono gli unici client Beta supportati. Gli adapter lifecycle
restano sottili sopra runtime e MCP comuni. Modifiche a manifest, hook,
identità client o launcher MCP riguardano compatibilità e permessi e richiedono
revisione e test mirati. Il discovery del manifest non basta a supportare un
altro client: servono adapter e test del suo lifecycle.

Ogni checkout continua a risolvere una sola autorità isolata. Un binding remoto
non crea o interroga un fallback locale. Installer, migrazioni e scorciatoie
non devono far ereditare a un progetto identità, segreti, database, collection
Qdrant o token di un altro.

<a id="beta-releases"></a>
## Release Beta

L'utente sceglie esplicitamente la revisione da installare o aggiornare.
L'API del progetto non può attivare codice sul client. L'installer verifica
checksum e dipendenze bloccate, poi cambia runtime e adapter in transazione;
un errore deve lasciare utilizzabile l'installazione precedente.

Prima di creare un tag Beta:

1. Aggiornare tutte le versioni e il changelog.
2. Rigenerare `checksums.sha256` dopo l'ultima modifica.
3. Eseguire i controlli completi backend, frontend, Compose, pacchetto e installer.
4. Validare `plugin.json`, `mcp.json` e Skill distribuita.
5. Esercitare l'installer in una home isolata e verificare rollback e migrazione
   transazionale dei segreti delle installazioni precedenti.

L'accettazione Beta comprende installazione pulita, aggiornamento, nuova chat
Codex e nuova sessione Claude. I test nativi automatici usano account sintetici:
indicare separatamente prove reali di abbonamento, consenso e VPS pubblica per
ogni OS effettivamente provato. Setup verifica i prerequisiti del deployment;
la promozione stabile richiede comunque l'accettazione con client reali.

`VERSION` deve coincidere con il tag senza `v` iniziale; gli artefatti immutabili
derivano da quel commit verificato. Dopo l'aggiornamento riavviare completamente
Codex e aprire una nuova chat, oppure una nuova sessione Claude, per ricaricare
plugin, hook e MCP.

<a id="pull-requests"></a>
## Pull request

- `main` è protetto: non inviare push diretti, nemmeno da amministratore.
- Usare un branch breve, aprire una PR e mantenerla aggiornata con `main`.
- Unire soltanto dopo il successo del `CI gate`, che aggrega matrice Python
  inclusa Windows, frontend, browser e Compose.
- Il flusso solo founder non impone un numero di approvazioni ma non aggira
  controlli falliti o pendenti.
- Descrivere comportamento visibile e modalità di errore.
- Aggiungere una migrazione Alembic per ogni modifica di schema.
- Non committare credenziali, memorie progetto, volumi o chiavi benchmark.
- Preservare compatibilità Codex/Claude salvo migrazione graduale documentata.
- Le azioni esterne appartengono all'agente attivo, non al runtime dDuo.

Usare titoli commit convenzionali e imperativi. Le vulnerabilità seguono il
processo privato in [Sicurezza](SECURITY.it.md), non le issue pubbliche.
