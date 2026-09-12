[**EN · English**](backup-and-recovery.md) · [IT · Italiano](backup-and-recovery.it.md)

# Backup and recovery

Recovery guide for dDuo Solo Founder `0.2.0-beta.1`.

## Recovery contract

PostgreSQL is authoritative. Qdrant contains separate derived collections for
semantic memory and task search. Full Recovery Bundle v2 snapshots both
collections when Qdrant is enabled, but either projection can still be rebuilt
from PostgreSQL. A missing or incompatible vector snapshot therefore degrades
restore speed, never the authoritative project state.

The same PostgreSQL backup protects all Work views: Sprint, Backlog and
History. It includes sprint definitions and lifecycle state, task membership,
immutable closure snapshots and outcomes, task revisions, and sprint mutation
receipts. Restore preserves that recorded placement and history; it does not
start a sprint, close one, or automatically reassign unfinished tasks. An older
archive without sprints gains the current schema during migration; it does not
invent historical sprint membership.

dDuo Solo Founder does not back up Docker volumes or Docker Desktop disk images.
It exports application data into one portable `.dduobackup` file that can be
restored into fresh, project-isolated volumes on another supported host.

Observability events and optional exact hook/MCP snapshots survive restore.
They retain unmasked project context, including secrets if supplied in that
content. Raw CLI diagnostics, runtime authentication keys and unrelated paths
are not collected as technical telemetry fields. Their recording alone does not schedule a
backup, and missing historical snapshots are never invented.

## Archive format

The outer file uses streaming AES-256-GCM with a random 96-bit nonce and an
independent 256-bit key per project. A current authenticated inner ZIP contains:

```text
manifest.json
checksums.sha256
postgres.dump
project.toml
runtime-settings.json
secrets/dduo.env
secrets/codex/auth.json                  # when file-backed auth is available
binding/project.toml                     # portable identity, not local approval
binding/remote-credential.token          # only for an explicitly remote binding
host-state/hooks/*.json                  # project-scoped pending delivery
host-state/mcp-observability.json        # optional pending telemetry
history/backup-records.json               # portable completed history when available
qdrant/memory.snapshot                    # optional derived accelerator
qdrant/tasks.snapshot                     # optional derived accelerator
```

Runtime settings and secrets use explicit allowlists; dDuo never archives an
arbitrary environment file, home directory, SSH key, operating-system keychain,
repository file, Docker image, bridge token, PID, log, or unrelated project
spool. The installed Agent Plugins package and Codex/Claude client
registrations are also excluded: restoring one project cannot replace the
client software used by the rest of the machine. Unsupported installation
entries in older bundles are ignored. The runtime and minimum compatible dDuo
version remain authenticated in the manifest and runtime settings. The Codex
file credential is copied only from the project's private
`CODEX_HOME` configured with `cli_auth_credentials_store = "file"`. OpenAI
documents this headless/container transfer path, but an externally revoked or
expired login still requires a new login after restore.

The derived manual cache is excluded and refreshed from authenticated
PostgreSQL-backed responses. The workstation's Claude status-line adapter and
restore metadata are also excluded: install them from local Setup on a new
computer without overwriting its existing status line during project restore.

The manifest records format and application versions, project identity, backup
cut, file sizes, SHA-256 digests, both Qdrant collection identities, credential
coverage, and warnings. No secret or plaintext metadata is exposed outside the
encrypted payload. The recovery key is never a member of the archive.

Readers continue to accept schema v1 archives. New backups are v2 and are not
published unless the authenticated project host agent returns the bounded,
project-filtered supplement needed for integral recovery. A missing agent does
not silently produce a backup labelled complete.

After writing an archive, the runtime decrypts it, validates the complete ZIP
inventory, rejects unsafe paths, links, duplicate members, unexpected host
files and cross-project state, verifies every size and digest, and runs
`pg_restore --list`. Only a successful verification makes the archive eligible
for retention and marks the included project generation as protected.

## Configure

For a local project, the infrastructure manager opens **Backup** in the
dashboard and chooses **Enable backup**. The host Setup page configures the
default folder `~/Documents/dDuo Solo Founder Backups`, refreshes the two runtime
services that need the new mount, starts the first archive when it is due, and
offers the new recovery key as a one-time download.

For a remote project, the manager-only Backup tab provides a credential-free
request for an authorized project chat. The agent configures the authoritative
VPS, not workstation Setup, and requests only missing host access. Use private
input for credentials; off-record mode neither replaces private input nor
erases audit records. Members cannot inspect backup metadata or create,
download or configure full recovery archives and are not asked to operate the VPS.

