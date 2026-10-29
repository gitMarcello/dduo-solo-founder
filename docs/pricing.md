[**EN · English**](pricing.md) · [IT · Italiano](pricing.it.md)

# Usage and API-equivalent prices

Observability separates paid embeddings, interactive usage and sleep.
API-equivalent estimates for subscription usage are not invoices or quota
counters and must not be added to embedding charges as an actual bill.

The catalog was checked against official OpenAI and Anthropic prices on
**2026-10-23**. GPT-6.1 Sol and Claude Opus, Sonnet and Haiku 5.5 use a new
snapshot with that date. Previously catalogued rates still match the official
prices and keep their original snapshots: GPT-6 Sol and Luna use **2026-09-22**,
Astra and Fable 5.1 use **2026-09-05**, and older models use **2026-08-29**.
The following standard API rates are in USD per million tokens:

| Model | Uncached input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 10 | 1 | 12.50 | 50 |
| GPT-6.1 Sol | 2 | 0.10 | 2.50 | 10 |
| GPT-6 Sol | 2 | 0.20 | 2.50 | 10 |
| GPT-6 Luna | 0.10 | 0.01 | 0.125 | 0.50 |
| Claude Fable 5.1 | 10 | 0.25 | 12.50 (5 minutes), 20 (1 hour) | 50 |
| Claude Opus 5.5 | 4 | 0.20 | 5 (5 minutes), 8 (1 hour) | 20 |
| Claude Sonnet 5.5 | 2 | 0.10 | 2.50 (5 minutes), 4 (1 hour) | 10 |
| Claude Haiku 5.5, up to 100K prompt tokens | 0.10 | 0.01 | 0.125 (5 minutes), 0.20 (1 hour) | 0.50 |

The reference sources recorded for the snapshot are the
[OpenAI Astra documentation](https://developers.openai.com/api/docs/models/gpt-6-astra),
[OpenAI GPT-6.1 Sol documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[OpenAI Sol documentation](https://developers.openai.com/api/docs/models/gpt-6-sol),
[OpenAI Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna),
[OpenAI pricing](https://developers.openai.com/api/docs/pricing) and
[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing).
These are versioned rates used by this release, not a live pricing feed.

For Astra, Sol and Luna requests exceeding **272,000 input tokens**, the stored
long-context rule doubles input-component rates and multiplies output rates by
1.5. Do not use the short-context table unchanged for those requests. GPT-6.1
Sol cache reads cost 5% of base input; GPT-6 Sol keeps its separate 10% rate.
OpenAI cache-write figures above use the standard 30-minute write rate.
The comparisons use Standard pricing, without Fast/Ultrafast premiums, Batch
or Flex discounts, or regional surcharges.

Claude Haiku 5.5 requests exceeding **100,000 prompt tokens** multiply every
token rate by **5** for the entire request, including output and both cache
write durations. Prompt length includes uncached input, cache reads and cache
writes; output does not count toward the threshold. Each request is priced
separately, even when a turn contains several requests. Claude Opus and Sonnet
5.5 keep standard rates throughout their 1M context window.

GPT-6.1 Sol, GPT-6 Sol, GPT-6 Luna and the Claude 5.5 models have a
128,000-token maximum output per request; larger output counts remain unpriced.
Fable 5.1 cache reads
use 0.025 times the base input rate; the older Fable 5 entry remains separate.
Cache writes require an attributable retention duration when the provider's
rate depends on it. Unknown duration or inconsistent counters remain unpriced.

The embeddings snapshot is separate: `text-embedding-3-large` is priced at
USD 0.13 per million input tokens. The event preserves provider-reported usage,
rate and snapshot version. Existing observations retain their original price
version and are not retroactively rewritten when a model entry changes.

When a past interactive or sleep sample has no stored price or price version,
the dashboard can now estimate its API equivalent from the versioned catalog.
It prices each request separately and includes the result in model, operation,
timeline and component totals. The dashboard identifies these samples and the
catalog date. This is a retrospective comparison using that catalog, not a
claim about an invoice or the rate available when the sample was collected.
The stored observation stays unchanged, including any existing price or zero.
Missing counters, unknown models and unattributable cache-write durations stay
unpriced; embedding charges do not use this fallback.

Unknown models, absent usage and invalid totals display **Unavailable**.
Measured zero remains zero. Claude client-computed estimates are identified as
such and can differ from dDuo's list-price estimate or a real bill. Cache-hit
counters describe quantities; they do not identify which exact prompt text was
cached. dDuo's own deterministic context reuse is a separate measurement from
the provider's prompt cache. See [memory engine](memory-engine.md).
