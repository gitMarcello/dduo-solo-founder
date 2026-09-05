[**EN · English**](platform-support.md) · [IT · Italiano](platform-support.it.md)

# Platform support and first VPS installation

Release `0.2.0-beta.1` supports Codex and Claude Code on native macOS and
Windows, with local memory or a Linux VPS. Workstations need Node.js 18+, Git
and the client; only local memory needs Docker Desktop. The server needs
Docker/Compose, Git, Node.js 18+ and Codex CLI 0.150.0+ for consolidation.

## Windows

PowerShell can invoke the same installer directly:

```powershell
node "<DDUO-RELEASE-DIRECTORY>/bin/install.mjs" --only codex --project-root "<PROJECT-DIRECTORY>" --yes
```

Use `--only claude` for Claude Code. For a remote workstation add
`--no-setup`, then bind or join the project. The PowerShell entry point
`install.ps1` delegates to this installer. Runtime entry points use the native
`Scripts` directory and `.exe` files, wrapper commands use `.cmd`, and Codex
lifecycle hooks run through Node. Native Windows does not require a POSIX
`/bin/sh` hook runner. WSL is a separate Linux environment.

The OS CI matrix covers locked installation, update/rollback, MCP and hooks
with synthetic authentication and sleep. It cannot prove real subscriptions
or native consent. Setup verifies the actual client version, login, hook
authorization, Docker or remote endpoint; report untested paths accurately.

## First installation directly on a VPS

Use this for a project whose first memory will live on the VPS. First inspect
the authorized workstation and server checkouts for an existing binding and
memory. Preserve any existing project; use its invitation or the
[authority-transfer procedure](remote-teams.md#move-the-authoritative-memory)
when appropriate. A repository alone is not evidence that a memory already
exists, and copying its configured descriptor is not a fresh initialization.

The active agent performs the steps below under the manager's installation
and host-access authorization. Ordinary members do not provision the server.
Use the [README prompt](../README.md) for the short user-facing request.

### 1. Prepare the host

Use a persistent Linux account with systemd user services, Docker/Compose
access, Git, Node.js 18+ and Codex CLI 0.150.0+. The dDuo installer installs
`uv`; it does not install Docker, Node.js, Git or the Codex CLI.

Any VPS provider is acceptable. For one lightly loaded project the minimum is
1 vCPU, 1 GiB physical RAM, 2 GiB persistent disk-backed swap and 5 GiB free in
Docker storage after swap. Root and Docker must share one filesystem. A host
with no swap needs 7 GiB free before preparing the 2 GiB swap. The agent
configures missing swap and verifies its active state and persistence. Each
additional project needs additional resources for its own full stack.

Enable linger for the service account, when absent, using the administrative
`loginctl enable-linger <VPS-USER>` operation, then verify that account's
`systemctl --user status`. Preserve SSH access while preparing the firewall.
Expose only SSH and the required project HTTPS ports; do not expose PostgreSQL,
Qdrant, the internal API or the host bridge.

### 2. Install only the server runtime

Run from the authorized project checkout on the VPS:

```bash
dduo_install_dir="$(mktemp -d)"
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git "$dduo_install_dir"
node "$dduo_install_dir/bin/install.mjs" --headless --project-root "$PWD" --yes
```

`--headless` or `--only core` installs the runtime without adapters or browser
Setup. Add the reported executable directory to PATH if needed. Keep the
temporary release checkout separate from the project.

Run the read-only prerequisite check:

```bash
dduo-solo-founder remote-preflight
```

It checks Linux/systemd, linger, Docker, RAM, CPU, live disk-backed swap and
Docker free space. It creates no project and does not configure swap itself.
Resolve any failure before initialization; successful preflight is not project
or HTTPS readiness.

### 3. Create identity and privately configure credentials

Run on the VPS project checkout:

```bash
dduo-solo-founder init --headless --yes --project-root .
```

For a fresh project without an embeddings key, this creates its identity and
private secret store, reports `setup_required` with the project ID, and exits
with code `5`. This is an expected intermediate state. It opens no browser
and has not completed database initialization. Do not hide unrelated errors or
claim the project is ready.

Continue through private input and the official device login:

```bash
dduo-solo-founder configure-openai --project-root .
dduo-solo-founder login-codex --device-auth --project-root .
dduo-solo-founder init --headless --yes --project-root .
```

The first command prompts without echo for the embeddings key and stores it
outside Git. The second logs in to the project's private file-backed Codex
home; the manager completes the official authorization shown by that process.
Keep the key, device-login codes and authentication output out of chat and
shared logs. A new project has no dDuo session to mark off-record: use private
input, not an invented session ID. Existing sessions handling infrastructure
credentials use the verified off-record procedure in
[remote teams](remote-teams.md#host-a-project-on-a-vps).

The final `init` verifies credentials, builds the isolated VPS stack and creates
its Project row. “Local services” means local to this VPS, not the workstation.
`start` resumes a stack; it cannot replace initialization.

### 4. Host and verify HTTPS

Capture the following command's output privately because first bootstrap
prints the initial manager token:

```bash
dduo-solo-founder remote-host --project-root . --public-ip <PUBLIC-IP> --owner-name "<DISPLAY-NAME>"
```

Optionally supply `--acme-email <EMAIL>`. Hosting repeats its own prerequisite
and Codex login checks, installs and verifies the persistent user agent, secures
the database and services, claims this node and creates the first manager.
Follow the printed firewall instructions: TCP 443 remains open for certificate
issuance and renewal, plus the assigned project HTTPS port if different.
Further projects normally receive a stable port between 24443 and 25442.

Verify actual public HTTPS health and authenticated dashboard access. A
`remote-host` result alone does not prove that external firewalls, certificate
issuance and routing all work. Verify service persistence after a controlled
restart when authorized. Configure, create and verify a
[Full Recovery Bundle](backup-and-recovery.md), with the recovery key separately
recoverable. A fresh VPS project needs no transfer or retirement receipt.

### 5. Bind the workstation

Install the same selected release from a separate temporary checkout on macOS
or Windows, using `--only codex --no-setup` or
`--only claude --no-setup` and the actual workstation project root. Then run:

```bash
dduo-solo-founder remote-bind --project-root . --project-id <PROJECT-ID> --name "<PROJECT-NAME>" --api-url <HTTPS-API-URL> --dashboard-url <HTTPS-DASHBOARD-URL>
dduo-solo-founder dashboard --tab tasks --project-root .
```

Supply the initial manager token through the command's private prompt, never
an argument, chat message or Git file. A fresh binding needs neither
`--replace-existing` nor `remote-rebind`. Existing bindings must be preserved
and handled through their appropriate join or transfer path.

Verify project identity, authenticated memory status, Work, dashboard and
backup. Remote workstations need no local Docker or server Codex login.
After installing/updating Codex, fully quit and reopen the app and open a new
chat in this project; Claude requires a new session. Verify any actual native
consent still required. Create collaborator invitations from the
[Team workflow](remote-teams.md#invite-a-project-member).
