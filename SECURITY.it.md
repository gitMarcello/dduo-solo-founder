[EN · English](SECURITY.md) · [**IT · Italiano**](SECURITY.it.md)

# Sicurezza

dDuo Solo Founder è uno strumento di sviluppo isolato per progetto, locale o
come servizio team autenticato su VPS. Segnalare vulnerabilità tramite un
[avviso privato di sicurezza GitHub](https://github.com/gitMarcello/dduo-solo-founder/security/advisories/new)
prima di aprire issue pubbliche. Non allegare volumi Docker, file `.env`,
conversazioni grezze, credenziali, archivi recovery o liste di contatti.

<a id="installation-and-update-trust"></a>
## Fiducia nell'installazione e negli aggiornamenti

Installa e aggiorna solo su richiesta, dalla revisione del repository che scegli.
Non serve una chiave aggiuntiva per gli aggiornamenti. L'API del progetto non
può scaricare o attivare software sul tuo computer.

L'installer usa i permessi dell'utente corrente, verifica `checksums.sha256`,
materializza l'ambiente Python bloccato e cambia runtime e adapter selezionato
in una transazione. Non usa `sudo` e non elimina volumi progetto durante un
normale install/upgrade. Verifica e considera affidabile il sorgente prima di
eseguirlo, anche con `--dry-run`: l'opzione esegue comunque codice del repository
e non è una sandbox. I checksum rilevano incoerenze della distribuzione, non
l'autenticità di un fork malevolo. Un aggiornamento Codex richiede riavvio
completo dell'app e nuova chat; Claude una nuova sessione. Il client già aperto
non può ricaricare hook, Skill o MCP sostituiti.

Il pacchetto Beta usa Agent Plugins 1.0 con `plugin.json`, `skills/` e
`mcp.json`. Codex e Claude Code sono gli unici client supportati. I loro hook
sono adapter nativi verificati; altri client non ottengono capacità dDuo
scoprendo il manifest standard. `PLUGIN_ROOT` individua risorse installate;
`PLUGIN_DATA` non è autorità per progetto, memoria o credenziali perché
appartiene a un client e può essere eliminato alla disinstallazione.

Le credenziali restano isolate per progetto. La migrazione da installazioni
precedenti copia solo valori ammessi, conserva quelli già presenti e annulla
le modifiche in caso di errore; scritture concorrenti restano recuperabili.
Il runtime ordinario non eredita segreti globali o di altri progetti.

<a id="client-and-project-boundaries"></a>
## Confini tra client e progetti

Ogni repository configurato è legato a un'identità e una sola autorità:
stack locale isolato oppure endpoint remoto approvato. La titolarità della
radice viene verificata prima delle operazioni. Copiare il checkout, cambiarne
la radice canonica o riusarne il descrittore richiede move, restore o rebind
esplicito. Un checkout remoto accetta soltanto HTTPS canonico e non ripiega su
Docker locale.

Il descrittore remoto non contiene segreti. Bearer dispositivo e approvazione
dell'impronta radice/progetto/endpoint vivono in file privati fuori da Git;
il server conserva solo gli hash dei token. Gli inviti sono monouso, limitati
al progetto e privi di credenziali VPS, SSH, OpenAI o Codex. La dashboard
scambia il bearer con un ticket breve e un cookie progetto `HttpOnly`,
`Secure`, `SameSite=Strict`. Il Gestore dell'infrastruttura controlla inviti,
revoche, pubblicazione manuale, backup e trasferimenti; il Membro del progetto
usa memoria e Work.

Il bridge CLI host usa un bearer principale ad alta entropia e deriva un token
diverso per ciascun container progetto. La porta host è dinamica per permettere
ai container di raggiungere il processo in abbonamento. Il bridge rifiuta
peer esterni a loopback/reti container private e non viene pubblicato da Caddy.
Il firewall VPS deve esporre soltanto SSH e le porte HTTPS indicate.

L'autorizzazione in abbonamento viene verificata nel flusso locale ufficiale
del provider. Avviare il login nell'ambito dell'autorizzazione dell'utente a
installazione, hosting o autenticazione; codici e credenziali restano in quel
flusso protetto. L'autorizzazione hook Codex si legge dall'API app-server
ufficiale e solo l'utente può concederla nella revisione nativa. dDuo non
modifica o aggira la fiducia del client.

Per una VPS nuova usa `--headless`, `remote-preflight` e `init --headless`.
Inserisci la chiave embeddings tramite input nascosto e autentica con
`login-codex --device-auth` nel Codex home privato del progetto. Chiavi, codici
login e token gestore restano fuori da chat, log e argomenti dei comandi.
La modalità off-record richiede una sessione inizializzata e non cancella
l'audit: non sostituisce input privato o consenso nativo dell'utente.

L'ultimo manuale ottenuto da una risposta remota autenticata può essere salvato
in cache privata `0600`, autenticata HMAC con il bearer e legata al progetto e
checkout. Non è cifrato separatamente. Viene usato solo per problemi di rete o
5xx, mai per aggirare revoca, autorizzazione o incompatibilità. Trattarlo come
contenuto del progetto.

<a id="backup-and-authority-transfer"></a>
## Backup e trasferimento dell'autorità

Gli archivi cifrati con recovery key specifica possono contenere l'intera
storia, compresi segreti condivisi intenzionalmente con agenti fidati.
Full Recovery Bundle v2 include i segreti durevoli ammessi: OpenAI, database,
autenticazione, sessione, autorità, token remoto quando pertinente, code
progetto e credenziale Codex file-backed quando disponibile. Non raccoglie
arbitrariamente home, `~/.ssh`, keychain, log, sorgenti repository, stato
installazione plugin o immagini Docker.

Non allegare archivi `.dduobackup` o chiavi a issue pubbliche. Conservare backup
fuori dispositivo e chiavi in un password manager recuperabile separatamente.
La chiave è esclusa dagli archivi. Chi possiede archivio e chiave può leggere
l'intero progetto e usare le credenziali copiate finché non vengono revocate.

Il restore sostituisce dati solo dopo autenticazione dell'archivio, verifica
checksum e PostgreSQL e conferma esplicita. `--force` può sostituire un'altra
identità o proseguire un'operazione installer dopo un backup fallito: rivedere
progetto e archivio indicati prima di approvare.

Il trasferimento congela la sorgente prima del backup finale. Annullare richiede
`--new-node-not-activated`. Quando la destinazione ripristinata restituisce
`destination_ready` e HTTPS è verificato, ritirare la sorgente con
`remote-transfer-retire --activation-receipt '<RICEVUTA>' --destination-api-url '<URL-API-HTTPS>' --yes`,
usando l'URL API pubblico esatto stampato da `remote-host`. Prima di finalizzare,
la sorgente controlla autonomamente certificato TLS, percorso HTTPS completo
fino all'API e identità del trasferimento. Gli errori TLS bloccano ritiro e
pulizia dei volumi: non disabilitare la verifica dei certificati per proseguire.
Non annullare dopo che la sorgente ha restituito la ricevuta finale.
