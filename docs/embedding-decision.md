[**EN · English**](embedding-decision.md) · [IT · Italiano](embedding-decision.it.md)

# Embedding model decision

<a id="initial-benchmark-conclusion"></a>
## Evaluation scope

The distributed benchmark uses fictional Italian and English examples.
It exercises the evaluation pipeline; it is not evidence of real-project
retrieval quality. No measurements from private projects or personal devices
are distributed, and no model-performance claim is made from this fixture.

<a id="v1-decision"></a>
## Current choice

OpenAI `text-embedding-3-large` is the default and reference for future
evaluations. It provides one ready-to-use embedding path without installing
local model dependencies. MiniLM and E5-large remain explicit experimental
alternatives, not automatic fallbacks or proven equivalents.

The same configured provider supports memory retrieval and the separate Task
projection. PostgreSQL remains authoritative for memory revisions, provenance,
Task status and Sprint placement; Qdrant is rebuildable derived state. Exact
Task IDs and unique titles resolve without an embedding request. A semantic
Task lookup falls back to bounded PostgreSQL lexical search when the provider
or projection is unavailable; see [Task semantic projection](architecture.md#task-semantic-projection).

Embedding requests use the project host's privately stored API key and have
attributable API cost. Subscription-based Codex or Claude sleep uses separate credentials;
interactive and sleep API-equivalent estimates, including cached-token values,
do not measure subscription spending or remaining subscription allowance.

The default installer omits FastEmbed and ONNX. Local-provider experiments
require the `local` or `benchmark` dependency extra.

A replacement evaluation begins after at least 500-1,000 consolidated memories
and 100-200 labelled real-world queries are available. A local model must reach
at least 95-98% of the OpenAI baseline and introduce no regressions on critical
queries. Changing provider/model increments `EMBEDDING_INDEX_VERSION` and runs
`dduo-solo-founder reindex`, leaving the previous collection intact for rollback.

Use public or synthetic, labelled queries and hard negatives for reproducible
tests; do not commit private project exports or machine-specific reports.