The default folder is local. Download each verified archive from the dashboard
to an external disk, synchronized cloud folder, NAS, or another independently
backed-up location before treating it as protection against loss of the
computer. The CLI remains available when an advanced user wants to select a
different destination:

```bash
dduo-solo-founder backup configure /path/to/off-device-folder
```

Each project receives a dedicated subdirectory and key. Configuration lives at:

```text
~/.config/dduo-solo-founder/backups.json
~/.config/dduo-solo-founder/backup-keys/<project-id>.key
```

The key file has user-only permissions. The recovery key is shown once during
initial configuration. Store it in a password manager that is not dependent on
the backed-up computer. The archive deliberately does not contain its own key.

Changing only the destination preserves the existing key so older backups stay
readable. A restore refuses to replace a different local key unless the user
explicitly chooses `--force` after confirming replacement of project memory.

## Automatic behavior

`Stop` calls a fast scheduling endpoint after the active turn is durably
committed, and the project API scans due projects every five minutes. A backup
runs in the background when all conditions hold:

- a destination and recovery key are available;
- project data changed after the last included generation;
- at least 24 hours passed since the last verified backup;
- no backup is already scheduled or running.

Writes that race an active dump advance the project generation. The completed
archive remains valid, but the project stays marked dirty so the later changes
are included next time.

Creation is serialized per project. PostgreSQL supplies the consistent
authoritative cut; the host agent snapshots project-owned spool files with
atomic file reads, and Qdrant is recorded as a reconcilable projection. This is
deliberately logical consistency rather than a fictional distributed
transaction across PostgreSQL, Qdrant and the host filesystem. Idempotency keys
make a spool item that is also present in PostgreSQL safe to replay once.

The default retention policy keeps one newest archive in each selected bucket:

- 7 daily buckets;
- 4 ISO-week buckets;
- 6 monthly buckets.

Several manual backups created in the same day occupy the same daily, weekly,
and monthly buckets, so only the newest of those copies is retained. The newly
verified archive is always preserved even if the computer clock is behind an
older filename.

Retention runs only after the new archive passes cryptographic, structural,
checksum, and PostgreSQL verification.

## Manual operations

```bash
dduo-solo-founder backup create
dduo-solo-founder backup status
dduo-solo-founder backup verify /path/to/archive.dduobackup
dduo-solo-founder backup drill /path/to/archive.dduobackup
```

`verify` proves cryptographic integrity, archive structure, checksums, and dump
readability. `drill` goes further: it starts a disposable PostgreSQL container,
restores the dump, verifies that the archived project exists, counts projects,
tasks, memories, and turns, then removes the container. Omitting the archive
path selects the newest configured backup. The drill never touches live project
volumes and reports both semantic projections as rebuildable from PostgreSQL.

The web UI provides the manager with current status, direct download of retained
verified archives, and a secondary manual creation action. A failed backup
remains visible in activity and status without marking the project clean.

Before an update or uninstall, the installer reads the private backup registry
and runs the equivalent of this command once for every registered project that
has a configured destination:

```bash
dduo-solo-founder backup create --trigger update --project-root /exact/project/root
dduo-solo-founder backup create --trigger uninstall --project-root /exact/project/root
```

The explicit project root prevents stale registrations from redirecting a
backup to another checkout. A configured backup failure stops the installer.
`./install.sh --force` is the explicit escape hatch and does not delete the
previous archive or project volumes.

## Move to another computer

1. Install the compatible dDuo Solo Founder release and Docker on the new
   host (Docker Desktop for a local workstation; Docker Engine with Compose
   for a Linux VPS). On a VPS, use `./install.sh --headless` from the release
   checkout and run `dduo-solo-founder remote-preflight` before restore.
2. Clone or copy the project repository.
3. Make the `.dduobackup` file available locally.
4. Retrieve the project's recovery key from the separately protected password
   manager.
5. Run the restore command from that repository.

```bash
dduo-solo-founder backup restore /path/to/archive.dduobackup
```

For a schema v2 archive, the project-scoped OpenAI key, internal dDuo secrets
and portable Codex file credential are restored from the encrypted bundle with
user-only permissions. A schema v1 archive still requires
`dduo-solo-founder configure-openai` first.

