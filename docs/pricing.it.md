[EN · English](pricing.md) · [**IT · Italiano**](pricing.it.md)

# Utilizzo e prezzi API equivalenti

L'osservabilità separa embedding a pagamento, uso interattivo e sleep.
Le stime API equivalenti dell'uso in abbonamento non sono fatture o contatori
di quota e non vanno sommate agli embedding come addebiti reali.

GPT-6.2 Astra, GPT-6.2 Sol e GPT-6.1 Terra sono stati verificati sulle rispettive
pagine ufficiali OpenAI e sul listino il **17 novembre 2026** e usano un nuovo
snapshot con questa data. Le voci esistenti conservano gli snapshot precedenti:
**23 ottobre 2026** per GPT-6.1 Sol e Claude Opus, Sonnet e Haiku 5.5,
**22 settembre 2026** per GPT-6 Sol e Luna, **5 settembre 2026** per GPT-6 Astra
e Fable 5.1 e **29 agosto 2026** per i modelli precedenti. L'aggiunta delle nuove
voci non riscrive i costi salvati. Le seguenti tariffe API standard
sono espresse in USD per milione di token:

| Modello | Input non in cache | Lettura cache | Scrittura cache | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6.2 Astra | 8 | 0,40 | 10 | 40 |
| GPT-6.2 Sol | 2 | 0,10 | 2,50 | 10 |
| GPT-6.1 Terra | 1 | 0,05 | 1,25 | 5 |
| GPT-6 Astra | 10 | 1 | 12,50 | 50 |
| GPT-6.1 Sol | 2 | 0,10 | 2,50 | 10 |
| GPT-6 Sol | 2 | 0,20 | 2,50 | 10 |
| GPT-6 Luna | 0,10 | 0,01 | 0,125 | 0,50 |
| Claude Fable 5.1 | 10 | 0,25 | 12,50 (5 minuti), 20 (1 ora) | 50 |
| Claude Opus 5.5 | 4 | 0,20 | 5 (5 minuti), 8 (1 ora) | 20 |
| Claude Sonnet 5.5 | 2 | 0,10 | 2,50 (5 minuti), 4 (1 ora) | 10 |
| Claude Haiku 5.5, fino a 100K token nel prompt | 0,10 | 0,01 | 0,125 (5 minuti), 0,20 (1 ora) | 0,50 |

Le fonti di riferimento dello snapshot sono
[documentazione GPT-6.2 Astra OpenAI](https://developers.openai.com/api/docs/models/gpt-6.2-astra),
[documentazione GPT-6.2 Sol OpenAI](https://developers.openai.com/api/docs/models/gpt-6.2-sol),
[documentazione GPT-6.1 Terra OpenAI](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[documentazione Astra OpenAI](https://developers.openai.com/api/docs/models/gpt-6-astra),
[documentazione GPT-6.1 Sol OpenAI](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[documentazione Sol OpenAI](https://developers.openai.com/api/docs/models/gpt-6-sol),
[documentazione Luna OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna),
[prezzi OpenAI](https://developers.openai.com/api/docs/pricing) e
[prezzi Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).
Sono tariffe versionate usate dalla release, non un listino aggiornato in tempo
reale.

Per richieste Astra, Sol, Terra e Luna oltre **272.000 token di input**, la regola
conservata raddoppia le tariffe dei componenti input e moltiplica quelle output
per 1,5. Non applicare invariata la tabella del contesto breve a queste richieste.
Le nuove voci di novembre e GPT-6.1 Sol leggono la cache al 5% del prezzo input
base; GPT-6 Sol e GPT-6 Astra conservano la tariffa distinta del 10%.
Le scritture cache OpenAI in tabella usano la tariffa
standard a 30 minuti. Il confronto usa prezzi Standard, senza maggiorazioni
Fast/Ultrafast, sconti Batch o Flex o supplementi regionali.

Per Claude Haiku 5.5, richieste oltre **100.000 token nel prompt** moltiplicano
ogni tariffa per **5** sull'intera richiesta, compresi output e scritture cache
di entrambe le durate. La soglia conta input non in cache, letture e scritture
cache, escludendo l'output. Ogni richiesta viene valutata separatamente, anche
quando un turno contiene più richieste. Claude Opus e Sonnet 5.5 mantengono le
tariffe standard nell'intera finestra di contesto da 1M token.

Le voci di novembre verificano un massimo di **922.000 token input** e
**128.000 token output**, entro una finestra di contesto da 1.050.000 token.
Quantità oltre i due limiti restano senza prezzo, compreso l'input in cache.
Anche GPT-6.1 Sol, GPT-6 Sol, GPT-6 Luna e i modelli Claude 5.5 hanno un massimo di
128.000 token output per richiesta: quantità maggiori restano senza prezzo.
Fable 5.1 legge la cache a 0,025 volte il prezzo input base; la vecchia voce
Fable 5 resta distinta. Le scritture cache richiedono una durata attribuibile
quando la tariffa ne dipende. Durata ignota o contatori incoerenti restano
senza prezzo.

Lo snapshot embeddings è separato: `text-embedding-3-large` costa USD 0,13 per
milione di token input. L'evento conserva consumo riportato dal provider,
tariffa e versione dello snapshot. Le osservazioni esistenti mantengono il
prezzo originario e non vengono riscritte quando cambia una voce modello.

Quando un campione interattivo o del sonno non ha un prezzo né una versione
di listino salvati, la dashboard può stimarne l'equivalente API dal catalogo
versionato. Calcola ogni richiesta separatamente e include il risultato nei
totali per modello, operazione, periodo e componente. Mostra quanti campioni
sono stati valorizzati così e la data del listino. È un confronto retrospettivo
con quel catalogo, non una fattura o un prezzo accertato al momento della raccolta.
Il record salvato resta invariato, compresi prezzi già presenti e zeri.
Contatori mancanti, modelli ignoti e scritture cache senza durata attribuibile
restano senza prezzo; gli addebiti embedding non usano questo calcolo aggiuntivo.

Modelli sconosciuti, dati assenti e totali invalidi mostrano **Non disponibile**.
Uno zero misurato resta zero. Le stime calcolate dal client Claude sono
identificate e possono differire dal listino dDuo o dalla fattura. I contatori
cache descrivono quantità, non quali porzioni di prompt siano state memorizzate.
Il riuso deterministico del contesto dDuo è distinto dalla cache del provider.
Vedere [Motore memoria](memory-engine.it.md).
