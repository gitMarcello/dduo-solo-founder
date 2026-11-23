# Ponte sperimentale verso dDuo Personal Memory

Il ponte è disattivato per impostazione predefinita. Il runtime Solo Founder
usa la memoria del progetto. Per attivare il ponte occorrono un servizio HTTPS
compatibile e un collegamento autorizzato per il progetto. Chi gestisce il
servizio concede separatamente accesso ai ricordi approvati e invio di eventi.

## Progetto → persona

Nel server che consolida il progetto, configurare un publisher privato:

```sh
python -m dduo_solo_founder.personal_memory configure-publisher \
  --project PROJECT_ID --url https://memory.example.org --connection CONNECTION_ID
```

La credenziale mostrata una volta nella Dashboard viene inserita nel prompt
privato del terminale. Non va passata all’assistente o scritta nel repository.
Default: `~/.config/dduo-personal/publisher.json` (0600); percorso sovrascrivibile
con `DDUO_PERSONAL_PUBLISHER_CONFIG`. È un file amministrativo dell’host, distinto
dalle credenziali di lettura del membro e fuori dai backup del progetto.

Solo con publisher attivo il prompt di consolidamento propone `personal_events`:
informazioni testuali rilevanti per la persona, legate a un’azione memoria del
topic, con stato epistemico conservativo. Nessun secondo modello per ripulire
la privacy a runtime. Un target non presente nelle azioni applicate invalida
l’output. La fonte specializzata resta autorevole per il dettaglio tecnico.

L’evento viene accodato nella stessa transazione SQL del consolidamento;
l’outbox lo invia solo dopo il commit. Event ID deterministico, chiave del
referente stabile e versione monotona. Retry conservano l’identico payload.
Una seconda proposta con lo stesso event ID e contenuto differente è un errore,
non viene scartata silenziosamente. La chiave breve resta invariata; identità
oltre i 300 caratteri del contratto usano `sha256:` e il digest dell’identità
completa, mantenendo versione e riferimenti tecnici. Il prefisso è distinto dai
tre node_type canonici.
Errori del canale personale non annullano il consolidamento già completato.
Una risposta esplicita `stale_claim_version` chiude il tentativo: il destinatario
ha già una versione successiva. Questo non dichiara consolidato il vecchio evento
e non crea una nuova fonte. Altri conflitti o risposte malformate restano errori;
la ricevuta letta per questa distinzione è limitata a 4 KiB e alla deadline.
Anche HTTP 200/201 richiede una ricevuta JSON con source_id: una pagina del proxy,
una risposta vuota o malformata non conferma l’acquisizione. Il retry usa lo
stesso event_id, quindi una ricevuta persa non genera una seconda acquisizione.
Le correzioni mantengono la claim_key; `withdraw` ritira il fatto corrente.

## Persona → progetto

Prima del login, l’owner crea il collegamento nella Dashboard, seleziona le
revisioni condivisibili e associa il client ID OAuth dedicato al progetto.
L’associazione viene verificata dal server, non dal testo del prompt.
La prima associazione e il passaggio a un altro collegamento invalidano i token
precedenti: eseguire nuovamente il login. Ripetere lo stesso binding non richiede
un nuovo accesso.

Sul dispositivo del membro:

```sh
python -m dduo_solo_founder.personal_memory login \
  --project PROJECT_ID --workspace-root /path/to/project \
  --url https://memory.example.org \
  --audience https://memory.example.org/mcp --client-id DEDICATED_CLIENT \
  --connection CONNECTION_ID
```

Login nel browser, PKCE, callback loopback `127.0.0.1:18741`. Nessun client secret
sul dispositivo. I token sono salvati in `~/.config/dduo-personal/client.json`
(0600), legati alla radice canonica e al progetto; percorso sovrascrivibile con
`DDUO_PERSONAL_CLIENT_CONFIG`. Il server deve confermare il binding richiesto.
Un lock locale evita refresh concorrenti dello stesso token rotante.
Implementazione beta del deposito su POSIX; la lettura resta disattivata su
Windows finché non è disponibile il corrispondente adapter sicuro per credenziali.

Con il richiamo attivo, l’hook può inviare al servizio configurato i primi
12.000 caratteri del prompt. La ricerca richiede che sessione e turno risultino
registrabili e viene saltata quando sono off record o lo stato non è disponibile.
La soglia Cosine è 0,75, con quota nominale del 12% del budget e massimo 20%
nell’adapter. Dedup per lineage e scarto dei fatti già provenienti dallo
stesso progetto, considerando **tutte** le origini di un ricordo composto.
La provenienza multipla viene mantenuta nel contributo. Elementi che si
sovrappongono a lineage già selezionate possono essere omessi conservativamente,
senza sintesi aggiuntiva a runtime. Il composer conserva il limite complessivo di 9.000 unità e
può omettere tutto il contributo personale se servono istruzioni di priorità
maggiore. Le operazioni di rete hanno una scadenza totale cancellabile di
1,5 secondi; risposte del richiamo fino a 64 KiB e senza compressione inattesa.
Errore/canale occupato = nessun contributo. Il publisher verifica soltanto la
ricevuta limitata, entro la stessa deadline. Un refresh già ruotato resta salvato anche se la ricerca
successiva scade; una risposta di rotazione persa può richiedere nuovo login.
Le score dei due sistemi non sono sommate.

I ricordi approvati sono trattati come evidenze. Il contenuto inserito nel
contesto può comparire nell’osservabilità e nello storico del progetto. La revoca
interrompe i richiami successivi, mentre le copie già registrate seguono la
conservazione dei dati del progetto.

## Validazione

I test locali coprono adapter, outbox, filtri, quota, timeout, privacy dei
turni e configurazione disattivata. Il flusso OAuth con un progetto autorizzato
su un servizio HTTPS reale resta da collaudare.