The restore command authenticates and checks the archive, validates the dump,
and proves it with a real restore into a disposable PostgreSQL container before
asking for destructive confirmation. If the target already owns project
memory, restore first requires a newly verified safety backup; failure never
silently discards the only source copy. It preserves the project UUID, creates
a new root/endpoint binding authority, reserves ports that are free on the new
computer, restores fresh isolated volumes, applies current migrations, and
updates the project root path. Machine-local approvals are intentionally not
copied: remote access and native hook trust must be approved on the new client.

For an in-place restore, the safety bundle is authenticated, extracted,
semantically checked and restore-drilled before the first mutation. Its archive
and manifest digests are pinned for the operation. If PostgreSQL restore,
configuration, startup, migration or API health fails after mutation begins,
dDuo automatically reinstates that verified bundle, its project configuration,
runtime settings, secrets, recovery key and backup registry. A failure limited
to the derived Qdrant indexes happens after the authoritative core commit and is
reported as `RESTORE_DEGRADED` for reconciliation instead of rolling back a
healthy PostgreSQL recovery.

If the Qdrant memory snapshot restores successfully, memory retrieval is
reconciled against PostgreSQL: unknown points are deleted and missing or stale
records are queued. Otherwise the command resets the current memory collection,
queues every active PostgreSQL memory for reindexing, and reports that fallback
explicitly.

The task snapshot is restored independently when present. Restore then always
ensures and reconciles the derived task collection after PostgreSQL and the
current application are ready. Current points are preserved without another embedding;
missing, stale, renderer-versioned, or newer-than-PostgreSQL points are queued
from the authoritative row. Reconciliation writes a collection epoch marker;
later semantic searches verify that marker and compare exact aggregate
cardinality with PostgreSQL without transferring a full task inventory. They
fall back if a collection was replaced or partially lost outside the normal
lifecycle. Exact task retrieval, lists, and task mutations remain
available while the worker rebuilds semantic search. A failure in either memory
or task index recovery does not roll back PostgreSQL; the CLI reports
`RESTORE_DEGRADED` and exits with status 8 so the incomplete semantic recovery
cannot be mistaken for a fully healthy restore.

Verify the active/planned sprints, Backlog, History, completed outcomes and
attachments after recovery. Index reconciliation changes search projections,
not sprint membership or execution status. Move work only through an explicit
Work operation after checking the restored state.

The in-flight backup record remains excluded from `postgres.dump` to avoid a
circular, permanently running record. Completed operational history is exported
as portable JSON, reinserted idempotently after restore, and followed by source
archive provenance. Host paths in old records are never treated as active
destinations. Later root-path and index changes remain dirty, so history does
not falsely claim that new local writes are already protected.

## Move an authoritative project to another VPS

Do not copy a live database while two nodes remain writable. Use the authority
state and Full Recovery Bundle v2 together.

Install the destination runtime with `./install.sh --headless`, prepare the
Linux VPS prerequisites and run `dduo-solo-founder remote-preflight` before
freezing the source. Restore supplies the existing project identity: do not
initialize a different project on the destination. This transfer procedure is
for existing memory; [creating the first memory directly on a VPS](remote-teams.md)
does not require a transfer archive.

1. Read the destination identity with `dduo-solo-founder remote-node-id`. On the
   old host, run
   `dduo-solo-founder remote-transfer-prepare --target-node-id <NODE_ID>`. It
   changes the project from `active` to `transfer_pending`, rejects ordinary
   writes and creates the final backup at the frozen authority generation. The
   transfer accepts only a verified Full Recovery Bundle v2 whose encrypted
   manifest inventories and fingerprints the included node-authority secret;
   a legacy or merely “complete” archive without that attestation cannot retire
   the source.
2. Restore the final archive on the new VPS, using the separate recovery key,
   then run `dduo-solo-founder remote-host --public-ip <PUBLIC-IP>`. The restored
   clone stays read-only and returns a signed `activation_receipt`; it does not
   advance the generation or become authoritative yet.
3. Verify `destination_ready` and `https_verified: true`. `remote-host` validates
   the public certificate and the complete HTTPS route to this exact read-only
   project; a signed receipt or a running gateway alone is insufficient.
   Existing remote-team credentials can inspect the read-only dashboard; a
   local project defers first-manager bootstrap until completion. If the move
   is abandoned at this phase, discard the clone and run
   `dduo-solo-founder remote-transfer-cancel --new-node-not-activated` on the
   old host.
