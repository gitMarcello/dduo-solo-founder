[EN · English](memory-engine.md) · [**IT · Italiano**](memory-engine.it.md)

# Motore della memoria

Riferimento tecnico per `v0.2.0-beta.1`.

Il motore trasforma le conversazioni concluse in una memoria di progetto
compatta e revisionabile. Non usa la trascrizione come archivio RAG e non chiede
all'agente interattivo di inventare memorie.

## Acquisizione e recupero

Ogni turno concluso conserva i frammenti dei prompt utente nel loro ordine,
la risposta finale, gli artefatti sorgente, l'appartenenza alla sessione e
l'insieme delle memorie recuperate per quel turno. Nei client supportati, i
messaggi di steering estendono il turno esistente; sleep conserva ogni ID di
evento prompt come fonte valida.

SessionStart riceve un Founder Brief completo entro il budget, con:

- la versione corrente del manuale operativo di progetto;
- il profilo di progetto e i principi confermati;
- i Task non conclusi dello Sprint attivo oppure, in assenza di Sprint attivo,
  il lavoro non concluso senza Sprint, insieme ai Plan attivi pertinenti;
- le memorie semantiche qualificate che rientrano nel budget condiviso;
- un breve stato di salute leggibile.

I prompt successivi ricevono memorie qualificate e solo le modifiche allo stato
stabile. Manuale, profilo e Work invariati restano nel contesto della sessione;
un elemento Work esatto omesso in precedenza viene inserito una volta quando
l'utente lo nomina esplicitamente. Avvio, clear e compattazione nativa
ricaricano lo snapshot completo. La ripresa usa un delta se la baseline è
affidabile, altrimenti uno snapshot completo.

Il recupero è isolato per progetto e usa OpenAI solo per gli embedding. La
chiave rimane nella configurazione privata dell'host di progetto e non viene
passata alla CLI che esegue sleep.

La ricerca parte dal prompt corrente. I punteggi grezzi di similarità non
vengono fusi con quelli della cronologia. Una seconda query con i prompt utente
recenti viene eseguita solo per una continuazione ellittica italiana o inglese,
oppure quando la ricerca diretta riesce ma non qualifica alcuna memoria. Un
errore del provider o di Qdrant non provoca un immediato tentativo storico.
Tra i candidati, ID esatti e chiavi dei nodi hanno precedenza. PostgreSQL
mantiene solo le memorie attive utilizzabili, seleziona l'ultima revisione per
tipo/chiave ed elimina i duplicati.

Non esistono un K fisso di risultati emessi o quote per tipo. La richiesta
Qdrant top-48 è una finestra tecnica di candidati; non promette 48 memorie nel
contesto. La consegna dipende dalle 9.000 unità del Founder Brief, condivise con
manuale, profilo, Plan, Task, passaggi di consegne e stato di salute.

Work è stato autorevole PostgreSQL, distinto dalle memorie. Recupero e sleep
non assegnano Task, avviano Sprint o spostano lavoro: servono operazioni
esplicite, versionate e idempotenti. Il lavoro concluso resta consultabile
fuori dal briefing attivo. Vedi [Work](work.it.md) per chiusure Sprint e storico.

## Rappresentazione delle memorie nel contesto

Il contesto automatico usa JSON Lines deterministico. Ogni memoria è un record
indipendente con ID, tipo, chiave stabile, revisione e testo autorevole.
Appartenenza al progetto, fonti, job, timestamp e provenienza delle revisioni
restano in PostgreSQL. Gli ID duplicati vengono rimossi prima della composizione.

La stringa completa non supera 9.000 unità sicure per i client, definite come
il maggiore tra punti di codice Unicode e unità UTF-16. Ogni memoria viene
emessa intera, tramite un estratto esplicito, oppure omessa secondo una priorità
stabile. Il compositore non taglia valori JSON e non inventa sottostringhe.
Un estratto conserva i campi identificativi, è marcato come parziale e include
`{"tool":"explain_memory","memory_id":"..."}` per leggere testo completo,
fonti e catena delle revisioni. Codex 0.150.0 o successivo riceve il contenuto
tramite `additionalContextLimit=0`; Claude conserva il proprio schema hook e
riceve lo stesso payload sotto le 10.000 unità.

## Consolidamento in una chiamata

Sleep raggruppa al massimo otto turni e 40.000 caratteri. Un'unica chiamata
strutturata all'esecutore in abbonamento dell'host raggruppa gli argomenti e
decide quali memorie durevoli creare, aggiornare, sostituire o ritirare. Lo
schema valida ogni turno, messaggio, memoria e artefatto citato prima di
modificare PostgreSQL.

