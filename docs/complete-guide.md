[**EN · English**](complete-guide.md) · [IT · Italiano](guida-completa-dduo-solo-founder.md)

# dDuo Solo Founder: complete guide

Overview for `0.2.0-beta.2`. Use the linked runbooks for exact commands and
recovery procedures; use the [README](../README.md) for copyable setup prompts.

## 1. What the founder gets

dDuo gives Codex and Claude Code persistent project context, Plans, Epics,
Tasks and Sprints, plus an operating manual and an inspectable dashboard.
Every project has separate databases, vectors, credentials, Work and backups.
One project's memory can be local while another's is on a shared VPS.

Choose the path from the request: new local memory, first memory directly on
VPS, invitation to existing remote memory, or transfer of existing memory.
Never initialize a replacement for an existing project. See
[Installation](installation.md).

## 2. Setup and credentials

macOS and Windows need Node.js 18+, Git and the selected client; local memory
also needs Docker Desktop. The host supplies an OpenAI embeddings key and one
official subscription login for consolidation: Codex or Claude. The first
supported project chat establishes the preference; the host verifies it before
each pass and can use the other already-verified host subscription only when
the preferred provider is unavailable. The installer prepares `uv`, not Docker
or client CLIs.

Setup requests only missing actions and respects native hook approval. After
installing/updating Codex, fully quit and reopen it, then start a new chat;
Claude needs a new session. Activation without a client update needs only a
fresh session. An empty profile prompts for project goals and current work;
onboarding creates no demo data. Declined activation stays declined.

Credentials stay outside Git. Collaborators receive revocable project device
tokens, not server credentials. Recovery bundles intentionally contain project
secrets under encryption; keep their recovery key separate. See
[Security](../SECURITY.md) and [Privacy](privacy.md).

## 3. Isolation and runtime

Agent Plugins 1.0 supplies `plugin.json`, `skills/` and `mcp.json`. Native
Codex/Claude lifecycle adapters live under `it.dduo.client-support/`; other
clients remain unsupported. `PLUGIN_ROOT` locates the package; `PLUGIN_DATA`
is client-scoped storage, never authoritative project memory.

Each project has its own API, worker, dashboard, PostgreSQL and Qdrant.
PostgreSQL is authoritative; memory/task vector collections are derived and
recoverable. A host agent handles setup and project-scoped credentials and
subscription execution; its authenticated bridge is not publicly routed.
See [Architecture](architecture.md).

## 4. Direct VPS and shared projects

One lightly loaded project requires Linux with systemd, 1 vCPU, 1 GiB RAM,
2 GiB persistent disk-backed swap and 5 GiB free Docker storage after swap.
Root and Docker share one filesystem; without swap, reserve 7 GiB before setup.
Every additional project needs resources for another complete stack.

