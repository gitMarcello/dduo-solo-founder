[**EN · English**](uninstall.md) · [IT · Italiano](uninstall.it.md)

# Uninstall

From a checkout of the same or newer dDuo Solo Founder release, run:

```bash
node bin/install.mjs --uninstall --yes
```

The installer first backs up every project with a configured destination. A
failed configured backup blocks uninstall unless `--force` is explicitly
supplied. Projects without backup configuration are named as unprotected and
their Docker volumes are still preserved.

Stopping a project preserves data. Removing its Compose volumes permanently
deletes PostgreSQL and Qdrant data. The agent must name the
affected project and ask for explicit confirmation before any volume deletion.

A remote-bound checkout owns no local project stack to remove. Uninstalling its
client plugin does not stop or delete the authoritative VPS project and does not
implicitly revoke that device credential. The Gestore dell'infrastruttura
revokes the member or device from the Team page when access must end.

If Claude usage telemetry was installed, uninstall first restores the exact
status-line configuration that dDuo had preserved, then removes its adapter.

Uninstall removes the package and selected Codex/Claude registrations, not
PostgreSQL, Qdrant, project secrets, backups or recovery keys. Client-owned
`PLUGIN_DATA` may be removed; it never holds authoritative project memory.

Delete `.dduo-solo-founder/project.toml` only when the repository should no longer point
to that memory identity.