La prima sessione supportata stabilisce una preferenza stabile per il sonno:
Codex o Claude. Prima di ogni passaggio l'host verifica l'abbonamento preferito
ed esegue quello quando è disponibile. Se login o eseguibile non sono disponibili
prima dell'output del modello, può usare l'altro abbonamento già verificato
sull'host; non cambia silenziosamente per aggirare limite, timeout o output non
valido. `source_client` resta solo provenienza e non divide la coda. Ogni job
registra preferenza ed esecutore effettivo; la memoria risultante appartiene al
progetto ed è condivisa subito tra i due client. Non esistono fallback generativi
automatici su API a pagamento o memorie temporanee per il passaggio di consegne.

## Manuale operativo e fallback remoto

Il manuale versionato contiene regole permanenti per singoli e team, non
memorie generate o stato transitorio. Gli snapshot completi lo includono;
i delta lo ripetono solo dopo una revisione. Se supera il budget, un estratto
iniziale/finale esplicito rimanda a `get_project_manual`.

Pubblicare con `update_project_manual` richiede una richiesta diretta autorizzata
o una proposta concisa confermata. Leggere prima la versione corrente: valgono
gli stessi permessi gestore, concorrenza ottimistica e idempotenza della dashboard.

Per un collegamento remoto, il client conserva solo il manuale restituito da
una risposta live autenticata. La cache è un file privato `0600`, autenticato
con il bearer del dispositivo e vincolato all'esatto progetto e collegamento
del checkout. Hook e `get_project_manual` possono usarlo dopo errori di rete o
risposte 5xx, segnalando esplicitamente che può essere obsoleto. Una cache
assente non produce un manuale inventato; errori di autorizzazione o obblighi
di aggiornamento non vengono aggirati. Memorie e Work non sono in cache e il
client non avvia un'autorità locale sostitutiva.

## Lingua delle nuove memorie

La lingua viene scelta separatamente per ogni argomento consolidato. Vince
una richiesta esplicita del founder; altrimenti conta l'ultimo messaggio utente
sostanziale su quell'argomento. Un input chiaramente inglese produce testo
inglese. L'italiano è il valore predefinito per input italiani o ambigui; quando
si revisiona una memoria con input ambiguo, si conserva la lingua esistente.
Testo dell'assistente, intestazioni degli allegati, artefatti, citazioni e log/codice
non decidono la lingua. Il prompt richiede esplicitamente l'italiano come default.

Il consolidatore restituisce `it` o `en` come valore strutturato. La lingua è una
preferenza, non un blocco: un risultato altrimenti valido nell'altra lingua viene
salvato invariato, senza chiamate di traduzione o ritentativi dovuti alla sola lingua.
Il job registra `language_warnings`, cioè il numero di argomenti interessati.
Il metadato della lingua usa il rilevamento del testo quando chiaro, altrimenti
la dichiarazione del modello: non è una garanzia di riconoscimento linguistico.
I controlli su struttura, contenuto e fonti rimangono attivi. Chiavi stabili e ID sorgente
rimangono invariati: la lingua non divide un fatto in memorie parallele. La
regola riguarda solo i nuovi consolidamenti; memorie e revisioni precedenti
non vengono tradotte, riscritte o ricostruite retroattivamente.

## Pianificazione di sleep

Sleep è asincrono e viene pianificato dopo uno di questi segnali:

- otto turni in attesa;
- venti minuti di inattività;
- un confine di argomento richiesto esplicitamente dal modello interattivo;
- una richiesta manuale dalla dashboard o da MCP.

Il modello può suggerire un confine, ma la decisione semantica finale spetta
al consolidatore. Inattività e soglia coprono le conversazioni terminate senza
un segnale esplicito.

## Errori tipizzati e nuovi tentativi

Solo questi errori lasciano un job sleep in attesa:

| Tipo | Comportamento |
| --- | --- |
| `auth_required` | Avvisa in chat e chiede se ricollegare o continuare temporaneamente. Riprende solo dopo accesso nativo verificato; sui progetti remoti interviene il gestore dell'infrastruttura. |
| `rate_limited` | Attende la finestra registrata e riprova automaticamente. |
| `bridge_unavailable` | Riprova localmente con intervalli esponenziali. |
| `dependency_unavailable` | Riprova dopo il ripristino della dipendenza locale mancante. |
| `invalid_model_output` | Conserva il gruppo di turni e riprova dopo il fallimento della validazione. |

Non ci sono fallback generativi automatici su API, cambi di provider o blocchi
della chat interattiva. PostgreSQL conserva il gruppo originale fino al
consolidamento o alla sua rimozione esplicita da parte dell'utente.

## Revisioni e provenienza

Le memorie hanno gruppi e revisioni. Sostituire un fatto crea una revisione;
quelle precedenti restano consultabili. `explain_memory` restituisce turni,
ID dei messaggi, artefatti e catena delle revisioni. Ogni modifica validata
raggiunge Qdrant tramite un outbox idempotente: l'indice può essere ricostruito
da PostgreSQL senza perdere la storia semantica.
