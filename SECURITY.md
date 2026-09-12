[**EN · English**](SECURITY.md) · [IT · Italiano](SECURITY.it.md)

# Security

dDuo Solo Founder is a project-isolated development tool that can run locally
or as an authenticated team service on a VPS. Report vulnerabilities through a
[private GitHub security advisory](https://github.com/gitMarcello/dduo-solo-founder/security/advisories/new)
before opening a public issue. Never attach
Docker volumes, `.env` files, raw conversations, credentials, recovery archives
or contact lists to a report.

## Installation and update trust

Install and update only on request, from a repository revision you choose.
No additional update key is required. The project API cannot download or
activate software on your computer.

The installer runs with the current user's permissions, verifies the checked
distribution against `checksums.sha256`, materializes the locked Python
environment and changes the runtime plus the selected Codex/Claude adapter as
one transaction. It never uses `sudo` and never deletes project Docker volumes
as part of a normal install or update. Review and trust the source before
running it, including `--dry-run`: that option still executes the repository's
code and is not a sandbox. Checksums detect mismatches within a distribution,
not the authenticity of a malicious fork. A successful Codex update requires a complete
app restart and then a new chat; Claude requires a new session. An
already-running client cannot reload replaced hooks, skills or MCP processes.

The Beta package follows Agent Plugins 1.0 for its portable `plugin.json`,
`skills/` and `mcp.json`. Codex and Claude Code are the only supported clients.
Their lifecycle hooks are native, reviewed adapters; other clients do not gain
dDuo capabilities merely by discovering the standard manifest. `PLUGIN_ROOT`
locates packaged resources. `PLUGIN_DATA` is not trusted as project authority,
memory or credential storage because it is owned by one client installation and
may be removed on uninstall.

Credentials remain project-scoped. Migration from older installations copies
only allowed values, preserves existing project credentials and rolls back on
failure; concurrent changes are retained for recovery. Normal runtime paths
never inherit another project's or a machine-global secret file.

## Client and project boundaries

Each configured repository is bound to exactly one project identity and one
authority: its isolated local stack or one approved remote endpoint. Root
ownership is validated before operational commands. Copying a configured
checkout, changing its canonical root or reusing its descriptor requires an
explicit move, restore or rebind. A remote checkout accepts only canonical
HTTPS endpoints and never falls back to local Docker.

A remote descriptor contains no secret. The device bearer and approval for the
exact root/project/endpoint fingerprint live outside Git in private files; the
server stores only token hashes. Invitation prompts are single-project and
one-time and contain no VPS, SSH, OpenAI or Codex credential. Dashboard access
exchanges the bearer for a short-lived ticket and a project-specific
`HttpOnly`, `Secure`, `SameSite=Strict` cookie. The two capabilities are Gestore
dell'infrastruttura and Membro del progetto; only the manager controls invites,
revocation, manual publication, backup and authority transfer.

The host CLI bridge uses a high-entropy master bearer and derives a different
token for each project container. It listens on a dynamic host port only so
Docker can reach the host subscription process, rejects peers outside
loopback/private container networks, and is never routed by the public Caddy
gateway. A VPS firewall should expose only SSH and the printed project HTTPS
ports.

Subscription authorization is verified through the provider's official local
flow. Start a provider login within the user's installation, hosting or login
authorization; authentication codes and credentials remain in that protected flow.
Codex hook authorization is read through the official app-server API and can be
granted only by the user through Codex's native trust review. dDuo never edits
or bypasses that trust state.

For a fresh VPS, use `--headless`, `remote-preflight` and `init --headless`.
Enter the embeddings key through hidden input and authenticate with
`login-codex --device-auth` in the private project Codex home. Keep keys, login
codes and manager tokens out of chat, logs and command arguments. Off-record
mode requires an initialized session and does not erase its audit history;
it is not a substitute for private credential input or native user consent.

The last operational manual received from an authenticated remote response may
be cached in a private `0600` file. It is HMAC-authenticated with the project
device bearer and bound to the exact project and checkout binding, but is not
separately encrypted. Hooks and MCP use it only for network or server 5xx
failures; revoked credentials, authorization failures and incompatible clients
are never bypassed. Treat the cache as project content.

## Backup and authority transfer

Backup archives are encrypted with a project-specific recovery key and may
contain the complete project history, including secrets intentionally shared
with trusted agents. Full Recovery Bundle v2 includes all allowlisted durable
dDuo credentials needed to recreate the project: OpenAI, internal database,
authentication, session and authority secrets, an explicit remote device token
when applicable, project queues and the file-backed Codex credential when
available. It never sweeps arbitrary home files, `~/.ssh`, an OS keychain,
logs, repository source, plugin installation state or Docker images.

Never attach `.dduobackup` files or recovery keys to public issues. Store
archives off-device and keep recovery keys in a separately recoverable password
manager. The recovery key is excluded from every archive. Anyone holding both
the archive and key can decrypt the whole project and use copied provider
credentials until they are revoked.

Restore is destructive only after archive authentication, checksum and
PostgreSQL validation, and explicit confirmation. `--force` can replace a
different project identity or continue an installer operation after backup
failure; review the named project and archive before approving it.

Authority transfer freezes the old project before the final backup. Cancelling
requires the explicit `--new-node-not-activated` attestation. Once the restored
node reports `destination_ready` and its HTTPS endpoint is verified, retire the
old node with
`remote-transfer-retire --activation-receipt '<RECEIPT>' --destination-api-url '<HTTPS-API-URL>' --yes`,
using the exact public API URL printed by `remote-host`. Before finalizing,
the source independently checks the TLS certificate, complete HTTPS API route
and transfer identity. TLS failures block retirement and volume cleanup; do
not disable certificate verification to proceed.
Never cancel after the source has returned its finalization receipt.