4. To commit the move, run this on the old host with the destination receipt:

   ```bash
   dduo-solo-founder remote-transfer-retire \
     --activation-receipt '<ACTIVATION_RECEIPT>' \
     --destination-api-url '<HTTPS-API-URL>' \
     --yes
   ```

   Use the exact API URL printed by `remote-host`. Before finalization, the
   source independently verifies the certificate, HTTPS route and transfer
   identity, without disabling TLS verification or following redirects. If
   verification fails, retirement and volume cleanup do not begin.
   It cryptographically binds the acknowledgement to the project, frozen
   generation, source, target and transfer nonce. The source becomes
   irreversibly `transferred`, persists a `finalization_receipt` before cleanup,
   deletes only that project's old Docker volumes and removes only its Caddy
   route. An interrupted cleanup resumes from the private marker without the
   deleted database, but first HMAC-authenticates the saved receipt again and
   checks its project, generation, source and target claims. A syntactically
   plausible or edited marker cannot authorize volume deletion.
5. On the destination, rerun `remote-host --public-ip <PUBLIC-IP>
   --finalization-receipt '<FINALIZATION_RECEIPT>'`. Only then does the clone
   advance to the next generation, become writable, bootstrap a manager when
   needed and reconcile semantic indexes. In the same authority transaction it
   marks any scheduled/running backup row inherited from an older image as
   interrupted, preventing stale work from blocking the destination's first
   real backup.
6. For a first local-to-VPS move, connect the original checkout with the manager
   token returned after step 5 and `remote-bind --replace-existing`, checking
   that the project ID matches the retired local binding. For workstations
   already connected remotely, use `remote-rebind`; it reuses that device's
   private project token without printing or copying it. New collaborators
   use their one-time invitation. See the [binding instructions](remote-teams.md#move-the-authoritative-memory).

Never cancel after the source returned its finalization receipt. The HMAC
receipt is a KISS safety mechanism for accidental split brain between hosts run
by the same trusted infrastructure manager. Because the node-authority secret
is intentionally present in the encrypted full-recovery bundle, this Beta
protocol is not a defense against a malicious destination host holding that
shared secret; that stronger threat model needs asymmetric source-only signing
or an external coordinator. Keep the final archive and its recovery key until
the new node has passed the full access and restore checks. See
[Remote projects and teams](remote-teams.md) for the complete hosting and
binding workflow.

## Device loss

Recovery after total device loss requires two independent items:

- at least one synchronized or otherwise off-device `.dduobackup` archive;
- the matching recovery key in a separately recoverable password manager.

Losing either item makes recovery impossible by design. Periodically run
`backup drill` against a synchronized archive. A successful drill proves the
authoritative database can be restored without replacing the live project.
Also test a complete cross-machine restore before treating the system as the
only operational memory.

Anyone holding both items can decrypt project conversations and use the copied
OpenAI/Codex credentials until those credentials are revoked. Treat the pair as
account-level access, distribute it only to an intended recovery operator, and
rotate provider credentials after an archive leaves the intended trust
boundary. Secure erasure of plaintext temporary blocks on SSD storage cannot be
guaranteed; restore uses private temporary directories and minimizes their
lifetime.

## Failure behavior

- Destination unavailable: no archive is written; the project remains dirty.
- Wrong key or altered ciphertext: authentication fails before extraction.
- Invalid ZIP inventory, path, checksum, or size: verification fails.
- Invalid PostgreSQL dump: `pg_restore --list` fails and retention does not run.
- Host agent unavailable, oversized or malformed supplement, unexpected file,
  cross-project spool, or missing dDuo secret: a v2 archive is not published.
- Qdrant unavailable during backup: PostgreSQL backup succeeds with a warning.
- Qdrant memory snapshot incompatible during restore: PostgreSQL restores and
  memory reindex is queued.
- Task index restore: the task collection is always reconciled from PostgreSQL,
  preserving exact current points and rebuilding every mismatch whether or not
  a memory snapshot was included or restored.
- Memory or task reindex unavailable: PostgreSQL remains restored, the CLI exits
  with degraded status, and the user is told to repair semantic retrieval.
- Process interruption: a scheduled/running record is marked failed at next API startup.
- Sleep credential revoked or expired: project data and embeddings restore;
  subscription-backed sleep reports login required until Codex or Claude is
  authenticated again in the project's private host state. File-backed Claude
  credentials are included when available; macOS Keychain credentials require
  a new official login after restore.
