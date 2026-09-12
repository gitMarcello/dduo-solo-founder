# Changelog

## 0.2.0-beta.2 — Beta 2

Reliability and access updates for existing local and VPS projects.

- Upgrade snapshots stream Docker archive bytes into private files owned by
  the installing user, verify the archive and checksum, and reject incomplete
  or replaced files. Failed snapshots preserve the installed runtime and
  attempt to restart every project stopped for the upgrade, even when cleanup fails.
- Local-to-VPS transfer checks project authority, recovery backups, container
  bridge access and HTTPS readiness before handoff. Portable backup history
  restore handles PostgreSQL 16.14 and 16.15 through standard input.
- Remote dashboard and Work links sign the browser in automatically and can
  be reused for seven days. Access stays bound to the project member and device;
  textual capture redacts link credentials and dashboard access logs omit queries.
- Sleep MCP descriptions and annotations disclose the configured memory host,
  stored conversation scope, external providers, usage and memory writes.
  They distinguish requesting or retrying consolidation from checking its status
  and retain client approval boundaries.
- Updated English and Italian release, installation, access and recovery guidance.

The automated checks include an isolated Linux rootful-Docker snapshot smoke
with an installer running as UID 1000. Synthetic installer and browser checks
do not certify real account login, native consent or the VS Code chat lifecycle;
those still require acceptance in the actual environment.

## 0.2.0-beta.1 — Beta 1

First distributed Beta for invited developers using Codex or Claude Code.

- Project-isolated memory, semantic retrieval and sleep consolidation, with
  observable context delivery and versioned API-equivalent cost estimates.
- Work management with Plans, Epics, Tasks, Sprints, Backlog and History.
  Sprint closure uses explicit previews and preserves task history; concurrent
  mutations are version-checked and idempotent.
- Readable Markdown, paginated Work and history, and a responsive dashboard.
- Native macOS and Windows installation, updates and rollback, with shared
  Agent Plugins packaging and client-specific lifecycle adapters.
- Guided setup and chat-first configuration notices, with explicit sign-in,
  native hook authorization and a choice to continue with memory paused.
- Local Docker stacks or an authenticated Linux VPS, including first-time
  headless VPS setup, project-scoped invitations and authority transfer.
- Recovery backups and verified upgrade snapshots that preserve project data.
- English and Italian installation and operating guides, VPS-only hardware
  requirements, and a one-sentence installation prompt pinned to this release.
- Versioned model pricing snapshots, including cache semantics;
  historical measurements retain their original prices.

The operating-system CI matrix exercises installation, update/rollback, MCP
and hooks with synthetic account responses. Real login, native hook consent
and public VPS HTTPS are verified when configuring each actual environment.
