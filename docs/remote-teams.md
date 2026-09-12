[**EN · English**](remote-teams.md) · [IT · Italiano](remote-teams.it.md)

# Remote projects and teams

Each local or remote project keeps its own PostgreSQL, Qdrant, API, worker,
dashboard and credentials. A VPS may host multiple isolated stacks; only Caddy
is shared, terminating HTTPS and routing to each project's loopback dashboard.

The first project registered on a VPS normally receives HTTPS port `443`.
Further projects receive a stable free port in the range `24443`-`25442`.
`remote-host` prints every port that must be allowed through the firewall.
TCP `443` must remain open permanently for the TLS-ALPN ACME challenge and
certificate renewal; when a project uses an additional port, that assigned
port must be open as well. Caddy keeps a minimal challenge listener on `443`
even if the project originally assigned to `443` is later removed. A public
IPv4 or IPv6 address is required. Plain HTTP remote bindings are rejected.

Both manager and members use the same Agent Plugins 1.0 package with a supported
Codex or Claude Code adapter. Invitations bind one project, never a new local
memory. The package and client-scoped `PLUGIN_DATA` stay on the workstation;
shared memory stays on the VPS.

## One authoritative binding per checkout

Each repository checkout has exactly one binding in
`.dduo-solo-founder/project.toml`:

- `local` contacts only that project's reserved loopback ports and may start its
  isolated Docker stack;
- `remote` contacts only the declared HTTPS API and dashboard and never starts
  or falls back to local Docker.

This decision is project-specific: one project can use a VPS while another
stays local on the same computer. A remote descriptor contains no secret. The client stores
its project/device token outside the repository in a private `0600` file and
approves the exact repository root, project ID and endpoint fingerprint on that
machine.

If the remote service is unavailable, work is not blocked and dDuo does not
create a second memory authority. Pending completed turns remain in the
project-scoped private client spool for idempotent replay when the authoritative
endpoint returns.

The client also keeps the last operational manual received from an authenticated
remote response under `~/.config/dduo-solo-founder/manual-cache/`. The private
`0600` file is HMAC-authenticated with the device bearer and scoped by project
and binding ID, so an edited or cross-binding copy is rejected. During a network
error or server 5xx, SessionStart and UserPromptSubmit can inject that manual
with an explicit warning that current memory and Work are unavailable or stale;
`get_project_manual` can return the same copy marked `last_verified_cache`.
Authorization, revocation and required-client-update failures never fall back
to it. The cache contains no memory or Work replica and cannot become an
authority.

## Host a project on a VPS

