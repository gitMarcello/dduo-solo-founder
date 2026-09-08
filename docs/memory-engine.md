[**EN · English**](memory-engine.md) · [IT · Italiano](memory-engine.it.md)

# Memory engine

Technical reference for `v0.2.0-beta.1`.

The memory engine turns completed conversations into a compact, revisable
project memory. It is not a transcript RAG and it does not ask the interactive
agent to manufacture memories.

## Capture and retrieval

Each completed turn contains its ordered user-prompt fragments, final assistant
response, source artifacts, session ownership, and the union of memories
retrieved for that turn. Steering from a supported client extends the existing
turn instead of creating an orphan turn; sleep retains every prompt-event ID as
valid provenance.
SessionStart receives a bounded full Founder Brief containing:

- the current version of the project operating manual;
- the current project profile and confirmed principles;
- unfinished Tasks in the active Sprint, or unfinished unsprinted Work when
  there is no active Sprint, plus relevant active Plans;
- every qualified semantic memory that survives the shared delivery budget;
- a short, readable health state.

Later prompts receive qualified semantic memories plus only changed stable
state. An unchanged manual/profile/Work snapshot remains in the live session;
an exact Work item omitted earlier is injected once when the user explicitly
names it. Startup, clear and native compact rehydrate the full snapshot. Resume
uses a delta when its baseline is trusted and fails safe to a full snapshot
when the baseline is absent, corrupt or incompatible.

Retrieval is project-scoped and uses OpenAI only for embeddings. The embeddings
key remains in the project host's private configuration and is never passed to
the sleep CLI.

The current prompt is searched first and its raw similarity scores are never
fused with session history. A second query containing recent user prompts runs
only when the current prompt is an Italian or English elliptical continuation,
or when a successful direct search qualifies no memory. A failed direct
provider or Qdrant operation does not trigger an immediate historical retry.
Exact memory IDs and node keys among the candidates sort first. PostgreSQL then
keeps only active actionable memories, selects the latest revision per type/key
and removes duplicates.

There is no fixed output K and no per-type quota. The top-48 Qdrant request is a
technical candidate window, not a promise to emit 48 items. Delivery is bounded
instead by the 9,000-unit Founder Brief shared with the operational manual,
profile, Plans, Tasks, handoffs and health state.

Work is authoritative PostgreSQL state, separate from memories. Retrieval and
sleep never assign Tasks, start Sprints or move work; these require explicit,
versioned, idempotent operations. Completed work stays accessible without
entering the active briefing. See [Work](work.md) for Sprint closure and history.

## Model-facing memory representation

Automatic context is deterministic JSON Lines. Each memory is an independent
record containing only its ID, type, stable key, revision and authoritative
text; project ownership, source IDs, jobs, timestamps and revision provenance
remain in PostgreSQL. Duplicate IDs are removed before composition.

The complete automatic string may use at most 9,000 client-safe units, defined
as the greater of Unicode code points and UTF-16 code units. A memory is emitted
whole, emitted through its explicit excerpt, or omitted in stable priority
order. The composer never cuts a JSON value or invents a substring. An explicit
memory excerpt is marked as partial, keeps the identifying fields and carries
`{"tool":"explain_memory","memory_id":"..."}` so the agent can request the
complete text, sources and revision chain. Codex 0.150.0 or newer receives this
through its compatible `additionalContextLimit=0` hook setting; Claude keeps
its existing hook schema and receives the same sub-10k payload.

## One-call consolidation

Sleep batches at most eight turns and 40,000 characters. One structured call
to the project host's subscription executor both groups related material and
decides which durable memories to create, revise, supersede, or retire. The
schema validates every source turn, source message, referenced memory, and
artifact before PostgreSQL changes.

The first supported project session establishes a stable sleep preference:
Codex or Claude. Immediately before each pass, the memory host verifies the
preferred subscription and runs it when available. If that login or executable
is unavailable before model output, it may use the other already-verified host
subscription; it never switches silently to bypass a rate limit, timeout or
invalid output. `source_client` remains provenance and does not split the
backlog. Each job records both its preference and its actual executor, and the
resulting memory is immediately shared across both clients. There is no paid
generative API fallback or temporary handoff memory.

## Project operating manual and remote fallback

The versioned manual contains standing project rules for solo users and teams,
not generated memories or transient status. Full snapshots include it; deltas
repeat it only after a revision. If it exceeds the shared budget, an explicit
head-and-tail excerpt points to `get_project_manual`.

Publishing through `update_project_manual` requires a direct authorized request
or confirmed concise proposal. Read the current version first; the same
manager capability, optimistic concurrency and idempotency as the dashboard apply.

For remote bindings, only a manual returned by an authenticated live response
is cached on the client. The cache is a private `0600` file authenticated with
the device bearer and bound to the exact project and checkout binding. Hooks
and `get_project_manual` may use it after a network error or server 5xx, with an
explicit stale-state warning. A missing cache produces no invented manual;
authorization and upgrade failures are never bypassed. Memories and Work are
not cached, and the client never starts a local replacement authority.

## Language of new memories

Language is selected independently for every newly consolidated topic.
An explicit founder request wins; otherwise dDuo uses the latest
substantive user message for that topic. Clearly English input produces English
memory text. Italian is the default for Italian or ambiguous input, while a
revision with ambiguous input keeps the language of the memory being revised.
Assistant prose, attachment headings, artifacts, quoted text and logs/code do not
decide the language. The prompt explicitly asks for Italian by default.

The consolidator must return the structured value `it` or `en`. Language is a
preference, not a persistence gate: otherwise valid output in the other language
is saved unchanged, without a translation call or a language-only retry. The job
records `language_warnings` (the number of affected topics). Language metadata
uses the text's inferred language when clear, otherwise the model's declared
language; this is not a guarantee of language detection. Structure, content and
source validation still apply. Stable node keys and source IDs remain unchanged,
so language does not split one fact into parallel memories. The policy applies only to newly consolidated
content: existing memories and their revision history are not translated,
rewritten or backfilled.

## Scheduling

Sleep is asynchronous and is scheduled after any of these signals:

- eight pending turns;
- twenty minutes of inactivity;
- a topic boundary explicitly requested by the interactive model;
- a manual dashboard or MCP request.

The model may suggest a topic boundary, but the consolidator makes the final
semantic decision. Idle and threshold scheduling cover conversations that end
without an explicit signal.

## Typed failures and retry

Only these failure kinds leave a sleep job waiting:

| Kind | Behaviour |
| --- | --- |
| `auth_required` | Explain in chat and ask whether to reconnect or continue temporarily. Resume only after verified native sign-in; remote projects require their infrastructure manager. |
| `rate_limited` | Wait until the recorded retry window, then retry automatically. |
| `bridge_unavailable` | Exponential local retry. |
| `dependency_unavailable` | Exponential retry after a missing local dependency is repaired. |
| `invalid_model_output` | Preserve the batch and retry after validation failure. |

There is never an automatic generative API fallback, provider switch, or
interactive-chat block. PostgreSQL retains the original batch until it is
consolidated or the user explicitly removes it.

## Revisions and provenance

Memories have groups and revisions. Replacing a fact creates a new revision;
earlier revisions remain auditable. `explain_memory` returns source turns,
message IDs, artifacts, and the revision chain. Qdrant receives each validated
memory change through an idempotent outbox, so it can be rebuilt from
PostgreSQL without losing semantic history.
