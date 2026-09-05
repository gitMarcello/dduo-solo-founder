[**EN · English**](privacy.md) · [IT · Italiano](privacy.it.md)

# Privacy and data handling

dDuo is designed for trusted agents and members working within one project.
Project content may include internal decisions, contacts and material supplied
in chat. **Do not put credentials in chat, Git or logs:** use private host
input and a separately recoverable password manager.

## Where data lives

Each project owns its Docker stack, PostgreSQL, Qdrant, secrets and volumes,
locally or on its chosen VPS. Several projects may share a VPS and its HTTPS
gateway, but not memory stores. There is no cross-project search.

The plugin package contains code. Neither `PLUGIN_ROOT` nor client-owned
`PLUGIN_DATA` is authoritative project storage. The same project state serves
Codex and Claude; other clients are unsupported in this Beta.

Private host files use user-only permissions. Their standard locations are:

| Location under `~/.config/dduo-solo-founder/` | Contents |
| --- | --- |
| `project-secrets/` | Project-scoped provider and runtime credentials |
| `projects.json` | Project UUIDs, canonical local paths and reserved ports; no chat or credentials |
| `bridge/` | Ephemeral host control token |
| `manual-cache/` | Authenticated remote manual cache |
| `client-telemetry/` | Workstation-local Claude status-line restore metadata |
| `backup-keys/<project-id>.key` | Project recovery key, never included in its archive |

Client credentials and pending hook/MCP state also remain outside Git.
Project files, caches and spools are private, not automatically encrypted at
rest. Protect the host account and its storage.

## What leaves the host

OpenAI `text-embedding-3-large` receives consolidated memory text and semantic
search queries. The separate task index sends title, objective, next action,
description and labels, but not completion evidence, attachments, revisions
or activity. Exact task ID/title lookup and lists require no embedding.
Task creation and updates commit without a synchronous embedding call, but
queue the projection; the worker can later send changed semantic fields to
the configured provider. An explicitly configured local provider keeps that text on the host;
the provider never switches automatically.

Sleep sends pending turns, relevant memories and selected artifact text through
the host's Codex subscription executor, in a private project `CODEX_HOME`.
Source-client identity is provenance, not a second provider selection.
Sleep has no repository tools, browser or persistent model session, and no
paid generative API fallback. Provider data-handling terms still apply.
The project's OpenAI key is not passed to the sleep CLI.

## Capture and off-record mode

Completed turns and native compaction summaries are source records, not
automatically trusted consolidated memories. Off-record turns remain in raw
project audit records and encrypted backups, but are excluded from sleep and
future turn-history retrieval. **Off record is not masking or deletion.**

For an initialized session containing voluntarily supplied infrastructure
credentials, the agent marks that exact session off record, does not copy
credentials into Work, manual, artifacts or memory, and restores capture for
future turns after the operation. Enabling it excludes the already-open turn;
disabling it never re-enables that turn.

An unconfigured project has no session to mark off record. Use hidden
`configure-openai` input and official `login-codex --device-auth`, keeping
login codes and initial manager tokens private. Installer `--headless` and
`--no-setup` change setup behavior, not data retention.

Outage spools preserve the off-record value at capture and replay it
idempotently. Older entries without a provable privacy value replay off record.
Spools may contain full pending turns and exact delivered context.

## Remote access and cached manual

Repository descriptors hold project identity and canonical HTTPS endpoints,
not credentials. Device bearers live in private files; the server stores only
their hashes. One-time invitations contain the plugin repository, project ID,
endpoints and an enrollment code, never VPS/SSH/provider credentials.
Dashboard access exchanges the bearer for a short-lived ticket and secure
project cookie; do not put permanent tokens in URLs.

Collaborators do not receive the host's OpenAI or Codex credentials through
ordinary access. The private host bridge rejects peers outside loopback and
container-private networks and is never exposed by the public gateway.

Only an authenticated live response can seed the manual cache. It contains
text and metadata, HMAC-authenticated with the device bearer and bound to the
exact project/checkout. It is not separately encrypted. Network or server 5xx
failures may use it with a stale warning; authorization or compatibility
errors may not. There is no local fallback memory or cached current Work.

## Observability

Metrics stay in project PostgreSQL; there is no remote metrics service or
tracker. Numeric events contain counts, durations, provider/model usage,
approved metadata and versioned prices. Embedding costs concern actual API
calls; interactive and sleep API equivalents are hypothetical subscription-usage
comparisons, not invoices or quota counters. Unknown usage/pricing is
**Unavailable**, not zero; categories are never combined into a bill.
See [pricing](pricing.md).

Claude's optional status-line adapter preserves existing output and collects
only attributable numeric usage. Codex parses the client-provided transcript
through an isolated adapter, retaining only IDs, timestamps, models and
numeric counters. Its private retry state may retain the transcript locator
and byte cursor, but does not copy transcript text or write its path to
PostgreSQL telemetry. Neither adapter blocks prompts.

Context inspection is different: it intentionally retains the exact hook/MCP
string, hash and component/version metadata. It is unmasked project content,
visible to all authorized project members, not just its author. Raw CLI errors,
authentication keys and unrelated paths are not telemetry fields. Treat
deliberately supplied project content as potentially sensitive regardless.

Events and exact payloads live in PostgreSQL and encrypted backups without
historical backfill. Member attribution is causal; background or older records
may show **System / legacy**. It is not a per-member privacy partition.

## Backups and deletion boundaries

Encrypted Full Recovery Bundle v2 includes database records, raw turns,
memories, contacts, Work/Sprints/immutable closure history, Plans and artifacts.
Attachments are checksum-deduplicated and limited to 10 MiB each. It also
includes allowed durable project credentials, file-backed Codex `auth.json`
when available, an applicable remote device credential, and project hook/MCP
queues. This intentionally restores the dDuo-owned runtime, not just its data.

It excludes arbitrary home files, `~/.ssh`, unrelated `.env` files, OS
keychains, source repositories, Docker images, logs, PIDs, ephemeral bridge
tokens, plugin installations and client registrations. Manual and telemetry
adapter caches are not exported. Native client approvals must be renewed.

Each archive uses a distinct project AES-256-GCM recovery key stored
separately. Anyone holding both archive and key can decrypt the whole project
and use copied provider credentials until revoked. Keep verified archives
off-device and keys separately recoverable; test recovery before relying on it.

PostgreSQL is authoritative. Qdrant indexes are reconciled after restore and
do not change task placement or Sprint history. Sprint, Backlog and History
are views, not privacy boundaries. The manager selects the backup destination;
external synchronization and retention depend on that destination.

See [Backup and recovery](backup-and-recovery.md) for verification, restore
and removal safeguards, and [Security](../SECURITY.md) for trust boundaries.
