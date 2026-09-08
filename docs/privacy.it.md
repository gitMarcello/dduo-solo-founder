[EN · English](privacy.md) · [**IT · Italiano**](privacy.it.md)

<a id="privacy-and-data-handling"></a>
# Privacy e trattamento dei dati

dDuo è pensato per agenti e membri fidati che lavorano nello stesso progetto.
I contenuti possono includere decisioni interne, contatti e materiale fornito
in chat. **Non inserire credenziali in chat, Git o log:** usa input privato
sull'host e un password manager recuperabile separatamente.

## Dove risiedono i dati

Ogni progetto possiede stack Docker, PostgreSQL, Qdrant, segreti e volumi,
in locale o sulla VPS scelta. Più progetti possono condividere VPS e gateway
HTTPS, non gli archivi della memoria. Non esiste ricerca tra progetti.

Il pacchetto plugin contiene codice. Né `PLUGIN_ROOT` né `PLUGIN_DATA`
gestito dal client sono archivi autorevoli. Lo stesso stato serve Codex e
Claude; altri client non sono supportati in questa Beta.

I file privati host hanno permessi riservati all'utente. Percorsi standard:

| Percorso sotto `~/.config/dduo-solo-founder/` | Contenuto |
| --- | --- |
| `project-secrets/` | Credenziali provider e runtime del progetto |
| `projects.json` | UUID, percorsi canonici locali e porte; niente chat o credenziali |
| `bridge/` | Token temporaneo del controllo host |
| `manual-cache/` | Cache autenticata del manuale remoto |
| `client-telemetry/` | Metadati locali di ripristino della status line Claude |
| `backup-keys/<project-id>.key` | Chiave di recupero, mai inclusa nel proprio archivio |

Anche credenziali client e stato hook/MCP pendente restano fuori da Git.
File, cache e spool sono privati, non automaticamente cifrati a riposo.
Proteggi account host e storage.

## Cosa lascia l'host

OpenAI `text-embedding-3-large` riceve testo consolidato e query semantiche.
L'indice Task separato invia titolo, obiettivo, prossima azione, descrizione ed
etichette, non evidenze, allegati, revisioni o attività. Letture per ID/titolo
esatto e liste non richiedono embedding. Creazioni e modifiche vengono salvate
senza una chiamata sincrona, ma accodano la proiezione: il worker può poi inviare
i campi semantici modificati al provider. Un provider locale configurato
esplicitamente mantiene questi testi sull'host; non cambia automaticamente.

Sleep invia turni pendenti, memorie pertinenti e testi selezionati degli
artefatti tramite l'esecutore in abbonamento Codex o Claude preferito dall'host,
in una configurazione privata del progetto. Il client d'origine resta
provenienza; l'host può usare l'altro abbonamento già verificato soltanto se il
login o l'eseguibile preferito non è disponibile prima dell'output del modello.
Sleep non dispone di strumenti sul repository, browser o sessione persistente,
né di fallback su API generative a pagamento. Restano applicabili le regole
del provider. La chiave OpenAI del progetto non passa alla CLI sleep.

## Acquisizione e modalità off record

Turni conclusi e riepiloghi della compattazione sono fonti, non diventano
automaticamente memorie consolidate fidate. I turni off record restano
nell'audit grezzo e nei backup cifrati, ma sono esclusi da sleep e dalle
ricerche future nella cronologia. **Off record non maschera né cancella.**

In una sessione inizializzata contenente credenziali infrastrutturali fornite
volontariamente, l'agente mette quella sessione off record, non copia i segreti
in Work, manuale, artefatti o memoria e ripristina l'acquisizione per i turni
futuri al termine. Attivarla esclude anche il turno aperto; disattivarla non
riabilita quel turno.

Un progetto non configurato non ha una sessione da mettere off record. Usa
input nascosto `configure-openai` e login ufficiale
`login-codex --device-auth` oppure `login-claude`, mantenendo privati codici login e token gestore.
`--headless` e `--no-setup` cambiano il setup, non la conservazione dei dati.

Gli spool di emergenza conservano il valore off record originale e lo
riproducono idempotentemente. Voci precedenti senza privacy dimostrabile
vengono riprodotte off record. Possono contenere turni completi e contesto
esatto in attesa.

