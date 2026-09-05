[**EN · English**](client-binding-and-updates.md) · [IT · Italiano](client-binding-and-updates.it.md)

# Client binding and explicit updates

## One checkout, one project authority

Each repository has exactly one binding in
`.dduo-solo-founder/project.toml`:

- `binding = "local"` uses only that project's reserved loopback API and
  dashboard ports and starts only its isolated Docker stack;
- `binding = "remote"` uses only the declared HTTPS API and dashboard
  endpoints and never starts or probes a local fallback.

The binding is per checkout, not global to the client or workstation. Every
project keeps separate IDs, PostgreSQL volumes, Qdrant collections, secrets
and backup history even when the same plugin serves several projects.

The local registry records the canonical repository root and an owner-aware
fingerprint. Runtime commands validate that claim before starting, stopping,
configuring, backing up or reading a project. Copying the descriptor into a
different folder does not clone authority: the copied checkout must complete an
explicit move, restore or remote rebind. The same project cannot be silently
claimed by two roots.

Remote descriptors contain no secret. Before use, the user approves the exact
canonical root, project ID, API endpoint and dashboard endpoint. The device
bearer is stored with mode `0600` outside Git; the server persists only its
hash. Moving or restoring a repository changes its binding authority and
requires another explicit approval.

The permanent bearer is sent only in the `Authorization` header. The dashboard
command exchanges it for a five-minute one-time ticket and then for a
project-specific secure browser session. Revoking a member revokes that
member's devices and browser sessions.

## Agent Plugins package and native adapters

The Beta distribution has three layers:

1. the Agent Plugins 1.0 portable package: root `plugin.json`, `skills/` and
   root `mcp.json`;
2. the shared dDuo application/runtime: MCP implementation, API, database,
   retrieval, Work, observability, backup and Setup;
3. thin client adapters under the dDuo-owned `it.dduo.client-support/`
   extension namespace: Codex and Claude Code lifecycle manifests.

The root `mcp.json` starts the same stdio launcher through `PLUGIN_ROOT`. The
launcher resolves the installed runtime and delegates to the shared MCP server.
Codex and Claude initialize that server with their real client identity; an
unsupported or mismatched client is rejected. This is an explicit Beta support
boundary, not DRM. A third-party client is not supported until it has a tested
lifecycle adapter, because Agent Plugins 1.0 standardizes Skills and MCP but not
the automatic turn hooks required by dDuo.

Installation materializes one small native projection per selected client
instead of installing the repository root directly. Codex receives its native
manifest, shared Skill, hook adapter and license. Claude receives its native
manifest, shared Skill, package-local MCP launcher and license. Portable root
manifests and the other client's files are excluded from both projections, so
they cannot shadow native discovery. The installer writes the verified absolute
runtime dispatcher into the Codex projection. Project selection is never frozen
there: every MCP call still supplies and validates its own `workspace_root`.

`PLUGIN_ROOT` is appropriate for files shipped with the selected plugin
revision. `PLUGIN_DATA` may be used by a client for disposable installation
cache, but dDuo does not make it authoritative. Project memory must be shared
between Codex and Claude and may live on a VPS; plugin-instance storage can be
client-specific and may be deleted on uninstall. Authoritative data therefore
remains in the bound project stack and project-scoped host configuration.

## Compatibility response

Every dDuo hook, MCP process and launcher request declares its release and wire
protocol. A project server accepts compatible protocol versions even when the
client release differs. An incompatible request returns HTTP `426` with a safe
instruction to install a compatible official version and apply the client
reload boundary: a complete Codex restart plus new chat, or a new Claude
session. The response is advisory only: it contains no executable URL, trust
key, checksum or permission grant and cannot start an update.

Browser traffic is negotiated by the dashboard bundle and is not mistaken for
a plugin client. Unknown or newer plugin release strings remain usable when the
wire protocol matches, so the server never asks a compatible client to
downgrade.

## Explicit installation and update

The user or authorized agent chooses a repository revision and runs its
installer explicitly. No additional update key is needed; the project API
cannot download or activate software on the client.

The installer:

1. validates the source tree and checksum manifest before project mutation;
2. acquires an owner-aware machine install lock;
3. protects configured project data with the existing backup/snapshot gates;
4. builds the locked runtime side by side;
5. installs the selected Codex or Claude adapter, or only the runtime when
   `--headless` / `--only core` is selected;
6. verifies the result and commits all pointers atomically;
7. removes obsolete installation artifacts only after a successful commit.

If a later verification fails, the prior runtime, selected client state,
registry and project configuration are restored. Project databases, Qdrant
volumes and encrypted backups are not installation artifacts and are not removed.

`--headless` also suppresses browser Setup. `--no-setup` suppresses Setup while
retaining an explicitly selected client adapter, for example when installing a
workstation for `remote-bind` or `remote-join`. Neither option activates a new
local project. Native Windows entry points, `.cmd` wrappers and Node-based
Codex hooks use the same verified runtime as macOS; see
[platform support](platform-support.md).

<a id="alpha-secret-migration"></a>
## Migration from older installations

Normal operation uses one private secret file per project, never a global
environment. When migrating an older installation, the installer validates
registered destinations, freezes the previous file atomically and copies only
allowed secrets to project-private files. Existing project values win.
Failure restores original files; concurrent writes are retained separately
for explicit recovery. The frozen file is retired only after successful commit.

## Client reload boundary

Codex Desktop caches local plugin packages outside the chat. After a Codex
installation or update, fully restart the app, finish any Setup action and open
one new chat in the same project folder. Claude needs only a new session. First
activation or remote rebind without a client update requires only a fresh
chat/session. Normal project work and temporary memory outages do not require
either.

Full Recovery Bundle v2 remains project-scoped. It includes the compatible
runtime requirement and project-owned queues/secrets, but never exports the
workstation's plugin package or client registrations. Restoring one project
cannot change which plugin revision another project or client is running.
