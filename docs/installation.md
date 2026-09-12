[**EN · English**](installation.md) · [IT · Italiano](installation.it.md)

# Installation

On native macOS and Windows, install Node.js 18+, Git and the selected client.
New CLI or VS Code installations also require that family's official standalone
Codex or Claude Code CLI for package management and durable sleep on the memory
host. An extension alone is not this prerequisite. Prepare a missing CLI only
within authorized onboarding; the dDuo installer does not install it. Existing
Codex Desktop installations can keep their selected runtime.
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

For an official VS Code integration, install the same selected adapter with
`--only codex --surface vscode` or `--only claude --surface vscode`, reload
VS Code and open a new graphical conversation. The
flag selects precise repair instructions; it does not itself certify lifecycle
delivery. Follow the [VS Code acceptance record](vscode-lifecycle-validation.md)
before promising IDE support to another user.

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
workstation use `--only codex --surface cli --no-setup` or the Claude equivalent,
then `remote-bind` or `remote-join`. Use `--surface vscode` for the official IDE
extension, or `--surface desktop` for Codex Desktop. Remote collaborators need
their chat client, not a local sleep login. The installer does not install
Docker or either standalone CLI.

## Instructions for the installing agent

Read this guide and [SECURITY.md](../SECURITY.md). Briefly explain what dDuo
adds and which prerequisites are missing, and collect one confirmation. Use a
temporary release checkout outside the user's project; install only the active
supported client with the real project root. Preserve existing memories and
bindings and select the path from the request. Select the actual client family
and surface; if the request does not identify them, ask one concise question
before proceeding. A VS Code terminal does not prove that the chat uses its
extension. Pass `--only` and `--surface` accordingly.

Reuse the managed client profile and executable on updates. For an explicitly
selected custom profile, use `--client-config-dir <ABSOLUTE-PATH>` and, when
needed, `--client-executable <ABSOLUTE-PATH>`. The scope must match the active
client's `CODEX_HOME` or `CLAUDE_CONFIG_DIR`; do not register another profile
silently. A separate `--client-probe-executable` is diagnostic only, never the
sleep runtime or proof of a graphical chat.

For local setup, reuse components that are already ready. If a local OpenAI
key exists, ask in chat whether to reuse it or enter another: only after that
choice copy the key into the new project's private settings, without sharing
memories or databases. Activate without a page when everything is ready;
otherwise open dDuo Setup only for missing actions, including new private
credentials and native hook approval. Do not ask
for secrets or terminal work in chat. Never bypass native consent. Apply the
[surface-specific handoff](#required-handoff). Verify readiness and report only the
next necessary action when something is missing. Keep explanations concise
and in the user's language.

## What Setup does

The local page belongs to the persistent dDuo host agent. It requests only
missing actions:

1. Start Docker Desktop for local memory.
2. Store the OpenAI embeddings key in the private project secret directory
   under `~/.config/dduo-solo-founder/project-secrets/` (`0700` directory,
   `0600` file permissions on POSIX).
3. Complete official Codex **or** Claude subscription login for the host's
   consolidation executor. One is enough; the first supported project chat
   establishes the preference. Native Codex hook trust remains a separate
   client decision when Codex is installed.
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
`dduo-solo-founder dashboard --tab tasks --project-root .`, using a reusable
seven-day browser link, never the permanent device bearer in the URL.
Links returned in chat provide the same direct access. See [Remote teams](remote-teams.md)
for hosting, invitations and transfer commands.

Installation uses the checked distribution's frozen dependencies. Runtime and
client registration changes roll back together on failure; database migrations
do not. Existing registered local stacks receive pre-upgrade snapshots;
restoring older software may require a verified data restore. See [Update](update.md).

Updates require an explicit request and selected revision. A remote server
cannot download or activate client code. Project memory, Work and credentials
are separate from the installed package and client-scoped `PLUGIN_DATA`.

## Required handoff

After installation or update, complete only native approval that the active
client actually offers, then use the matching handoff:

- **Codex Desktop:** fully quit and reopen the app, then open a new project chat.
  A new chat alone does not reload the package.
- **Codex or Claude Code CLI:** start a new CLI session in the same project.
- **Official Codex or Claude Code VS Code extension:** reload the VS Code window,
  then open a new graphical conversation in the same project. A terminal CLI
  session is not a graphical-chat check.

For Codex Desktop, the verified consent path is **Settings > Hooks > dDuo Solo
Founder > Review > Trust all**. In Codex CLI use `/hooks` if that version offers
native review. The Codex IDE approval path is not verified: use only an approval
UI actually offered by the extension. If none is available, report that boundary
and stop IDE acceptance; do not invent menus or approve via another surface.
Runtime readiness does not certify IDE lifecycle delivery. First project
activation without an installation/update needs only a fresh chat/session.

An empty profile prompts once for purpose, goals, principles, current state
and primary work; an invitation reuses the shared profile. Optional next steps
are: inspect known context and sources, turn a priority into a Plan or Task,
or open Work. No demo tasks, memories or mandatory tour are created.

## Normal operation

To change the display name, ask “Rename this project to …” in chat, or click the
project name in the dashboard. Only the infrastructure manager can rename
a project. The repository, project identity, memory and existing links do not
change. The server name is authoritative; the local descriptor retains its
bootstrap label and cannot undo a rename on restart or restore.

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
