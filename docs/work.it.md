[EN · English](work.md) · [**IT · Italiano**](work.it.md)

# Work: Sprint, Backlog e Storico

Work distingue il lavoro corrente da quello futuro e dallo storico concluso.
Cambiare collocazione conserva identità, stato, evidenze, allegati e revisioni
del task. Uno Sprint raggruppa un periodo di lavoro con titolo e obiettivo;
non crea un progetto o una memoria separati.

<a id="choose-the-right-view"></a>
## Scegliere la vista

| Vista | Contenuto |
| --- | --- |
| Sprint corrente | Task assegnati esplicitamente all'unico Sprint attivo |
| Backlog | Task non conclusi senza Sprint |
| Archivio / Storico | Sprint archiviati e task conclusi o annullati senza Sprint |
| Tutto il lavoro | Inventario del progetto con filtri espliciti |
| Epic | Iniziative dell'intero progetto, indipendenti dagli Sprint |
| Plan | Documenti versionati di progettazione/decisione; quelli completati o superati restano nello storico |

Board e Lista presentano l'ambito selezionato. Cambiare vista non assegna,
chiude, sposta o elimina task. Stato di esecuzione e appartenenza allo Sprint
sono campi distinti. Un Epic può attraversare più Sprint: si assegnano i suoi
task concreti, non l'Epic stesso.

Task incompleti senza Sprint compaiono nel Backlog; quelli `done` o `cancelled`
nello Storico. La vista corrente vuota significa nessun task assegnato a uno
Sprint attivo, non lavoro perso. Gli aggiornamenti non inventano Sprint passati.

<a id="plan-and-start-a-sprint"></a>
## Pianificare e avviare uno Sprint

Creare uno Sprint pianificato con titolo e obiettivo utili. Selezionare
esplicitamente i task dal Backlog o da una collocazione consentita. L'avvio
cambia lo stato da `planned` ad `active`; ogni progetto può avere un solo
Sprint attivo. Per avviarne un altro occorre risolvere quello corrente.
Creazione e avvio non assegnano automaticamente tutto il lavoro aperto.

L'assistente usa il minimo Plan, Epic o Task pertinente alla richiesta. Una
richiesta chiara di esecuzione autorizza il Work minimo necessario senza una
seconda conferma. Un'iniziativa adiacente non autorizza ad ampliare il perimetro.
In chat si usano titoli umani e deep link del progetto corretto.

<a id="close-with-a-preview"></a>
## Chiudere dopo l'anteprima

Prima della chiusura controllare titolo, versione, totale e conteggi dei task
conclusi/annullati e incompleti. Scegliere dove mandare gli incompleti: Backlog
oppure un altro Sprint pianificato. La chiusura archivia lo Sprint, vi mantiene
i task conclusi e sposta soltanto quelli incompleti, senza cambiarne lo stato.

La chiusura conserva metadati compatti immutabili, esito e destinazione;
descrizioni ed evidenze complete restano nelle revisioni del task. Modifiche
successive non riscrivono lo storico: un task trasferito resta un esito
incompleto del vecchio Sprint. Lo Storico distingue versione di chiusura e task vivo.

Il server verifica `expected_version` dell'anteprima sotto lo stesso lock del
progetto usato dalle scritture task. Se il lavoro è cambiato, aggiornare e
rivedere l'anteprima. Le modifiche usano `idempotency_key`: ripetere la stessa
richiesta riusa il risultato; usare la stessa chiave per una richiesta diversa
viene rifiutato. Un retry non crea così operazioni Sprint duplicate.

Riaprire uno Sprint archiviato lo rende pianificato. Non recupera automaticamente
i task trasferiti, non elimina le snapshot precedenti e non avvia un secondo
Sprint attivo. Se serve, riassegnare il lavoro esplicitamente.

<a id="organize-older-completed-work"></a>
## Organizzare il lavoro concluso precedente

Lo Storico precedente è consultabile senza importazioni. Per raggrupparlo
deliberatamente, selezionare task specifici di questo progetto senza Sprint,
con stato `done` o `cancelled`, rivedere la selezione e creare uno Sprint
storico. Nascono un gruppo archiviato e le snapshot di chiusura; non vengono
dedotte date, evidenze o appartenenze passate. Task incompleti, Epic e task già
assegnati a Sprint non sono selezioni valide. Non esiste una riassegnazione
automatica del lavoro storico.

<a id="find-details-without-loading-the-archive"></a>
## Recuperare i dettagli necessari

Aprire direttamente un Task o Plan noto. Se la richiesta è ambigua, cercare
una volta e riusare l'ID scelto. Le card compatte mostrano stato, priorità,
obiettivo, prossima azione e versione; richiedere dettagli working/full per
descrizioni, dipendenze, evidenze e allegati estratti solo quando servono.
Un hash di snapshot noto può evitare di ritrasmettere dettagli invariati,
mantenendo l'identità restituita.

Liste e storico espongono ambito/collocazione, totale, limite e offset. La
ricerca task usa un cursore e restituisce `next_cursor` per altri risultati,
senza un totale d'inventario. Per l'inventario completo leggere tutte le pagine
della lista: una pagina di ricerca non è l'intero archivio. Le letture esatte e
PostgreSQL sono autorevoli. La ricerca
semantica verifica progetto, versione e collocazione correnti; un indice
degradato non può inserire silenziosamente lavoro storico nello Sprint attuale.

Il briefing automatico riassume Sprint attivo e lavoro pertinente senza caricare
l'intero archivio a ogni turno. Storico ed evidenze complete restano disponibili
tramite letture esplicite. Il backup PostgreSQL include appartenenza Sprint,
ricevute delle modifiche e snapshot di chiusura; il restore non le reinterpreta.
Vedere [Motore memoria](memory-engine.it.md) e
[Backup e ripristino](backup-and-recovery.it.md).
