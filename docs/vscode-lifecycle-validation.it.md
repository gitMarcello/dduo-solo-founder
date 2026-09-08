[EN · English](vscode-lifecycle-validation.md) · [**IT · Italiano**](vscode-lifecycle-validation.it.md)

# Registro di accettazione del lifecycle VS Code

Questo registro decide se dDuo può dichiarare funzionante l’integrazione VS
Code **ufficiale** di Codex o Claude Code su uno specifico sistema operativo.
Non sostituisce i test unitari e non può essere compilato osservando solo lo
stdout di una CLI.

## Perimetro e sicurezza

Creare un repository eliminabile, un nuovo id progetto e una memoria locale o
VPS dedicata. Non attivare dDuo nel repository di dDuo. Non usare progetto,
memoria, export account, token, percorso, conversazione o dati cliente reali.
Eliminare la memoria di prova solo dopo aver completato il registro.

L’esito vale soltanto per questa combinazione:

```text
famiglia client + versione client + versione VS Code + versione estensione + OS + modalità memoria
```

WSL, Remote SSH e Dev Containers non rientrano nella Beta. Un checkout locale
normale collegato a una VPS dDuo è un test di memoria remota, non di IDE remoto.

## Procedura di prova

1. Annotare i sei valori indicati e la release dDuo selezionata.
2. Installare con `--only <codex|claude> --surface vscode` nella configurazione
   effettiva del client. Completare soltanto una UI nativa realmente proposta.
3. Ricaricare la finestra VS Code e aprire una **nuova conversazione grafica**.
   Una CLI aperta nel terminale integrato non è una prova.
4. Raccogliere envelope anonimizzati dell’inizializzazione MCP e dei tre eventi
   lifecycle. Conservare solo nomi campi, booleani e id opachi; sostituire ogni
   valore che possa identificare persona, computer, account o progetto.
5. Inserire nella memoria test una frase artificiale unica. Nella successiva
   chat grafica porre una domanda risolvibile solo se quella frase è arrivata
   come contesto dDuo. La frase non deve stare nel prompt.
6. Completare due turni brevi. Verificare separatamente SessionStart,
   UserPromptSubmit e Stop per la sessione nativa e che ogni turno sia salvato
   una sola volta. Verificare un’operazione MCP e un consolidamento sull’host.
7. Aprire una seconda chat grafica e recuperare il ricordo artificiale.

`context_emitted_at` prova solo che dDuo ha emesso l’envelope. Il controllo con
la frase unica prova che il client grafico ha accettato contesto utile. Un HTTP
200 non basta: il turno esatto deve riportare `committed: true`.

## Template delle evidenze anonimizzate

Conservare questo file con il risultato di accettazione, mai nel progetto
dell’utente o in una issue pubblica. Sostituire gli id con placeholder stabili e
non copiare testo dalla chat.

```json
{
  "record_version": 1,
  "result": "pass | fail | blocked",
  "client": "codex | claude",
  "surface": "vscode",
  "os": "macos | windows",
  "memory_mode": "local | remote",
  "versions": {"dduo": "<release>", "vscode": "<version>", "extension": "<version>", "client": "<version>"},
  "native_approval": "approved | not_offered | unavailable",
  "mcp_initialize": {"observed": false, "client_identity": "<redacted>"},
  "hooks": {
    "session_start": {"observed": false, "session": "<opaque>"},
    "user_prompt_submit": {"observed": false, "turn": "<opaque>"},
    "stop": {"observed": false, "turn": "<opaque>", "response_available": false}
  },
  "context_phrase_was_used": false,
  "two_turns_persisted_once": false,
  "mcp_operation": false,
  "sleep_on_memory_host": false,
  "second_chat_retrieval": false,
  "notes": "nessun dato personale, di progetto o della chat"
}
```

## Esito superato, fallito o bloccato

Superare solo quando tutti i booleani sono veri e il percorso di approvazione
nativa è noto. Un client che non carica MCP, non esegue un hook richiesto, non
fornisce un’identità di sessione stabile o una risposta finale utilizzabile
**fallisce** per quella superficie. Non aggiungere polling nascosto o scraping
della cronologia.

Se il vendor non offre un percorso di approvazione nativa o manca la UI
necessaria, segnare **blocked**, conservare le evidenze anonimizzate e non
dichiarare supportata la superficie. L’altro client o la CLI possono restare
supportati.
