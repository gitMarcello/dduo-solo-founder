import { createContext, type ReactNode, useContext, useEffect, useState } from 'react';

export type Language = 'en' | 'it';

const STORAGE_KEY = 'dduo.dashboard.language';

const italian = {
  'Rename project': 'Rinomina progetto',
  'Project name': 'Nome del progetto',
  'Save name': 'Salva nome',
  'The project changed. Review the name and save again.':
    'Il progetto è cambiato. Verifica il nome e salva di nuovo.',
  'Could not rename the project. Try again.':
    'Non è stato possibile rinominare il progetto. Riprova.',
  'To do': 'Da fare',
  Completed: 'Completato',
  Sprint: 'Sprint',
  'Backlog / no sprint': 'Backlog / nessuno sprint',
  'To reopen archived work, choose the backlog or another sprint. The archive keeps its original snapshot.':
    'Per riaprire un lavoro archiviato, scegli il backlog o un altro sprint. L’archivio conserva la situazione alla chiusura.',
  'Loading work details…': 'Caricamento dettagli…',
  'The drawer shows the current task. The archived sprint keeps its closing snapshot.':
    'Il dettaglio mostra il task attuale. Lo sprint archiviato conserva la situazione alla chiusura.',
  'Development cycles': 'Cicli di sviluppo',
  'Work scope': 'Ambito del lavoro',
  'Planned and active sprints': 'Sprint pianificati e attivi',
  'Archived sprints': 'Sprint archiviati',
  'Sprint details': 'Dettagli sprint',
  New: 'Nuovo',
  Filters: 'Filtri',
  'Clear filters': 'Azzera filtri',
  'Labels on this page': 'Etichette di questa pagina',
  'Search labels': 'Cerca etichette',
  'Current sprint': 'Sprint corrente',
  Archive: 'Archivio',
  'All work': 'Tutto il lavoro',
  'No active sprint. Your unfinished work remains in the backlog.':
    'Nessuno sprint attivo. Il lavoro ancora aperto resta nel backlog.',
  'Unfinished tasks without a sprint, whatever their status.':
    'Task ancora aperti senza sprint, qualunque sia il loro stato.',
  'Completed work and archived cycles stay available with their history and attachments.':
    'Il lavoro completato e i cicli archiviati restano disponibili con cronologia e allegati.',
  'Epics and plans belong to the whole project and can span several sprints.':
    'Epic e piani appartengono al progetto e possono attraversare più sprint.',
  'Choose sprint': 'Scegli sprint',
  'Sprint objective': 'Obiettivo dello sprint',
  'All archived work': 'Tutto il lavoro archiviato',
  'Load more sprints': 'Carica altri sprint',
  'New sprint': 'Nuovo sprint',
  'Start sprint': 'Avvia sprint',
  'Close and archive': 'Chiudi e archivia',
  'Reopen as planned': 'Riapri come pianificato',
  Retry: 'Riprova',
  'Loading work…': 'Caricamento lavoro…',
  '{start}–{end} of {total} items': '{start}–{end} di {total} elementi',
  'Task counts on this page.': 'Conteggi task di questa pagina.',
  'Work pages': 'Pagine del lavoro',
  'Previous page': 'Pagina precedente',
  'Next page': 'Pagina successiva',
  items: 'elementi',
  'Group selected past work': 'Raggruppa lavoro passato selezionato',
  'Choose completed tasks to group. This records a historical collection; it does not invent past sprint dates.':
    'Scegli i task completati da raggruppare. Verrà creata una raccolta storica senza attribuire date di sprint passati.',
  'Historical collection name': 'Nome della raccolta storica',
  'Archive {count} selected tasks': 'Archivia i {count} task selezionati',
  'Sprint name': 'Nome dello sprint',
  'Objective (optional)': 'Obiettivo (facoltativo)',
  'Create a planned sprint, select its tasks, then start it when ready.':
    'Crea uno sprint pianificato, scegli i task e avvialo quando sei pronto.',
  'Create sprint': 'Crea sprint',
  '{completed} completed · {unfinished} unfinished · {total} total':
    '{completed} completati · {unfinished} aperti · {total} totali',
  'The closing snapshot keeps completed work and its history. Unfinished tasks keep their status and move to your chosen destination.':
    'La chiusura conserva il lavoro completato e la sua storia. I task aperti mantengono il loro stato e vengono spostati nella destinazione scelta.',
  'Move unfinished tasks to': 'Sposta i task aperti in',
  'Choose a destination': 'Scegli una destinazione',
  'Archive sprint': 'Archivia sprint',
  'Choose project work': 'Scegli lavoro del progetto',
  'Search the whole project': 'Cerca in tutto il progetto',
  'Find another epic': 'Cerca un altro epic',
  'Find more work': 'Cerca altro lavoro',
  'No Work items on this page. Search the project to link more.':
    'Nessun elemento di lavoro in questa pagina. Cerca nel progetto per aggiungere collegamenti.',
  'Reload closing preview': 'Ricarica riepilogo di chiusura',
  'Open the epic to inspect its linked work.': 'Apri l’epic per consultare il lavoro collegato.',
  'This item changed elsewhere. Reload it, or reapply your edited fields and review before saving.':
    'Questo elemento è stato modificato altrove. Ricaricalo oppure riapplica i campi modificati e controllali prima di salvare.',
  'Reload latest': 'Ricarica versione aggiornata',
  'Reapply my changes': 'Riapplica le mie modifiche',
  Work: 'Lavoro',
  Overview: 'Panoramica',
  Memory: 'Memoria',
  Team: 'Team',
  Observability: 'Osservabilità',
  Activity: 'Attività',
  Backup: 'Backup',
  Setup: 'Configurazione',
  WORK: 'LAVORO',
  PROJECT: 'PANORAMICA',
  OVERVIEW: 'PANORAMICA',
  MEMORY: 'MEMORIA',
  TEAM: 'TEAM',
  OBSERVABILITY: 'OSSERVABILITÀ',
  ACTIVITY: 'ATTIVITÀ',
  BACKUP: 'BACKUP',
  SETUP: 'CONFIGURAZIONE',
  'Primary navigation': 'Navigazione principale',
  'Memory online': 'Memoria online',
  'Memory needs attention': 'La memoria richiede attenzione',
  'Memory unavailable': 'Memoria non disponibile',
  'Memory is up to date.': 'La memoria è aggiornata.',
  'Recent turns are waiting to be consolidated.':
    'I turni recenti sono in attesa di consolidamento.',
  'Local memory will retry automatically.': 'La memoria locale riproverà automaticamente.',
  'Memory has a temporary usage limit and will retry automatically.':
    'La memoria ha un limite di utilizzo temporaneo e riproverà automaticamente.',
  'Connect {provider} in Setup to resume memory.':
    'Collega {provider} in Configurazione per riattivare la memoria.',
  'the selected client': 'il client selezionato',
  'Refresh current view': 'Aggiorna la vista corrente',
  Refresh: 'Aggiorna',
  English: 'English',
  Italian: 'Italiano',
  Language: 'Lingua',
  'The browser access link is missing its project identifier.':
    "Nel link di accesso dal browser manca l'identificativo del progetto.",
  'Setup could not open.': 'Impossibile aprire la configurazione.',
  'Browser access could not be established: {error}':
    "Impossibile stabilire l'accesso dal browser: {error}",
  'Retrying…': 'Nuovo tentativo…',
  'Retry browser access': "Riprova l'accesso dal browser",
  'Establishing browser access…': 'Connessione dal browser…',
  'Loading project…': 'Caricamento progetto…',
  'Connect a project': 'Collega un progetto',
  'Project UUID': 'UUID del progetto',
  Connect: 'Collega',
  'Why it exists': 'Perché esiste',
  'Waiting for onboarding': "In attesa dell'onboarding",
  Objectives: 'Obiettivi',
  'No objectives yet': 'Nessun obiettivo',
  Principles: 'Principi',
  'No principles yet': 'Nessun principio',
  'Project access': 'Accesso al progetto',
  'Infrastructure manager': "Gestore dell'infrastruttura",
  'Project member': 'Membro del progetto',
  Active: 'Attivo',
  Revoked: 'Revocato',
  'Project team': 'Team del progetto',
  'Everyone shares the same project memory. Access remains isolated from every other project.':
    "Tutti condividono la stessa memoria del progetto. L'accesso resta isolato da ogni altro progetto.",
  'Loading team…': 'Caricamento team…',
  '{count} active device(s) · joined {date}': '{count} dispositivi attivi · dal {date}',
  'Revoke access for {name}': "Revoca l'accesso a {name}",
  'Revoking…': 'Revoca…',
  'Revoke access': "Revoca l'accesso",
  'Revoke project access for {name}?': "Revocare l'accesso al progetto per {name}?",
  'Team access has not been initialized yet. The trusted local owner can still manage this project.':
    "L'accesso del team non è ancora stato inizializzato. Il proprietario locale autorizzato può comunque gestire il progetto.",
  'Operational manual': 'Manuale operativo',
  'Project operating rules delivered to every assistant session, whether you work alone or with a team.':
    "Regole operative del progetto fornite a ogni sessione dell'assistente, sia quando lavori da solo sia in team.",
  'Version {version}': 'Versione {version}',
  'Loading operational manual…': 'Caricamento manuale operativo…',
  'Operational manual could not be loaded: {error}':
    'Impossibile caricare il manuale operativo: {error}',
  'Retry manual': 'Riprova il manuale',
  'Operational manual is unavailable.': 'Il manuale operativo non è disponibile.',
  'Manual content': 'Contenuto del manuale',
  '{current} / {limit} characters': '{current} / {limit} caratteri',
  Cancel: 'Annulla',
  'Saving…': 'Salvataggio…',
  'Save new version': 'Salva nuova versione',
  '{count} characters': '{count} caratteri',
  'Updated by {name}': 'Aggiornato da {name}',
  'Updated {date}': 'Aggiornato il {date}',
  'Long manual — a compact draft is recommended for review':
    'Manuale lungo — si consiglia di rivedere una bozza compatta',
  'The manual is empty': 'È necessario compilare il manuale',
  'No operational rules have been recorded yet.':
    'Non sono ancora state registrate regole operative.',
  'Edit manual': 'Modifica manuale',
  'Creating draft…': 'Creazione bozza…',
  'Create compact draft': 'Crea bozza compatta',
  'Compact draft — not yet active': 'Bozza compatta — non ancora attiva',
  'Based on version {version} · {source}': 'Basata sulla versione {version} · {source}',
  'Model-assisted': 'Assistita dal modello',
  'Deterministic fallback': 'Fallback deterministico',
  'Compact manual draft': 'Bozza compatta del manuale',
  '{count} characters · review before accepting':
    '{count} caratteri · controlla prima di accettare',
  'Discard draft': 'Scarta bozza',
  'Accept as new version': 'Accetta come nuova versione',
  'Visible across the project. Only the Infrastructure manager can publish a new version.':
    "Visibile in tutto il progetto. Solo il Gestore dell'infrastruttura può pubblicare una nuova versione.",
  'Invite a project member': 'Invita un membro del progetto',
  'The generated prompt connects one person to this project only. It never contains VPS credentials.':
    'Il prompt generato collega una sola persona esclusivamente a questo progetto. Non contiene mai credenziali VPS.',
  'Member name': 'Nome del membro',
  'Valid for': 'Valido per',
  '24 hours': '24 ore',
  '3 days': '3 giorni',
  '7 days': '7 giorni',
  'Creating…': 'Creazione…',
  'Create invitation': 'Crea invito',
  'One-time setup prompt': 'Prompt di configurazione monouso',
  'Invitation already consumed': 'Invito già utilizzato',
  'Invitation expired': 'Invito scaduto',
  'Expires {date}': 'Scade il {date}',
  'Invitation setup prompt': 'Prompt di configurazione invito',
  'This one-time invitation can no longer be used. Create a new invitation if access is still needed.':
    "Questo invito monouso non può più essere utilizzato. Creane uno nuovo se l'accesso serve ancora.",
  'Prompt copied': 'Prompt copiato',
  'Copy failed': 'Copia non riuscita',
  'Copy prompt': 'Copia prompt',
  'Prompt unavailable': 'Prompt non disponibile',
  'To share this project, use a Linux VPS from any provider with at least 1 GiB RAM, 1 vCPU, 2 GiB persistent disk-backed swap, and 5 GiB free in Docker storage after swap. Setup checks the live resources before promotion; invitations become available when shared memory is online.':
    'Per condividere questo progetto usa una VPS Linux di qualsiasi provider con almeno 1 GiB di RAM, 1 vCPU, 2 GiB di swap persistente su disco e 5 GiB liberi nello storage Docker dopo lo swap. Il setup verifica le risorse attive prima della promozione; gli inviti saranno disponibili quando la memoria condivisa sarà online.',
  'Invitations are created by the Infrastructure manager.':
    "Gli inviti vengono creati dal Gestore dell'infrastruttura.",
  'Setup & health': 'Configurazione e stato',
  'Runtime, embeddings, client sign-in and lifecycle permissions are managed by the infrastructure manager.':
    "Runtime, embedding, accesso dei client e permessi del ciclo di vita sono gestiti dal Gestore dell'infrastruttura.",
  'Opening…': 'Apertura…',
  'Open setup': 'Apri configurazione',
  'Recent activity': 'Attività recente',
  'No activity yet': 'Nessuna attività',
  'Memory engine': 'Motore della memoria',
  'Consolidating {turns} automatically.': 'Consolidamento automatico di {turns}.',
  'recent turns': 'turni recenti',
  'one turn': 'un turno',
  '{count} turns': '{count} turni',
  'Open Setup': 'Apri configurazione',
  'Consolidating…': 'Consolidamento…',
  'Consolidate now': 'Consolida ora',
  'Active memories': 'Memorie attive',
  'To consolidate': 'Da consolidare',
  Waiting: 'In attesa',
  'Up to date': 'Aggiornata',
  Updating: 'Aggiornamento',
  Limited: 'Limitata',
  'Connection required': 'Connessione richiesta',
  '{count} waiting': '{count} in attesa',
  '{count} queued': '{count} in coda',
  'No pending work': 'Nessun lavoro in sospeso',
  'Memory providers': 'Provider della memoria',
  'Temporary usage limit': 'Limite di utilizzo temporaneo',
  'Memory will retry': 'La memoria riproverà',
  'Next attempt {date}.': ' Prossimo tentativo: {date}.',
  'Open memory setup': 'Apri configurazione memoria',
  'Retry sleep': 'Riprova il sonno',
  'Consolidated memory': 'Memoria consolidata',
  'Revision {revision}': 'Revisione {revision}',
  '{count} revisions': '{count} revisioni',
  'Source: {source}': 'Fonte: {source}',
  Explain: 'Spiega',
  Hide: 'Nascondi',
  'Loading…': 'Caricamento…',
  'Source turns': 'Turni sorgente',
  'No retained source turns': 'Nessun turno sorgente conservato',
  'No consolidated memories yet': 'Nessuna memoria consolidata',
  Never: 'Mai',
  'Backup is not configured': 'Il backup non è configurato',
  'Encrypted and created automatically after changes.':
    'Cifrato e creato automaticamente dopo le modifiche.',
  'Set up once, then protected automatically.':
    'Configuralo una volta, poi sarà protetto automaticamente.',
  'Create now': 'Crea ora',
  'Enable backup': 'Attiva backup',
  'Request copied': 'Richiesta copiata',
  'Copy setup request': 'Copia richiesta di configurazione',
  'Automatic backup is not enabled': 'Il backup automatico non è attivo',
  'Backup must be enabled on the VPS': 'Il backup deve essere attivato sulla VPS',
  'Choose Enable backup once. dDuo will create encrypted archives automatically.':
    'Scegli una volta Attiva backup. dDuo creerà automaticamente archivi cifrati.',
  'Copy the request and paste it into a chat opened in this project. The assistant will configure the remote backup for you.':
    "Copia la richiesta e incollala in una chat aperta in questo progetto. L'assistente configurerà il backup remoto.",
  'Could not copy automatically. Select and copy the request manually.':
    'Copia automatica non riuscita. Seleziona e copia manualmente la richiesta.',
  'New changes pending': 'Nuove modifiche in sospeso',
  'Memory protected': 'Memoria protetta',
  'Last verified {date}': 'Ultima verifica: {date}',
  'Latest archive': 'Ultimo archivio',
  'Archive size': 'Dimensione archivio',
  'Vector index': 'Indice vettoriale',
  Included: 'Incluso',
  'Rebuild on restore': 'Ricostruisci al ripristino',
  Retention: 'Conservazione',
  daily: 'giornalieri',
  weekly: 'settimanali',
  monthly: 'mensili',
  'No verified archive yet': 'Nessun archivio verificato',
  'Download latest': "Scarica l'ultimo",
  'Download archive': 'Scarica archivio',
  'Latest backup failed': 'Ultimo backup non riuscito',
  'Copy the safe request above into an authorized chat for this project. dDuo can complete the host work with the infrastructure manager; this dashboard does not open local Setup, expose a secret, or ask a team member to use the terminal.':
    "Copia la richiesta sicura qui sopra in una chat autorizzata per questo progetto. dDuo può completare il lavoro sull'host con il Gestore dell'infrastruttura; questa dashboard non apre la configurazione locale, non espone segreti e non chiede a un membro del team di usare il terminale.",
  'The request could not be copied. Select the text above and copy it manually.':
    'Impossibile copiare la richiesta. Seleziona il testo qui sopra e copialo manualmente.',
  'Close browser session and switch project': 'Chiudi la sessione browser e cambia progetto',
  Disconnect: 'Disconnetti',
  Board: 'Bacheca',
  List: 'Elenco',
  Epics: 'Epic',
  Plans: 'Piani',
  Backlog: 'Backlog',
  'In progress': 'In corso',
  Blocked: 'Bloccato',
  Done: 'Completato',
  Cancelled: 'Annullato',
  Draft: 'Bozza',
  Decided: 'Deciso',
  'In execution': 'In esecuzione',
  Superseded: 'Superato',
  Task: 'Task',
  Epic: 'Epic',
  Plan: 'Piano',
  Labels: 'Etichette',
  Status: 'Stato',
  Priority: 'Priorità',
  Due: 'Scadenza',
  'No matching work': 'Nessun lavoro corrispondente',
  'No matching tasks': 'Nessun task corrispondente',
  'No epics yet': 'Nessun epic',
  'Group related tasks under one outcome.': 'Raggruppa i task correlati sotto un unico risultato.',
  'No plans yet': 'Nessun piano',
  'Shape a broad approach here before committing the work.':
    'Definisci qui un approccio generale prima di impegnare il lavoro.',
  Attachment: 'Allegato',
  'Remove {name}': 'Rimuovi {name}',
  'Reopen {name}': 'Riapri {name}',
  'Complete {name}': 'Completa {name}',
  '{count} attachments': '{count} allegati',
  '{priority} priority': 'Priorità {priority}',
  '{count} linked work items': '{count} elementi collegati',
  Type: 'Tipo',
  'Plan an outcome': 'Pianifica un risultato',
  'Define the work': 'Definisci il lavoro',
  'Edit {kind}': 'Modifica {kind}',
  'New {kind}': 'Nuovo {kind}',
  Close: 'Chiudi',
  'Close drawer': 'Chiudi pannello',
  Title: 'Titolo',
  'Outcome to coordinate': 'Risultato da coordinare',
  'Concrete next piece of work': 'Prossima attività concreta',
  Description: 'Descrizione',
  Rationale: 'Motivazione',
  'Completion evidence': 'Evidenze di completamento',
  'Useful context, constraints, or expected outcome': 'Contesto utile, vincoli o risultato atteso',
  'Next action': 'Prossima azione',
  'The next concrete move': 'La prossima azione concreta',
  Low: 'Bassa',
  Medium: 'Media',
  High: 'Alta',
  Critical: 'Critica',
  'Due date': 'Scadenza',
  'No epic': 'Nessun epic',
  'Type a label and press Enter': "Scrivi un'etichetta e premi Invio",
  'Add label': 'Aggiungi etichetta',
  'Add plan label': 'Aggiungi etichetta al piano',
  'Images and files': 'Immagini e file',
  'ready to upload': 'pronto per il caricamento',
  'Add images or files': 'Aggiungi immagini o file',
  'Add plan images or files': 'Aggiungi immagini o file al piano',
  '{name} exceeds the 10 MiB limit': '{name} supera il limite di 10 MiB',
  Saving: 'Salvataggio',
  'Save changes': 'Salva modifiche',
  'Create {kind}': 'Crea {kind}',
  'Edit plan': 'Modifica piano',
  'New plan': 'Nuovo piano',
  'Shape the approach': "Definisci l'approccio",
  Outcome: 'Risultato',
  'Plan title': 'Titolo del piano',
  'Plan outcome': 'Risultato del piano',
  'Plan content': 'Contenuto del piano',
  'Plan status': 'Stato del piano',
  'The decision or approach to shape': "La decisione o l'approccio da definire",
  'What this plan should make clear or achieve': 'Ciò che questo piano deve chiarire o raggiungere',
  'Context, options, decisions, risks, constraints, and the proposed path':
    'Contesto, opzioni, decisioni, rischi, vincoli e percorso proposto',
  'Linked work': 'Lavoro collegato',
  'Connect this plan to the epics and tasks it informs. It can stay unlinked while the approach is still open.':
    "Collega questo piano agli epic e ai task che guida. Può restare scollegato finché l'approccio è ancora aperto.",
  'No Work items yet.': 'Nessun elemento di lavoro.',
  'Create plan': 'Crea piano',
  '{count} open tasks': '{count} task aperti',
  'open tasks': 'task aperti',
  '{count} active epics': '{count} epic attivi',
  'active epics': 'epic attivi',
  '{count} blocked': '{count} bloccati',
  blocked: 'bloccati',
  '{count} active plans': '{count} piani attivi',
  'active plans': 'piani attivi',
  'New epic': 'Nuovo epic',
  'New task': 'Nuovo task',
  'Work view': 'Vista lavoro',
  'Search work': 'Cerca nel lavoro',
  'Clear search': 'Cancella ricerca',
  'Filter by label': 'Filtra per etichetta',
  All: 'Tutti',
  'No data': 'Nessun dato',
  Reported: 'Riportato',
  Estimated: 'Stimato',
  Unavailable: 'Non disponibile',
  'Unknown provider': 'Provider sconosciuto',
  'Unknown model': 'Modello sconosciuto',
  'Mixed/unknown': 'Misto/sconosciuto',
  '{priced} priced · {unavailable} unavailable':
    '{priced} valorizzati · {unavailable} non disponibili',
  'Topic segmentation': 'Segmentazione argomenti',
  'Memory action planning': 'Pianificazione azioni di memoria',
  '{title} over time': '{title} nel tempo',
  'Show chart data': 'Mostra dati del grafico',
  Period: 'Periodo',
  Coverage: 'Copertura',
  'No measurements in this period': 'Nessuna misurazione in questo periodo',
  'Observability range': 'Intervallo di osservabilità',
  'Usage is separated by source. Values are never combined into a false total.':
    "L'utilizzo è separato per fonte. I valori non vengono mai combinati in un totale fuorviante.",
  'Metrics available since {date} · {reported} reported · {estimated} estimated · {unavailable} unavailable':
    'Metriche disponibili dal {date} · {reported} riportate · {estimated} stimate · {unavailable} non disponibili',
  'Metrics start when this version is installed. No historical usage is inferred.':
    "Le metriche iniziano con l'installazione di questa versione. Nessun utilizzo storico viene ricostruito.",
  'Loading observability…': 'Caricamento osservabilità…',
  'dDuo context emitted': 'Contesto emesso da dDuo',
  'Exact plugin output, separated between automatic founder context and requested MCP results. Token equivalents are deterministic estimates, not model-reported consumption.':
    'Output esatto del plugin, separato tra contesto automatico del founder e risultati MCP richiesti. I token equivalenti sono stime deterministiche, non consumi riportati dal modello.',
  'Automatic injections': 'Injection automatiche',
  '{count} token estimate': '{count} token stimati',
  'Per injection: p50 {p50} · p95 {p95} estimated tokens':
    'Per injection: p50 {p50} · p95 {p95} token stimati',
  'Requested MCP results': 'Risultati MCP richiesti',
  'Automatic size': 'Dimensione automatica',
  'MCP result size': 'Dimensione risultati MCP',
  'Founder brief budget': 'Budget del Founder brief',
  'characters per automatic hook · {measured} measured · {inline} inline expected':
    'caratteri per hook automatico · {measured} misurati · {inline} inline previsti',
  'Budget utilization': 'Utilizzo del budget',
  'Selected knowledge': 'Conoscenza selezionata',
  '{partial} partial · {omitted} omitted': '{partial} parziali · {omitted} omessi',
  'Context avoided': 'Contesto evitato',
  'Stable context reused': 'Contesto stabile riutilizzato',
  'Delta {delta} · Snapshot {snapshot} · Fallback {fallback}':
    'Delta {delta} · Snapshot {snapshot} · Fallback {fallback}',
  'Legacy or unknown {count}': 'Legacy o sconosciuti {count}',
  'Already present in the live session · not provider cache':
    'Già presente nella sessione attiva · non è cache del provider',
  '{count} tokens': '{count} token',
  '{budgeted} budgeted · {fallback} fallback': '{budgeted} entro budget · {fallback} fallback',
  'Automatic context by component': 'Contesto automatico per componente',
  'Exact emitted bytes; token equivalents remain deterministic estimates.':
    'Byte emessi esatti; i token equivalenti restano stime deterministiche.',
  'Emitted context': 'Contesto emesso',
  Automatic: 'Automatico',
  'MCP results': 'Risultati MCP',
  'Interactive agent — API equivalent': 'Agente interattivo — equivalente API',
  'Codex and Claude samples appear when their local adapters report structured per-turn usage. These are API-equivalent estimates, not subscription charges.':
    'I campioni Codex e Claude compaiono quando i rispettivi adapter locali riportano dati strutturati per turno. Sono stime equivalenti API, non addebiti degli abbonamenti.',
  'Token counters keep each provider and model separate. Provider input, cache read and cache write use provider-specific semantics and are never added into a cross-provider total.':
    'I contatori separano provider e modello. Input, letture e scritture cache hanno semantiche specifiche del provider e non vengono mai sommati in un totale tra provider.',
  'Cache counters tell us how many tokens were reused, but providers do not identify the exact text spans served from cache.':
    'I contatori cache indicano quanti token sono stati riutilizzati, ma i provider non identificano le porzioni di testo esatte servite dalla cache.',
  'Claude cost is an estimate computed by Claude Code. It can reflect configured model pricing and may differ from both the current public list price and an actual bill.':
    'Il costo Claude è una stima calcolata da Claude Code. Può riflettere il listino configurato e differire sia dal listino pubblico corrente sia da una fattura reale.',
  'User: {user}': 'Utente: {user}',
  'All users': 'Tutti gli utenti',
  'System / legacy': 'Sistema / storico',
  'Selected user': 'Utente selezionato',
  'Observed samples': 'Campioni osservati',
  '{success} successful · {failed} failed': '{success} riusciti · {failed} falliti',
  'Interactive usage is unavailable': 'Utilizzo interattivo non disponibile',
  'Provider/model rows': 'Righe provider/modello',
  'Token counters are shown below without a combined total':
    'I contatori token sono mostrati sotto senza un totale combinato',
  Duration: 'Durata',
  'p50 · p95 {p95}': 'p50 · p95 {p95}',
  'API equivalent estimate': 'Stima equivalente API',
  'Catalog-derived or client-reported estimate · not actual spend':
    'Stima da listino o riportata dal client · non è una spesa reale',
  'Interactive usage by provider and model': 'Utilizzo interattivo per provider e modello',
  '{count} samples observed': '{count} campioni osservati',
  'Provider input': 'Input del provider',
  'Uncached input': 'Input non in cache',
  'Cache read': 'Lettura cache',
  'Cache hit': 'Cache hit',
  'Cache write': 'Scrittura cache',
  Output: 'Output',
  'Claude Code computed this estimate; it may reflect configured pricing and span models.':
    'Claude Code ha calcolato questa stima; può riflettere il listino configurato e più modelli.',
  '{cost} API equivalent': '{cost} equivalente API',
  'No interactive provider/model usage in this period.':
    'Nessun utilizzo interattivo per provider/modello in questo periodo.',
  'Equivalent cost breakdown': 'Dettaglio costo equivalente',
  'Uncached input cost': 'Costo input non in cache',
  'Cached input cost': 'Costo input in cache',
  'Cache write cost': 'Costo scrittura cache',
  'Output cost': 'Costo output',
  'Cost breakdown coverage: {priced} priced · {unavailable} unavailable':
    'Copertura dettaglio costi: {priced} valorizzati · {unavailable} non disponibili',
  Sleep: 'Sonno',
  'Topic segmentation and memory planning run through the authenticated CLI bridge.':
    'Segmentazione degli argomenti e pianificazione della memoria passano dal bridge CLI autenticato.',
  Passes: 'Passaggi',
  'Input tokens': 'Token input',
  'Output tokens': 'Token output',
  'Catalog-derived or Claude Code client estimate · subscription access is not billed here':
    "Stima da listino o dal client Claude Code · l'accesso in abbonamento non viene addebitato qui",
  'Sleep pass breakdown': 'Dettaglio passaggi del sonno',
  '{passes} passes · {success} successful · {failed} failed':
    '{passes} passaggi · {success} riusciti · {failed} falliti',
  'No sleep passes in this period': 'Nessun passaggio del sonno in questo periodo',
  'Sleep input tokens': 'Token input del sonno',
  'Embedding API': 'API embedding',
  'Provider usage and saved list-price costs are shown when available.':
    'Utilizzo del provider e costi da listino salvati sono mostrati quando disponibili.',
  Requests: 'Richieste',
  'Estimated attributable cost': 'Costo attribuibile stimato',
  'Embedding API only': 'Solo API embedding',
  'Embedding input tokens': 'Token input embedding',
  Reliability: 'Affidabilità',
  'Operational outcomes and end-to-end retrieval latency.':
    'Esiti operativi e latenza end-to-end del recupero.',
  'Observed operations': 'Operazioni osservate',
  '{count} failed or degraded': '{count} fallite o degradate',
  'Retrieval runs': 'Esecuzioni del recupero',
  '{count} degraded': '{count} degradate',
  'Retrieval p50': 'Recupero p50',
  'Measurement coverage': 'Copertura misurazioni',
  'Events in selected range': "Eventi nell'intervallo selezionato",
  'Recent operations': 'Operazioni recenti',
  'Metrics stay compact; exact dDuo context is loaded only when you inspect an event.':
    'Le metriche restano compatte; il contesto dDuo esatto viene caricato solo quando ispezioni un evento.',
  User: 'Utente',
  'Operation user': "Utente dell'operazione",
  Category: 'Categoria',
  'Operation category': "Categoria dell'operazione",
  'All categories': 'Tutte le categorie',
  Context: 'Contesto',
  'Interactive agent': 'Agente interattivo',
  Embedding: 'Embedding',
  Retrieval: 'Recupero',
  'Operation status': "Stato dell'operazione",
  'All statuses': 'Tutti gli stati',
  Success: 'Riuscita',
  Failed: 'Fallita',
  'Partial failure': 'Fallimento parziale',
  Degraded: 'Degradata',
  When: 'Quando',
  Operation: 'Operazione',
  Measure: 'Misura',
  'Inspect emitted context': 'Ispeziona contesto emesso',
  'Content unavailable for this historical event':
    'Contenuto non disponibile per questo evento storico',
  '{count} input tokens': '{count} token input',
  'attributable cost': 'costo attribuibile',
  'API equivalent': 'equivalente API',
  cost: 'costo',
  'No operations in this period': 'Nessuna operazione in questo periodo',
  'Loading operations…': 'Caricamento operazioni…',
  'Load more': 'Carica altro',
  'Context emitted by dDuo': 'Contesto emesso da dDuo',
  'Exact plugin delivery with a deterministic token estimate — not provider-reported consumption and not the complete model prompt.':
    'Consegna esatta del plugin con una stima deterministica dei token — non consumo riportato dal provider né prompt completo del modello.',
  'Close context inspector': 'Chiudi ispezione del contesto',
  'Loading emitted context…': 'Caricamento contesto emesso…',
  Captured: 'Acquisito',
  'Estimated tokens': 'Token stimati',
  Size: 'Dimensione',
  Client: 'Client',
  'Founder brief selection': 'Selezione Founder brief',
  'dDuo selected complete, relevant units before emitting this hook payload. “Inline expected” describes the configured delivery path; it is not proof of provider-side consumption.':
    'dDuo ha selezionato unità complete e pertinenti prima di emettere questo payload hook. “Inline previsto” descrive il percorso configurato, non prova il consumo lato provider.',
  'Client-safe units': 'Unità sicure per il client',
  'Candidate context': 'Contesto candidato',
  Selection: 'Selezione',
  '{included} included · {partial} partial · {omitted} omitted':
    '{included} inclusi · {partial} parziali · {omitted} omessi',
  'Delivery identity': 'Identità della consegna',
  Scope: 'Ambito',
  Event: 'Evento',
  Session: 'Sessione',
  Turn: 'Turno',
  'MCP tool': 'Strumento MCP',
  Estimator: 'Stimatore',
  'Turn orientation — excluded from dDuo usage':
    "Orientamento del turno — escluso dall'utilizzo dDuo",
  'User request': 'Richiesta utente',
  'Assistant response': "Risposta dell'assistente",
  'Emitted components': 'Componenti emessi',
  '{bytes} · {tokens} estimated tokens': '{bytes} · {tokens} token stimati',
  '{count} emitted': '{count} emessi',
  'of {count} candidates': 'su {count} candidati',
  '{count} partial': '{count} parziali',
  '{count} omitted': '{count} omessi',
  'References: {references}': 'Riferimenti: {references}',
  'Omitted references: {references}': 'Riferimenti omessi: {references}',
  'Memories emitted in this turn': 'Memorie emesse in questo turno',
  'Revision {revision}{score}': 'Revisione {revision}{score}',
  ' · score {score}': ' · punteggio {score}',
  'Exact emitted context': 'Contesto esatto emesso',
  Copied: 'Copiato',
  Copy: 'Copia',
} as const;