The [VPS runbook](platform-support.md#first-installation-directly-on-a-vps)
covers headless installation, preflight, initialization, private credentials,
HTTPS hosting and workstation binding. Only Caddy is shared between stacks.
Keep TCP 443 open for certificates plus any assigned project HTTPS port.

The infrastructure manager handles invitations, revocation, manuals, backups
and transfers. Members share memory and Work without VPS access. Invitations
are project-scoped, one-time and release-pinned; Git access is separate.
See [Remote teams](remote-teams.md).

## 5. Every conversation

SessionStart resolves the approved project binding, replays queued completed
turns and supplies a Founder Brief. UserPromptSubmit retrieves relevant
memory. Stop saves the completed turn or queues it privately for retry.

The final brief stays within 9,000 client-compatible units. It distinguishes
complete items, explicit excerpts with retrieval pointers, and omissions.
Startup, clear and native compaction receive full snapshots; ordinary turns
and trusted resumes receive changes, not repeated operating instructions.
See [Memory engine](memory-engine.md).

A remote outage never creates local replacement memory. A valid private cache
can supply only the last authenticated manual, marked stale. Revocation,
authorization and compatibility failures cannot use that fallback.

## 6. The assistant and operating manual

The assistant should use context quietly, be concise and evidence-based,
propose proportionate Work, and show human-readable links. Pure exploration
need not create tasks; unrelated initiatives need a scope decision.

The manual holds stable procedures, such as branches, review and deployment
rules. Current status belongs in Work. Only the local owner or remote manager
publishes a revision after a direct request or an accepted proposal; members
can propose changes. Compaction creates a reviewable draft, never an automatic
replacement. Its target is 4,000 characters; editing is capped at 100,000.
See [Operating manual](remote-teams.md#project-operating-manual).

## 7. Work: Sprint, Backlog and History

Current Sprint contains assigned tasks; Backlog contains unfinished tasks
without a Sprint. History contains archived Sprints, completed/cancelled
unassigned tasks and completed/superseded Plans. Epics remain project-wide.

Only one Sprint can be active. Closing requires a current preview and a
destination for unfinished tasks; immutable outcomes, versions and idempotency
protect history and concurrent edits. Reopening does not undo previous moves.
Existing tasks are not assigned to invented historical Sprints. Known items
open directly; lists and search expose pagination. See [Work](work.md).

## 8. Memory and sleep

Retrieval qualifies memories against the current request and validates active
revisions in PostgreSQL. Historical queries help with elliptical requests or
an empty successful search; there is no fixed quota of memories to emit.

One project-owned automatic executor prefers the client established by the
first supported project chat. Codex uses `gpt-5.6-terra` with medium reasoning.
Codex and Claude turns share one queue while retaining their source client.
Batches contain at most eight turns and 40,000 characters. Eight pending turns,
twenty minutes of inactivity, a topic change or a manual request can trigger
consolidation. There is no generative API fallback: the other host subscription
is considered only before model output when the preferred login or executable is
unavailable, never to bypass a rate limit. Rate limits retry; authentication
failures need one host login. See
[Troubleshooting](troubleshooting.md).

## 9. Observability and costs

Context delivery, embeddings, interactive usage, sleep and reliability are
separate categories. Exact emitted dDuo context can be inspected with its
provenance and size breakdown; it is not the model's entire prompt or proof
that the client consumed every emitted character.

Input, cache read/write, output and reasoning counters remain provider/model
specific. Missing values stay unavailable; measured zero stays zero. API
equivalent is a versioned estimate, not a subscription invoice. Numeric client
telemetry does not archive transcripts. Team filters attribute operations,
not separate memories. See [Pricing](pricing.md).

## 10. Update, backup and transfer

Updates install a user-selected revision with verified checksums and locked
dependencies. Runtime/client registration rollback does not undo database
migrations. Verified encrypted backups and local volume snapshots protect
updates; local snapshots do not replace off-device recovery. See
[Update](update.md).

Full Recovery Bundle v2 includes PostgreSQL, optional Qdrant snapshots,
project credentials, pending state and backup history. It excludes source
checkout, plugin installation, SSH, OS keychains, arbitrary home files,
logs, Docker images and its own recovery key. Restore validates the archive
before replacing data. See [Backup and recovery](backup-and-recovery.md).

An authority transfer freezes the source, restores a read-only destination,
verifies readiness, then retires the source before activating the destination.
Never cancel after source finalization. Receipt checks prevent accidental
split-brain between trusted hosts, not a malicious host holding the shared
secret. Follow the exact [transfer sequence](remote-teams.md#move-the-authoritative-memory).

## 11. Verification and limits

CI covers backend, frontend, browser, Compose, distribution and native
installation/update/rollback, MCP and hooks. Synthetic authentication tests
do not prove real account consent, external VPS TLS or provider quotas.
Verify actual readiness and report untested steps. See
[Platform support](platform-support.md) and [Contributing](../CONTRIBUTING.md).

Outages or provider limits can delay memory; retry storage is bounded.
Uninstalling the plugin and deleting project data are separate operations:
see [Uninstall](uninstall.md).
