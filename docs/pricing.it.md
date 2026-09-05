[EN · English](pricing.md) · [**IT · Italiano**](pricing.it.md)

# Utilizzo e prezzi API equivalenti

L'osservabilità separa embedding a pagamento, uso interattivo e sleep.
Le stime API equivalenti dell'uso in abbonamento non sono fatture o contatori
di quota e non vanno sommate agli embedding come addebiti reali.

La release conserva questo snapshot di prezzi API standard del
**5 settembre 2026**, in USD per milione di token:

| Modello | Input non in cache | Lettura cache | Scrittura cache | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 10 | 1 | 12,50 | 50 |
| Claude Fable 5.1 | 10 | 0,25 | 12,50 (5 minuti), 20 (1 ora) | 50 |

Le fonti di riferimento dello snapshot sono
[documentazione modello OpenAI](https://developers.openai.com/api/docs/models/gpt-6-astra),
[prezzi OpenAI](https://developers.openai.com/api/docs/pricing) e
[prezzi Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).
Sono tariffe versionate usate dalla release, non un listino aggiornato in tempo
reale.

Per richieste Astra oltre **272.000 token di input**, la regola conservata
raddoppia le tariffe dei componenti input e moltiplica quelle output per 1,5.
Non applicare invariata la tabella del contesto breve a queste richieste.
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