A project's first memory can be created on the VPS itself. It does not require
a previous memory on a developer's computer or an authority-transfer archive.
Follow the [direct VPS installation
runbook](platform-support.md#first-installation-directly-on-a-vps): install
with `--headless`, pass `remote-preflight`, initialize with `init --headless`,
configure private credentials and resume initialization before `remote-host`.
`remote-preflight` creates no project; `remote-host` expects the initialized or
restored project database. An existing memory uses the transfer procedure below.

Any VPS provider is acceptable. The minimum for one lightly loaded project is
Linux with systemd, 1 GiB physical RAM, 1 vCPU, 2 GiB persistent disk-backed
swap and 5 GiB free Docker storage after swap. Root and Docker share one
filesystem; without swap, reserve 7 GiB before setup. The authorized agent
prepares missing swap; `remote-host` independently verifies resources before
changing authority. Additional projects need additional stacks and resources.
Project members do not operate the VPS.

Run this on the VPS checkout after its project data has been initialized or
restored:

```bash
dduo-solo-founder remote-host \
  --public-ip <PUBLIC-IP> \
  --owner-name "<DISPLAY-NAME>" \
  --acme-email <ACME-EMAIL>
```

`--acme-email` is optional. The command:

1. verifies Linux/systemd, CPU, physical RAM, active disk-backed swap and free
   Docker storage before changing secrets, databases or project authority;
2. imports the project's private file-backed Codex login when available and
   verifies the server-side subscription before changing deployment state;
3. installs one host-wide `systemd --user` dDuo agent, enables it at boot and
   verifies authenticated readiness before changing the project or database;
4. creates the remote authentication, session and node-authority secrets;
5. preserves the project's existing isolated stack and rotates its internal
   PostgreSQL password before remote services adopt the new secret;
6. enables authenticated remote mode for API and worker;
7. claims this VPS as the authoritative memory node, or emits a read-only
   readiness receipt when completing a prepared transfer;
8. bootstraps the first **Gestore dell'infrastruttura** when necessary and only
   after the authority is writable;
9. registers the project with the shared Caddy gateway and prints its HTTPS
   API, dashboard, assigned port and firewall instruction for permanent `443`
   access plus the assigned project port when different. For a prepared
   transfer, it verifies the public TLS certificate and the complete HTTPS
   route to that exact read-only project before reporting `https_verified`.

The first manager need not exist on a restored local project. Its host reads
`POST /projects/{id}/authority/status` using the project-scoped node-authority
secret before attempting activation. This narrowly scoped control-plane check
does not register a team member, grant ordinary API access or make the
destination writable; manager bootstrap still waits for transfer completion.

The VPS account must support persistent user services. If `remote-host` asks
for linger, run the printed one-time command as an administrator, for example
`sudo loginctl enable-linger <VPS-USER>`, verify `systemctl --user status` from
that account, and rerun `remote-host`. The command refuses to promote the
project when this prerequisite is missing. The private bridge token and dynamic
port live only in a `0600` environment file; neither is embedded in the unit or
its process arguments. `bridge-stop` stops the unit without disabling boot, and
the next dDuo start reactivates it.

On first bootstrap it also prints one initial manager device token once. A
private pending record is written before the server mutation and removed only
after the token is printed, so a crash or gateway failure can safely replay the
same token on the next `remote-host` run. Keep that token out of Git and bind the
manager's workstation with:

```bash
dduo-solo-founder remote-bind \
  --project-id <PROJECT-ID> \
  --name "<PROJECT-NAME>" \
  --api-url <HTTPS-API-URL> \
  --dashboard-url <HTTPS-DASHBOARD-URL>
```

Supply the token through private input, never shell arguments or chat. Use
`--replace-existing` only after a deliberate authority move; a fresh binding
does not need it.

Remote memory consolidation runs on the server-side host bridge. The first
supported project chat establishes a Codex or Claude preference. Before every
pass, the VPS checks that preferred project-scoped subscription; if it is
unavailable before model output, it can use the other already-verified VPS
subscription. It never switches automatically for rate limits, timeouts or
invalid output. The collaborator does not receive the VPS or provider
credentials. Sleep runs ephemerally with shell/exec tools disabled, web search
disabled and no project checkout, so untrusted memory text cannot turn the
consolidation pass into a local credential-reading agent. If neither host login
is available, the Gestore dell'infrastruttura must authenticate Codex or Claude
again on the VPS.

If the Gestore dell'infrastruttura supplies infrastructure credentials directly
in an authorized project chat, the Founder Brief gives the agent the exact
current dDuo session ID. The agent immediately enables off-record capture for
that session, performs the credential operation, and disables it when finished.
The already-open credential turn remains excluded from sleep and future
turn-history retrieval; the credential must never be copied into the shared
manual, Work, artifacts, semantic memory or observability.

A fresh project has no dDuo session to mark off-record. Use private credential
input and the official `login-codex --device-auth` or `login-claude` flow on its host; never
invent a session ID or claim off-record protection before it exists. The agent
keeps login codes and the initial manager token out of chat transcripts and
shared content, and handles binding through a private input channel.

## Invite a project member

The two neutral project capabilities are:

- **Gestore dell'infrastruttura**: manages invitations and revocation, the
  shared manual, backup and authority-transfer operations;
- **Membro del progetto**: works with project memory, Work and the shared
  dashboard without receiving VPS credentials.

Both can see the team and project observability. Each member has one or more
revocable device credentials. The server stores only their hashes.

From an authenticated manager checkout, create a 24-hour invitation with:

```bash
dduo-solo-founder team-invite --display-name "<MEMBER-NAME>" --language en
```

`--language` accepts `en` or `it`; the Team dashboard uses its active language
automatically. `--expires-in-hours` accepts from 1 to 168 hours. The command
prints a self-contained prompt pinned to the remote authority's immutable dDuo
release, with the official plugin repository, project identity, HTTPS endpoints
and one-time invitation code. It never includes SSH keys, passwords or VPS
access. The collaborator runs the prompt from the authorized project repository;
its embedded operation is equivalent to:

```bash
dduo-solo-founder remote-join --invite-payload <BASE64URL-DESCRIPTOR>
```

The base64url-encoded JSON keeps names and endpoints as data, not shell syntax.

`remote-join` first verifies that it is running at the root of a real Git
worktree, then creates a private device token locally, exchanges the invitation
once, binds only this checkout and does not require local Docker. A local binding
is promoted automatically only when its project ID exactly matches the invite.
A different project or an existing remote endpoint is never replaced by
`remote-join`; use `remote-rebind` for a verified endpoint move. Replaying the
same invitation is accepted only for the same registered token; a different
device receives an already-consumed conflict. If the collaborator cannot access
the project Git repository, memory access does not grant it and work should stop
until the repository owner authorizes Git access.

Open a remote dashboard through the CLI rather than placing a permanent bearer
in a URL:

```bash
dduo-solo-founder dashboard --tab tasks --project-root .
```

The CLI exchanges the device bearer for a five-minute, one-time browser ticket.
The dashboard consumes it into a project-specific, `HttpOnly`, `Secure`,
`SameSite=Strict` session cookie valid for seven days. Revoking a member also
revokes that member's device and browser credentials.

Every browser mutation additionally requires the CSRF value issued for that
exact browser session. The dashboard keeps it only in origin-scoped browser
storage and sends it in `X-DDUO-CSRF`; logout and authentication failures clear
it. This second binding matters when several isolated dashboards share one VPS
hostname on different HTTPS ports, because cookies are not isolated by port.

The agent runs the invitation commands and opens the authenticated dashboard;
the member need not use a terminal. `remote-join` preloads the authenticated
manual; a cache failure warns without undoing the join and retries on a later
read. After Codex installation/update, fully restart the app and open a new
chat. Claude, or binding activation without a client update, needs a new session.

The **Setup** tab is intentionally host-local and is shown only to the trusted
local infrastructure control plane. A remote dashboard never tries to open a
browser on the headless VPS; remote hosting and recovery remain explicit
Gestore dell'infrastruttura operations from the authorized project chat.
When remote backup is not configured, the manager-only **Backup** tab follows
the same boundary: it copies a credential-free request for that authorized chat
instead of calling local Setup. The agent handles VPS operations; ordinary
project members receive neither a terminal instruction nor infrastructure or
recovery secrets.

## Project operating manual

Every project has one versioned operational manual, including a solo local
project. The **Team** tab exposes it in both modes; after the authority is
shared, every member and supported client reads that same document. It is the
place for stable project procedures such as the authoritative branch, PR
policy, release gates, deployment checks, environment boundaries
and recurring QA rules.
On a shared host only the Gestore dell'infrastruttura publishes a new version;
every member reads it.

The agent can publish through `update_project_manual` only when the manager has
directly requested the update or explicitly confirmed the concise proposed
delta. It reads the current version first. Ordinary members may propose a
change but cannot bypass the server capability, and current status or narrative
history does not belong in the manual.

The current manual is required in every full session snapshot. It is delivered
in full when the 9,000-unit context budget permits; otherwise dDuo uses an
explicit head-and-tail excerpt carrying a `get_project_manual` pointer to the
complete version. Ordinary turn deltas reuse the already-live manual and emit
it again only after a revision or a startup, clear or native-compaction
SessionStart rehydration. Resume uses a delta while its baseline remains
trusted.

Above 4,000 characters the dashboard warns that compaction should be considered.
**Create compact draft** asks the configured sleep executor for an at-most
4,000-character draft, with a deterministic fallback when the provider is
unavailable. A draft is never active automatically: the manager must review and
accept it as a new version. The hard editing limit is 100,000 characters. A
provider-backed compaction records its own attributed sleep-model usage and
latency in Observability; deterministic fallback reports no invented provider
usage.

## Observability in a team

Sessions, turns, retrieval, embeddings, Work mutations and sleep jobs carry the
authenticated member identity through their derived operations. The
Observability tab is visible to the whole team and can filter summaries and
recent operations by member. Unattributed background work and data created
before member attribution appear as **System / legacy**, rather than being
assigned to a person retroactively.

Interactive usage follows the same member filter. Claude's status line and
Codex Stop contribute only attributable samples. Provider/model and input,
cache read/write, output and reasoning counters remain separate. **API equivalent**
is an estimate, not a charge; sleep and paid embeddings are separate categories.
Unknown models or incomplete usage remain **Unavailable**, never guessed zero.
See [Pricing](pricing.md) for source and list-price boundaries. Attribution
explains who caused an operation, not separate memories per member.

## Move the authoritative memory

Moving a local or remote project is a controlled full-recovery operation:

1. On the destination VPS, run `dduo-solo-founder remote-node-id` and copy the
   displayed node ID. On the old authoritative host, run
   `dduo-solo-founder remote-transfer-prepare --target-node-id <NODE_ID>`.
   The project enters `transfer_pending`, all ordinary mutations become
   read-only and the command creates the final Full Recovery Bundle v2. A local
   project also receives the single node-authority secret needed by the
   handoff; no database credential is rotated.
2. Restore that exact archive on the destination with its separately held
   recovery key, then run `remote-host`. The restored database remains
   `transfer_pending` and read-only. The command exposes the read-only
   dashboard and returns a signed `activation_receipt` proving that this exact
   project, source generation, target node and one-time transfer nonce were
   restored together, only after its public HTTPS check succeeds.
3. Check that `remote-host` reports `destination_ready` and
   `https_verified: true`. Starting Docker/Caddy or obtaining a signed receipt
   alone does not prove public HTTPS works. The automatic check validates the
   certificate for the configured host/IP and calls the API through the public
   gateway, checking the same project, source generation, target node and
   signed receipt while the destination remains read-only. TLS verification
   is never disabled and redirects are not followed. A team restored from an
   existing remote host can also inspect the read-only dashboard with its existing credentials; a
   local project intentionally creates its first manager only after completion.
   If HTTPS fails, fix the certificate, firewall or routing and retry; the
   source is not retired and the restored destination stays read-only.
   If the move is abandoned now, discard that read-only clone and run
   `dduo-solo-founder remote-transfer-cancel --new-node-not-activated` on the
   source. The old authority becomes writable and the old receipt can no longer
   complete a later transfer.
4. To commit the move, run this on the old source:

   ```bash
   dduo-solo-founder remote-transfer-retire \
     --activation-receipt '<ACTIVATION_RECEIPT>' \
     --destination-api-url '<HTTPS-API-URL>' \
     --yes
   ```

   Use the exact API URL printed by `remote-host`. `--destination-api-url` is
   required for initial retirement: the source independently repeats the
   certificate, HTTPS route and transfer-identity checks immediately before
   finalization. A failure prevents retirement and volume cleanup; cancellation
   is still possible before finalization. A verified local retirement marker
   allows a cleanup retry without repeating a completed transfer.
   After successful verification, the source marks itself irrevocably
   `transferred`, durably saves the returned `finalization_receipt`, then deletes
   only that project's old Docker volumes and unregisters its gateway route.
   Cleanup can be retried without contacting the deleted database.
5. On the destination, rerun `remote-host` with
   `--finalization-receipt '<FINALIZATION_RECEIPT>'`. Only this second signed
   proof advances the restored generation and makes it writable. Manager
   bootstrap and semantic-index reconciliation are deferred until this phase.
6. Connect the workstations according to their existing binding:

   - **First local-to-VPS move:** use the manager token returned after step 5
     with `remote-bind --replace-existing` on the original project checkout.
     Supply the project ID, name and HTTPS URLs as shown under
     [Host a project on a VPS](#host-a-project-on-a-vps). The flag replaces that
     checkout's retired local binding only after the deliberate transfer;
     verify that the project ID matches. The private token prompt keeps the
     token out of shell history.
   - **Already remote workstation:** use `remote-rebind` below to reuse its
     existing device token without copying it. A local checkout has no remote
     token and cannot use this command.

   ```bash
   dduo-solo-founder remote-rebind \
     --project-id <PROJECT_ID> \
     --api-url https://<NEW_HOST>/api \
     --dashboard-url https://<NEW_HOST>
   ```

   The command confirms the endpoint change, reuses the existing private local
   token and verifies that the same project member is accepted by the restored
   node. New collaborators use their one-time invitation.

Never cancel after source finalization. Destination readiness alone never makes
the clone writable, so cancellation remains safe before that irreversible
step. Receipts use HMAC with the node-authority secret copied inside the
encrypted full-recovery bundle. In the current Beta this is intentionally an
operational split-brain guard for hosts controlled by the same trusted
infrastructure manager: it detects a wrong project, generation, target, nonce
or corrupted receipt, but cannot protect against a malicious destination
that already possesses the shared secret.
Keep the final archive and recovery key until the new host has passed its
recovery and access checks.

Full Recovery Bundle v2 and its security boundary are documented in
[Backup and recovery](backup-and-recovery.md). Client binding and explicit
update rules are documented in
[Client binding and explicit updates](client-binding-and-updates.md).