export type TranslationKey = keyof typeof italian;
export type TranslationParams = Record<string, string | number>;

function interpolate(template: string, params?: TranslationParams) {
  if (!params) return template;
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) =>
    Object.hasOwn(params, key) ? String(params[key]) : match,
  );
}

function browserLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'it') return stored;
  } catch {
    // A blocked storage preference must not prevent the dashboard from opening.
  }
  return window.navigator.language.toLowerCase().startsWith('it') ? 'it' : 'en';
}

type I18nValue = {
  language: Language;
  locale: 'en-US' | 'it-IT';
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, params?: TranslationParams) => string;
};

export type Translate = I18nValue['t'];

const defaultValue: I18nValue = {
  language: 'en',
  locale: 'en-US',
  setLanguage: () => undefined,
  t: (key, params) => interpolate(key, params),
};

const I18nContext = createContext<I18nValue>(defaultValue);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(browserLanguage);
  const locale = language === 'it' ? 'it-IT' : 'en-US';

  // Formatters outside React hooks read this value during the same render.
  document.documentElement.lang = language;

  function setLanguage(next: Language) {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The in-memory preference still works when browser storage is unavailable.
    }
    setLanguageState(next);
  }

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const value: I18nValue = {
    language,
    locale,
    setLanguage,
    t: (key, params) => interpolate(language === 'it' ? italian[key] : key, params),
  };

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
