[**EN · English**](update.md) · [IT · Italiano](update.it.md)

# Update and rollback

Updates are explicit. The user chooses a reviewed repository revision and asks
Codex or Claude to install that version for the current project. dDuo never
downloads or activates a release in response to a project server, dashboard or
hook message.

After one confirmation, the installer verifies the package, protects projects,
prepares the runtime side by side and updates the selected adapter transactionally.
It opens Setup unless `--no-setup` or `--headless` is selected. Fully restart
Codex and open a new chat; Claude needs only a new session.

## Agent Plugins update boundary

The revision contains the portable package and native Codex/Claude adapters
under `it.dduo.client-support/`. `PLUGIN_ROOT` follows the installed package;
client-scoped `PLUGIN_DATA` is never project memory, Work, credentials or backup
authority. Updating the standard package does not enable unsupported clients.

## Data safety

The Beta migration is additive for authoritative project data. It preserves
project identity, profile revisions, Plans, Tasks, artifacts, raw turns,
segments, consolidated memories, memory revisions, sleep jobs, observability,
team state, outbox events and backup records. Version 0.2 adds Sprint records,
membership and versioned closure history without assigning existing tasks to
invented sprints. Unfinished tasks without a sprint remain in Backlog;
`done`/`cancelled` tasks without a sprint remain in legacy History. Epics remain
project-wide and completed Plans remain available in history. Existing memory
text is not rewritten.

The semantic task and memory collections remain derived data, separately
versioned from PostgreSQL. Startup and restore reconcile them with authoritative
rows. Missing, stale or orphaned points are repaired; exact task reads and
bounded PostgreSQL fallback remain available while reconciliation is pending.

Before replacing a runtime, the installer runs every configured verified
encrypted project backup with trigger `update`. It also stops each registered
local project briefly and creates a private checksummed PostgreSQL/Qdrant volume
snapshot under `~/.config/dduo-solo-founder/upgrade-snapshots/`. This snapshot
is a local rollback safety net, not a substitute for an off-device encrypted
backup.

Project secrets stay under
`~/.config/dduo-solo-founder/project-secrets/<project-id>/`. Compatibility
migrations preserve existing project values and roll back on failure.
Obsolete software registrations and caches are removed only after a successful
installation, never project databases, configuration, backups or Docker volumes.

## Update procedure

1. Commit or otherwise preserve current repository work.
2. Select the exact trusted dDuo repository revision to install.
3. Ask the active Codex or Claude client to update dDuo for this project and
   approve the one aggregate install action.
4. Complete only the actions still shown by Setup.
5. Let the agent run `check_setup`; do not accept a success claim without that
   result.
6. Fully quit and reopen Codex, then open a new chat in the same folder; with
   Claude, open a new session.
7. Verify memory status and Work before deleting any rollback snapshot.

An API compatibility response can tell the user that the client is too old,
but it cannot perform steps 2–6 or choose a source revision.

For a VPS runtime update use `node bin/install.mjs --headless --yes` from the
selected release with the server's `--project-root`. For a remote workstation
use its selected adapter with `--no-setup`, preserve its existing binding and
verify authenticated remote readiness. Neither path needs local Docker on a
remote workstation. A change to the server endpoint follows `remote-rebind`,
not project initialization.

## Roll back safely

1. Ask the active agent to diagnose the update. It must request explicit
   confirmation before changing local data.
2. Reinstall the intended prior dDuo revision through the same explicit
   installer.
3. If application rollback is insufficient, restore the last verified
   `.dduobackup` archive using its separate recovery key.
4. For an immediate local volume rollback, use the verified upgrade snapshot:

   ```bash
   node bin/install.mjs --restore-upgrade-snapshot <snapshot-directory> \
     --project-root <project-directory> --yes
   ```

5. If Codex was reinstalled, fully restart it; then open a fresh project chat.

The snapshot command verifies its manifest and archive checksums before
replacing only the named project's PostgreSQL and Qdrant volumes. Use the
encrypted Full Recovery Bundle when the host itself is uncertain or the
project must be recreated on another computer or VPS.

Full Recovery Bundle v2 includes allowlisted project secrets, a project-scoped
file-backed Codex credential when available, a remote device token when
explicitly applicable, and pending project hook/MCP state. It excludes the
installed plugin package, client registrations, `~/.ssh`, arbitrary home files,
OS keychains and its own recovery key. Restoring one project therefore cannot
change the plugin installation used by another project.

## Why Codex restart and a fresh session are mandatory

Codex Desktop caches local plugin packages outside the individual chat, so an
install or update requires a complete app restart and then a new chat. Claude
loads the revision in a new session and does not require an app restart. This
boundary prevents one conversation from mixing two releases and keeps
observability and turn capture unambiguous.

See [Client binding and explicit updates](client-binding-and-updates.md) for
package and binding contracts.
