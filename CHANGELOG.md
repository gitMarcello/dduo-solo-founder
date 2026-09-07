# Changelog

## 0.2.0-beta.1 — Beta 1

First distributed Beta for invited developers using Codex or Claude Code.

- Chat-first configuration notices distinguish hook permission from paused
  consolidation. Setup detects rejected sleep credentials even with a saved
  login, reconnects explicitly without switching accounts, and resumes only
  after verified sign-in; queued work is not presented as completed recovery.
- Project-isolated memory, semantic retrieval and sleep consolidation, with
  observable context delivery and versioned API-equivalent cost estimates.
- Work management with Plans, Epics, Tasks, Sprints, Backlog and History.
  Sprint closure uses explicit previews and preserves task history; concurrent
  mutations are version-checked and idempotent.
- Readable Markdown, paginated Work and history, and a responsive dashboard.
- Native macOS and Windows installation, updates and rollback, with shared
  Agent Plugins packaging and client-specific lifecycle adapters.
- Independent MCP checks surface missing Codex hook authorization before Work
  operations, with an explicit per-chat choice to continue without automatic
  memory. Installation distinguishes files installed from hooks authorized;
  permission checks do not claim end-to-end capture health.
- Local Docker stacks or an authenticated Linux VPS, including first-time
  headless VPS setup, project-scoped invitations and authority transfer.
- Recovery backups and verified upgrade snapshots that preserve project data.
- English and Italian installation and operating guides, VPS-only hardware
  requirements, and a one-sentence installation prompt pinned to this release.
- The 2026-09-05 Astra and Fable 5.1 price snapshot, including cache semantics;
  historical measurements retain their original prices.

The operating-system CI matrix exercises installation, update/rollback, MCP
and hooks with synthetic account responses. Real login, native hook consent
and public VPS HTTPS are verified when configuring each actual environment.
