[EN · English](embedding-decision.md) · [**IT · Italiano**](embedding-decision.it.md)

# Scelta del modello di embedding

<a id="conclusione-del-benchmark-iniziale"></a>
<a id="initial-benchmark-conclusion"></a>
## Ambito della valutazione

Il benchmark distribuito usa esempi fittizi italiani e inglesi. Verifica la
procedura di valutazione, non dimostra la qualità del recupero su progetti
reali. Non vengono distribuite misurazioni di progetti privati o dispositivi
personali, né dichiarate prestazioni dei modelli sulla base di questa fixture.

<a id="decisione-v1"></a>
<a id="v1-decision"></a>
## Scelta attuale

OpenAI `text-embedding-3-large` è il valore predefinito e il riferimento per
valutazioni future: offre un percorso pronto senza installare dipendenze per
modelli locali. MiniLM ed E5-large restano alternative sperimentali esplicite,
non ripieghi automatici o equivalenti dimostrati.

Il provider configurato serve il recupero delle memorie e la proiezione
separata dei Task. PostgreSQL rimane autorevole per revisioni, provenienza,
stato dei Task e appartenenza agli Sprint; Qdrant è un indice derivato
ricostruibile. ID esatti e titoli univoci dei Task non richiedono embedding.
Quando provider o proiezione non sono disponibili, la ricerca semantica dei
Task usa una ricerca lessicale limitata in PostgreSQL. Vedi
[Proiezione semantica dei Task](architecture.it.md#task-semantic-projection).

Le richieste di embedding usano la chiave API privata dell'host di progetto e
producono un costo API attribuibile. Sleep usa l'abbonamento Codex e credenziali
separate. Le stime equivalenti API di chat e sleep, inclusi i valori dei token
in cache, non misurano la spesa o la quota residua dell'abbonamento.

L'installer predefinito omette FastEmbed e ONNX. Gli esperimenti con provider
locali richiedono l'extra `local` o `benchmark`.

La valutazione di un sostituto parte quando sono disponibili almeno 500–1.000
memorie consolidate e 100–200 query reali etichettate. Un modello locale deve
raggiungere almeno il 95–98% del riferimento OpenAI senza regressioni sulle
query critiche. Cambiare provider o modello richiede di incrementare
`EMBEDDING_INDEX_VERSION` ed eseguire `dduo-solo-founder reindex`, conservando la
raccolta precedente per un eventuale rollback.

Usare query etichettate pubbliche o sintetiche e negativi difficili per prove
riproducibili; non committare esportazioni private o report specifici dei dispositivi.
