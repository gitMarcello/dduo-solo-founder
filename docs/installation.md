[**EN · English**](installation.md) · [IT · Italiano](installation.it.md)

# Installation

On native macOS and Windows, install Node.js 18+, Git and Codex or Claude Code.
Docker Desktop is required only for local memory. After one installation
confirmation, the agent performs setup; remote collaborators need no local
Docker. See [platform support](platform-support.md#windows) for verification boundaries.

A VPS needs Linux with systemd, 1 GiB physical RAM, 1 vCPU, 2 GiB persistent
disk-backed swap and 5 GiB free Docker storage after swap. Root and Docker
share one filesystem; without swap, reserve 7 GiB before setup. The authorized
agent prepares missing swap; preflight and hosting verify resources before
changing authority. Follow the [VPS runbook](platform-support.md).

The Agent Plugins 1.0 package shares `plugin.json`, `skills/` and `mcp.json`.
The installer adds the selected native adapter from `it.dduo.client-support/`.
Only Codex and Claude Code are supported; discovering the manifest does not
enable other clients.

## Select the path from the request

There is no separate onboarding wizard or global local/remote preference:

- **New local project:** use the [README prompt](../README.md#install-dduo-for-this-project)
  and complete local Setup.
- **New project directly on a VPS:** create its first memory there, then bind
  the workstation remotely. Follow the [headless VPS sequence](platform-support.md#first-installation-directly-on-a-vps).
- **Existing remote project:** use the manager's invitation in the authorized
  Git checkout, instead of activating local memory.
- **Existing project moving to a VPS:** follow the two-phase
  [authority transfer](remote-teams.md#move-the-authoritative-memory).
  First local→VPS binding uses `remote-bind` with the new manager token;
  already remote workstations use `remote-rebind` with existing device tokens.

## Start with the canonical prompt

Use the release-pinned prompts in [README.md](../README.md) or
[README.it.md](../README.it.md). These are the single copyable source.

Node.js 18+ and Git are required even for remote collaborators. The installer
prepares `uv`, verifies the package and installs the selected adapter. On the
VPS use `node bin/install.mjs --headless --project-root <SERVER-CHECKOUT> --yes`
for the runtime without client adapters or browser Setup. On a remote
workstation use `--only codex --no-setup` or `--only claude --no-setup`, then
`remote-bind` or `remote-join`. The installer does not install Docker or Codex CLI.

## Instructions for the installing agent

Read this guide and [SECURITY.md](../SECURITY.md). Briefly explain what dDuo
adds and which prerequisites are missing, and collect one confirmation. Use a
temporary release checkout outside the user's project; install only the active
supported client with the real project root. Preserve existing memories and
bindings and select the path from the request.

For local setup, open dDuo Setup and guide the user only through required
actions, including private credentials and native hook approval. Do not ask
for secrets or terminal work in chat. Never bypass native consent. After
installing/updating Codex, ask for a complete app restart and a new chat in
this folder; Claude needs a new session. Verify readiness and report only the
next necessary action when something is missing. Keep explanations concise
and in the user's language.

## What Setup does

The local page belongs to the persistent dDuo host agent. It requests only
missing actions:

1. Start Docker Desktop for local memory.
2. Store the OpenAI embeddings key in the private project secret directory
   under `~/.config/dduo-solo-founder/project-secrets/` (`0700` directory,
   `0600` file permissions on POSIX).
3. Complete official Codex login for the host's consolidation executor.
   Native Codex hook trust is a separate client decision.
4. Optionally install Claude usage telemetry.
5. Activate the folder and open Work.

Credentials entered through private Setup are stored outside Git and shared
project content. This does not redact secrets pasted into chat or Work; see
[Privacy](privacy.md). Optional Claude telemetry preserves the existing status line and
invokes it with the original payload. It records attributable numbers, not
transcripts, and never blocks prompts or changes conversation lifecycle.

## Join an existing remote project

The manager creates an invitation from **Team** or with
`dduo-solo-founder team-invite --display-name "<NAME>" --language en`.
The dashboard uses the selected EN/IT language. The prompt pins the release,
identifies one project and HTTPS endpoint, and contains a one-time code,
never VPS or provider credentials. Its encoded descriptor keeps names and
endpoints as data rather than shell syntax.

The agent runs it in the authorized Git worktree. `remote-join` creates a
private device token and remote binding without Docker. A local descriptor is
replaced only for the exact same project; other projects and existing remote
endpoints remain untouched. Git access must be granted separately.

The authenticated manual is cached for outages; a cache failure warns without
undoing the join. The agent opens the dashboard through
`dduo-solo-founder dashboard --tab tasks --project-root .`, using a one-time
browser ticket, not a bearer in the URL. See [Remote teams](remote-teams.md)
for hosting, invitations and transfer commands.

Installation uses the checked distribution's frozen dependencies. Runtime and
client registration changes roll back together on failure; database migrations
do not. Existing registered local stacks receive pre-upgrade snapshots;
restoring older software may require a verified data restore. See [Update](update.md).

Updates require an explicit request and selected revision. A remote server
cannot download or activate client code. Project memory, Work and credentials
are separate from the installed package and client-scoped `PLUGIN_DATA`.

## Required handoff

After Codex installation/update, fully quit and reopen the app, complete any
native hook review and open a new project chat. A new chat alone is insufficient.
Claude needs a new session. First project activation without a client update
needs only a fresh chat/session.

An empty profile prompts once for purpose, goals, principles, current state
and primary work; an invitation reuses the shared profile. Optional next steps
are: inspect known context and sources, turn a priority into a Plan or Task,
or open Work. No demo tasks, memories or mandatory tour are created.

## Normal operation

SessionStart starts the isolated local stack or contacts the approved remote
authority; prompts retrieve context and Stop saves completed turns. Work links
include project identity. Ordinary use needs no terminal operations.

During network/server outages the client can use its last authenticated manual,
cached privately for that project, checkout and device. Current memory and
Work may be unavailable; no local replacement stack starts. Authorization,
revocation or required-update failures do not use this fallback.

For explicit diagnosis or repair use `setup`, `doctor`, `status` and
`memory-status`. Claude telemetry can be repaired with local `setup` and stays
on each collaborator's workstation.
