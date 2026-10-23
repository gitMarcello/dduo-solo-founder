[EN · English](pricing.md) · [**IT · Italiano**](pricing.it.md)

# Utilizzo e prezzi API equivalenti

L'osservabilità separa embedding a pagamento, uso interattivo e sleep.
Le stime API equivalenti dell'uso in abbonamento non sono fatture o contatori
di quota e non vanno sommate agli embedding come addebiti reali.

Il catalogo è stato confrontato con i prezzi ufficiali OpenAI e Anthropic il
**23 ottobre 2026**. GPT-6.1 Sol e Claude Opus, Sonnet e Haiku 5.5 usano un nuovo
snapshot con questa data. Le tariffe già presenti corrispondono ancora ai
listini ufficiali e conservano gli snapshot originari: **22 settembre 2026**
per GPT-6 Sol e Luna, **5 settembre 2026** per Astra e Fable 5.1 e
**29 agosto 2026** per i modelli precedenti. Le seguenti tariffe API standard
sono espresse in USD per milione di token:

| Modello | Input non in cache | Lettura cache | Scrittura cache | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 10 | 1 | 12,50 | 50 |
| GPT-6.1 Sol | 2 | 0,10 | 2,50 | 10 |
| GPT-6 Sol | 2 | 0,20 | 2,50 | 10 |
| GPT-6 Luna | 0,10 | 0,01 | 0,125 | 0,50 |
| Claude Fable 5.1 | 10 | 0,25 | 12,50 (5 minuti), 20 (1 ora) | 50 |
| Claude Opus 5.5 | 4 | 0,20 | 5 (5 minuti), 8 (1 ora) | 20 |
| Claude Sonnet 5.5 | 2 | 0,10 | 2,50 (5 minuti), 4 (1 ora) | 10 |
| Claude Haiku 5.5, fino a 100K token nel prompt | 0,10 | 0,01 | 0,125 (5 minuti), 0,20 (1 ora) | 0,50 |

Le fonti di riferimento dello snapshot sono
[documentazione Astra OpenAI](https://developers.openai.com/api/docs/models/gpt-6-astra),
[documentazione GPT-6.1 Sol OpenAI](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[documentazione Sol OpenAI](https://developers.openai.com/api/docs/models/gpt-6-sol),
[documentazione Luna OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna),
[prezzi OpenAI](https://developers.openai.com/api/docs/pricing) e
[prezzi Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).
Sono tariffe versionate usate dalla release, non un listino aggiornato in tempo
reale.

Per richieste Astra, Sol e Luna oltre **272.000 token di input**, la regola
conservata raddoppia le tariffe dei componenti input e moltiplica quelle output
per 1,5. Non applicare invariata la tabella del contesto breve a queste richieste.
GPT-6.1 Sol legge la cache al 5% del prezzo input base; GPT-6 Sol conserva la
tariffa distinta del 10%. Le scritture cache OpenAI in tabella usano la tariffa
standard a 30 minuti. Il confronto usa prezzi Standard, senza maggiorazioni
Fast/Ultrafast, sconti Batch o Flex o supplementi regionali.

Per Claude Haiku 5.5, richieste oltre **100.000 token nel prompt** moltiplicano
ogni tariffa per **5** sull'intera richiesta, compresi output e scritture cache
di entrambe le durate. La soglia conta input non in cache, letture e scritture
cache, escludendo l'output. Ogni richiesta viene valutata separatamente, anche
quando un turno contiene più richieste. Claude Opus e Sonnet 5.5 mantengono le
tariffe standard nell'intera finestra di contesto da 1M token.

GPT-6.1 Sol, GPT-6 Sol, GPT-6 Luna e i modelli Claude 5.5 hanno un massimo di
128.000 token output per richiesta: quantità maggiori restano senza prezzo.
Fable 5.1 legge la cache a 0,025 volte il prezzo input base; la vecchia voce
Fable 5 resta distinta. Le scritture cache richiedono una durata attribuibile
quando la tariffa ne dipende. Durata ignota o contatori incoerenti restano
senza prezzo.

Lo snapshot embeddings è separato: `text-embedding-3-large` costa USD 0,13 per
milione di token input. L'evento conserva consumo riportato dal provider,
tariffa e versione dello snapshot. Le osservazioni esistenti mantengono il
prezzo originario e non vengono riscritte quando cambia una voce modello.

Modelli sconosciuti, dati assenti e totali invalidi mostrano **Non disponibile**.
Uno zero misurato resta zero. Le stime calcolate dal client Claude sono
identificate e possono differire dal listino dDuo o dalla fattura. I contatori
cache descrivono quantità, non quali porzioni di prompt siano state memorizzate.
Il riuso deterministico del contesto dDuo è distinto dalla cache del provider.
Vedere [Motore memoria](memory-engine.it.md).
