[**EN · English**](README.md) · [IT · Italiano](README.it.md)

# dDuo Solo Founder

**Give Codex and Claude Code project memory inspired by human memory.**

dDuo retrieves relevant information through **embeddings** and consolidates it
during **“sleep”**: it processes conversations to retain decisions, lessons and
useful memories for future chats.

It also provides a **project dashboard**: while you work in chat, the assistant
can create and update tasks, organize plans, epics and sprints, and keep the
backlog and completed work tidy. In the same dashboard, you can explore the
memory and see how much context and usage dDuo adds.

The system runs **on your computer or your server**, with separate memory for
each project, whether you work alone or with a team. Semantic search uses
embeddings from the OpenAI API.

## Start here

### Install dDuo for this project

Open **the project you want to work on** in Codex or Claude Code and paste:

```text
Install this plugin in this project and guide me through setup:
https://github.com/gitMarcello/dduo-solo-founder/tree/v0.2.0-beta.1
```

The assistant follows this release's
[installation instructions](docs/installation.md#instructions-for-the-installing-agent)
and [security guide](SECURITY.md); no long prompt is needed.

<details>
<summary>Prefer the terminal? Installation command</summary>

Run from your project's root. Requires Node.js 18+ and Git:

```bash
dduo_install_dir="$(mktemp -d)"
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git "$dduo_install_dir"
"$dduo_install_dir/install.sh" --only codex --project-root "$PWD" --yes
# Use --only claude instead when installing for Claude Code.
```

On native Windows, use PowerShell:

```powershell
$dduoInstallDir = Join-Path ([IO.Path]::GetTempPath()) ("dduo-" + [guid]::NewGuid())
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git $dduoInstallDir
node (Join-Path $dduoInstallDir 'bin/install.mjs') --only codex --project-root (Get-Location).Path --yes
```

</details>

The assistant runs the installation and opens Setup. Complete the actions
shown there, then follow the restart instructions. See [requirements](#requirements).

### Create the first memory directly on a VPS

A new project's memory can live on the VPS from the beginning, without local
Docker. The assistant follows the [headless VPS runbook](docs/platform-support.md).

```text
Set up this project's first dDuo memory directly on a VPS and connect this
computer remotely. Preserve any existing memory, check the server requirements
and follow the procedure here. Ask only for missing details and decisions:
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/platform-support.md
```

### Move an existing dDuo project to a VPS

Paste this in the existing project's chat. Other projects keep their own setup.

```text
Move this project's existing dDuo memory to a shared VPS. Before handling
infrastructure credentials, enable and verify off-record capture. Verify a
recovery backup, keep credentials private and maintain one writable memory.
Follow this release's transfer and recovery procedures; ask only for missing
details and necessary decisions:
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/remote-teams.md
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/backup-and-recovery.md
```

### Join a project someone already shared

Use the manager's one-time invitation in place of the local installation prompt
in your authorized Git checkout. It connects to the existing memory without
local Docker or VPS credentials. Memory access does not grant Git access.

## What you get

- **Project continuity:** retrieve relevant decisions, constraints and lessons
  across chats, with sources and revision history.
- **Organized work:** discuss the goal; the assistant can propose a Plan,
  group work into Epics, plan Sprints and keep Backlog and completed history
  separate, with readable Task links.
- **Shared procedures:** keep testing, deployment and working rules in a
  versioned operating manual, useful both alone and with a team.
- **A dashboard you can inspect:** read memories, follow work, inspect delivered
  context and usage measurements, and manage encrypted recovery backups.

For example: “Pick up where we left off and suggest the next step”, “Turn this
idea into a plan” or “What have we already decided about authentication?”

## Requirements

| Scenario | Minimum |
| --- | --- |
| Shared VPS | Linux with systemd, 1 vCPU, 1 GiB RAM, 2 GiB persistent disk-backed swap, 5 GiB free in Docker storage after swap |

On macOS or native Windows, install Node.js 18+, Git and your supported client;
local memory also uses Docker Desktop. No local hardware minimum is imposed.
The installer sets up `uv`. The person hosting the memory supplies an OpenAI API key for
embeddings (semantic search) and a Codex subscription login for memory
consolidation, including when chatting through Claude. Collaborators supply
their own chat client access and use the host's memory services.

The VPS minimum is for one lightly loaded project. Every additional project
keeps its own complete stack and therefore needs additional RAM and storage.
`remote-host` verifies the live host before changing project authority.

## Documentation

- [Installation and onboarding](docs/installation.md)
- [Platform support and fresh VPS setup](docs/platform-support.md)
- [Remote projects and teams](docs/remote-teams.md)
- [Work: Sprint, Backlog and history](docs/work.md)
- [Architecture](docs/architecture.md)
- [Memory engine](docs/memory-engine.md)
- [Backup and recovery](docs/backup-and-recovery.md)
- [Client binding and updates](docs/client-binding-and-updates.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security](SECURITY.md) and [privacy](docs/privacy.md)
- [Complete guide](docs/complete-guide.md) · [Italiano](docs/guida-completa-dduo-solo-founder.md)
- [Contributing and release gates](CONTRIBUTING.md)

## Project status

[v0.2.0-beta.1](https://github.com/gitMarcello/dduo-solo-founder/releases/tag/v0.2.0-beta.1)
is a Beta for invited developers. It uses the Agent Plugins 1.0 format, with
native adapters for Codex and Claude Code on macOS and Windows, or a Linux
memory host. Codex IDE/VS Code uses the same adapter and must pass native hook
readiness; a standard manifest alone does not prove lifecycle support.
Other agent clients remain disabled. See [verification boundaries](docs/platform-support.md).

Licensed under [Apache License 2.0](LICENSE).
