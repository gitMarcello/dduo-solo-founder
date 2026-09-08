[**EN · English**](pricing.md) · [IT · Italiano](pricing.it.md)

# Usage and API-equivalent prices

Observability separates paid embeddings, interactive usage and sleep.
API-equivalent estimates for subscription usage are not invoices or quota
counters and must not be added to embedding charges as an actual bill.

The release stores the following standard API price snapshot dated
**2026-09-05**, in USD per million tokens:

| Model | Uncached input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 10 | 1 | 12.50 | 50 |
| Claude Fable 5.1 | 10 | 0.25 | 12.50 (5 minutes), 20 (1 hour) | 50 |

The reference sources recorded for the snapshot are the
[OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra),
[OpenAI pricing](https://developers.openai.com/api/docs/pricing) and
[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing).
These are versioned rates used by this release, not a live pricing feed.

For Astra requests exceeding **272,000 input tokens**, the stored long-context
rule doubles input-component rates and multiplies output rates by 1.5. Do not
use the short-context table unchanged for those requests. Fable 5.1 cache reads
use 0.025 times the base input rate; the older Fable 5 entry remains separate.
Cache writes require an attributable retention duration when the provider's
rate depends on it. Unknown duration or inconsistent counters remain unpriced.

The embeddings snapshot is separate: `text-embedding-3-large` is priced at
USD 0.13 per million input tokens. The event preserves provider-reported usage,
rate and snapshot version. Existing observations retain their original price
version and are not retroactively rewritten when a model entry changes.

Unknown models, absent usage and invalid totals display **Unavailable**.
Measured zero remains zero. Claude client-computed estimates are identified as
such and can differ from dDuo's list-price estimate or a real bill. Cache-hit
counters describe quantities; they do not identify which exact prompt text was
cached. dDuo's own deterministic context reuse is a separate measurement from
the provider's prompt cache. See [memory engine](memory-engine.md).