## Accesso remoto e cache del manuale

I descrittori nel repository contengono identità ed endpoint HTTPS, non segreti.
I bearer dispositivo sono file privati; il server conserva solo gli hash.
Gli inviti monouso contengono repository plugin, ID progetto, endpoint e codice,
mai credenziali VPS/SSH/provider. Per la dashboard il bearer diventa un ticket
breve e un cookie sicuro del progetto; non inserire token permanenti negli URL.

L'accesso ordinario non distribuisce ai collaboratori le credenziali OpenAI o
Codex dell'host. Il bridge privato rifiuta connessioni esterne a loopback e reti
private dei container e non viene esposto dal gateway pubblico.

Solo una risposta live autenticata alimenta la cache del manuale. Testo e
metadati sono autenticati HMAC con il bearer e legati a progetto/checkout
esatti, non cifrati separatamente. Errori di rete o 5xx possono usarla con
avviso di obsolescenza; errori di autorizzazione o compatibilità no. Non esiste
una memoria locale sostitutiva o una cache del Work corrente.

## Osservabilità

Le metriche restano in PostgreSQL, senza servizio remoto o tracker. Gli eventi
numerici contengono conteggi, durate, uso provider/modello, metadati ammessi e
prezzi versionati. I costi embedding riguardano richieste API reali; gli
equivalenti API interattivi e sleep sono confronti ipotetici per uso in
abbonamento, non fatture o quote. Misure/prezzi ignoti sono **Non disponibile**,
non zero; le categorie non vengono sommate in una fattura. Vedi [Prezzi](pricing.it.md).

La status line Claude facoltativa conserva l'output esistente e raccoglie solo
uso numerico attribuibile. L'adapter isolato Codex legge la trascrizione
indicata dal client, conservando solo ID, date, modelli e contatori. Il retry
privato può mantenere percorso e cursore byte, ma non copia la trascrizione
né scrive il percorso nella telemetria PostgreSQL. Nessun adapter blocca prompt.

L'ispettore del contesto è diverso: conserva intenzionalmente la stringa esatta
hook/MCP, hash e metadati di componenti/versioni. È contenuto non mascherato,
visibile a tutti i membri autorizzati, non solo all'autore. Errori CLI grezzi,
chiavi di autenticazione e percorsi estranei non sono campi di telemetria.
Tratta comunque come potenzialmente sensibile il contenuto fornito al progetto.

Eventi e payload entrano in PostgreSQL e backup cifrati senza ricostruzioni
storiche. L'attribuzione ai membri è causale; background o vecchi record possono
mostrare **System / legacy**. Non è una separazione privata per membro.

## Backup e limiti della cancellazione

Full Recovery Bundle v2 cifrato include record database, turni grezzi, memorie,
contatti, Work/Sprint/storico immutabile, Plan e artefatti. Gli allegati sono
deduplicati per checksum e limitati a 10 MiB. Include inoltre credenziali
durevoli ammesse, `auth.json` Codex su file quando disponibile, credenziale
remota pertinente e code hook/MCP. Ricrea intenzionalmente il runtime dDuo
del progetto, non solo i dati.

Esclude home arbitrarie, `~/.ssh`, `.env` estranei, portachiavi OS, sorgenti,
immagini Docker, log, PID, token temporanei bridge, installazioni plugin e
registrazioni client. Cache manuale e adapter telemetria non vengono esportati.
I consensi nativi client devono essere rinnovati.

Ogni archivio usa una chiave AES-256-GCM distinta per progetto, conservata
separatamente. Chi possiede archivio e chiave può decifrare tutto e usare le
credenziali copiate fino alla revoca. Conserva archivi verificati fuori
dispositivo e chiavi recuperabili separatamente; prova il ripristino.

PostgreSQL è autorevole. Gli indici Qdrant vengono riconciliati dopo il restore
senza cambiare collocazioni Task o storico Sprint. Sprint, Backlog e History
sono viste, non confini di privacy. Il gestore sceglie la destinazione backup;
sincronizzazione e conservazione esterne dipendono da essa.

Vedi [Backup e ripristino](backup-and-recovery.it.md) per verifiche, recupero e
cautele sulla rimozione, e [Sicurezza](../SECURITY.it.md) per i confini di fiducia.
