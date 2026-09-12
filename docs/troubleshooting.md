[**EN · English**](troubleshooting.md) · [IT · Italiano](troubleshooting.it.md)

# Troubleshooting

Troubleshooting for dDuo Solo Founder `0.2.0-beta.2`.

Normal use should require no technical diagnosis. dDuo keeps project work
available when its configured local or remote memory is temporarily unavailable.

## MCP works but new conversations are not recorded

MCP availability is not proof that native lifecycle hooks can run. In a
configured project the assistant checks `check_memory_connection` before work;
Codex hooks that need attention trigger a brief choice: review the native
permission in the active client, or explicitly continue without
automatic memory. Work calls are not executed until that choice is supplied.
The choice belongs to that chat only, not the machine or other projects.
After permission is repaired, the assistant rechecks; a new or changed warning
requires a new choice. Checks are cached for at most 30 seconds, and the explicit
check always refreshes. No permission is granted or service started by the check.

`--verify` fails while hook approval is missing. Installation itself can finish
with an **installed, authorization pending** notice so native approval remains
possible. After updating the plugin, use the
[surface-specific restart/session handoff](installation.md#required-handoff).
Verify a completed turn and subsequent sleep before calling the whole system
healthy. Authorizing hooks does not backfill uncaptured conversations.
Claude's native authorization check is unavailable; this does not declare its
capture broken or verified. If MCP and the Skill were not loaded either, dDuo
cannot deliver an independent warning. The prompt to choose is model-mediated,
not a technical lock on the entire coding agent.

## The client asks approval before consolidating remote memory

A native client safety review is separate from dDuo authentication and HTTPS.
An approval request alone does not mean that the VPS connection is insecure.

`request_sleep` schedules processing of turns already saved in the bound
project, excluding off-record turns; its scheduling POST does not upload a new
chat transcript. Pass the known session ID to scope it to that conversation.
Without it, a manager/local owner can request project-wide consolidation.
Workers send selected stored material and relevant context to Codex/Claude
and use configured embeddings (OpenAI by default). Processing may consume
subscription usage and, with OpenAI embeddings, API credits.
The operation writes jobs and may revise memories; it is not read-only.

The assistant should explain that scope and respect the native approval or
denial. Do not disable TLS validation, weaken permissions, or reroute the same
command to avoid a denied approval. These clearer tool descriptions reduce
ambiguity, but cannot guarantee that the client will never request approval.

## Setup did not finish

Ask the assistant to finish configuration: it offers to open Setup and guides
the next necessary action. You can explicitly defer; it will not keep asking
about the same accepted limitation. On first use it recommends activating
project memory and Work, with continuing without memory as the alternative.
If you prefer the dashboard, select **Setup > Open setup**. The
page reports only the relevant prerequisite: Docker Desktop, the embeddings
key, a provider connection, Codex hook permission, or project activation.

For a fresh Linux VPS, install the runtime from the release checkout with
`./install.sh --headless` (equivalent to `--only core`). This skips client
adapters and browser Setup. Use `--no-setup` with a selected workstation
adapter, such as `node bin/install.mjs --only codex --surface cli --no-setup`,
when installation must not open Setup. Use `--surface vscode` for the official
extension or `--surface desktop` for Codex Desktop. These installer options do
not initialize project memory. New CLI/VS Code installs require the selected
family's official standalone CLI; an extension-only install must prepare that
prerequisite through authorized onboarding, not a hidden runtime fallback.

Run the following on the VPS in its project checkout, in separate steps:

```bash
dduo-solo-founder remote-preflight
dduo-solo-founder init --headless --yes
```

With no embeddings key, the first `init` creates the descriptor and exits with
status `5` and `setup_required`. That is the expected private-configuration
checkpoint, not a failed database restore. Then complete private input and
resume initialization:

```bash
dduo-solo-founder configure-openai
dduo-solo-founder login-codex --device-auth
# Or use Claude for this project's sleep:
# dduo-solo-founder login-claude
dduo-solo-founder init --headless --yes
dduo-solo-founder remote-host --public-ip <PUBLIC-IP>
```

`configure-openai` hides the key as you type. Choose one supported sleep login:
Codex uses the project's private subscription credential store and Claude uses
its private project configuration. Install the selected client on the server.
Do not paste keys, login
codes or the initial manager token into chat, Git or logs. An uninitialized
project has no dDuo session to put off record. The resumed `init` creates the
database Project row; `start` alone cannot replace it. Connect the workstation
only after successful hosting, using the private manager credential and
`remote-bind`. See [Remote projects and teams](remote-teams.md).

For an existing project being moved, use the strict
[authority transfer procedure](backup-and-recovery.md#move-an-authoritative-project-to-another-vps)
instead of creating a new identity with `init`.

## The dashboard says Connection required

No verified sleep subscription is currently available on the memory host.
Changing an interactive account does not replace the isolated host login; a
saved login can still be revoked.
Setup now checks actual sleep failures as well as saved credentials. The plugin
reports the problem **in the current chat**, recommends opening configuration
to reconnect, and explicitly offers continuing temporarily as the alternative.
Existing memories and captured turns are retained; a paused sleep
does not by itself mean that capture or retrieval has stopped.

After choosing to fix it, select **Connect** in Setup and complete official
sign-in for Codex or Claude in that project's private host state. One completed,
verified subscription permits resuming sleep jobs; opening Setup alone never resumes them. Failed
resume requests offer an explicit retry. Scheduled means queued, not recovered:
verify a successful consolidation. No host credentials are copied or accounts
silently switched during reconnection. On a headless host, the infrastructure
manager runs `dduo-solo-founder login-codex --device-auth` or
`dduo-solo-founder login-claude` in the server project checkout. Pending memory jobs remain durable and interactive work can continue
while this happens. Remote collaborators ask the infrastructure manager to
reconnect on the server; they do not change their local account or start Docker.

## The dashboard says Temporary usage limit

The configured sleep executor has reached a temporary subscription limit. No
login is required, no different provider is used, and no paid generative API
fallback occurs. The job retries after the displayed time. **Sleep now** is
available when an earlier retry is appropriate.

## The dashboard says Memory will retry

Docker, the local agent, or a provider process was briefly unavailable. The
turn is still in PostgreSQL or the private local hook spool and will replay
on the next healthy prompt, session, or retry window. Continue working.

## A turn was created while Docker was unavailable

The Stop hook stores the complete prompt and response in a private local spool
file. SessionStart and UserPromptSubmit replay it idempotently before opening a
new turn. The dashboard will catch up once the local stack is healthy.

The same rule applies to a remote outage: the client preserves the turn for the
approved remote binding. It never starts a local Docker substitute for that
project.

## Remote memory is unavailable

Continue project work. dDuo fails open for the assistant, preserves completed
turns in its private project/binding spool and retries the same authoritative
HTTPS endpoint later. It deliberately does not search loopback, start Docker or
create a second local memory authority. `dduo-solo-founder status` reports the
configured endpoint and whether it is reachable; `doctor` provides the fuller
technical check.

If this client previously received the shared manual from an authenticated
response, SessionStart and UserPromptSubmit inject that last verified copy with
an explicit warning that current memory and Work may be stale or absent. The
same copy is available to `get_project_manual`. Its private HMAC-authenticated
cache is bound to the exact project, checkout binding and device credential;
tampering, copying it to another binding, unsafe permissions, a revoked token
or a required client upgrade never bypass server authority. If no valid copy
exists, dDuo emits only the unavailable-state notice and invents no procedures.

Confirm that the VPS is running, the public IP is unchanged and TCP `443` is
permanently open for ACME issuance and renewal. The first project normally uses
443; additional projects also require their assigned port between 24443 and
25442. The gateway keeps a challenge listener on 443 even after the original
443 project is removed. An endpoint must have a valid HTTPS certificate for the
IP. Do not replace `https://` with `http://` or edit the project descriptor to
point to a different stack.

## A remote binding asks for approval or has no credential

Remote approval is intentionally tied to the exact canonical repository root,
project ID, API URL and dashboard URL. Copying
`.dduo-solo-founder/project.toml` alone is insufficient. The device token must
remain in its private `0600` file outside Git.

`remote-join` may promote a local descriptor only when its project ID matches
the invitation exactly. It will not replace another project or an existing
remote endpoint. After a VPS/IP change, use `remote-rebind`: it validates the
same project at the new endpoint and reuses the private local token without
printing it. Reserve `remote-bind --replace-existing` for an infrastructure
manager who has independently verified a deliberate authority move.

## An invitation is expired or already consumed

The code is project-specific, single-use and valid for 1 to 168 hours. Ask the
infrastructure manager to create a new prompt with `team-invite`. A
consumed invitation is idempotent only when retried with the same device token;
it cannot enroll a different device. No invitation grants Git or VPS access.

## The remote dashboard says unauthorized

Use the complete link dDuo returned in chat: its browser token is reusable for
seven days, including from a new browser. A plain URL without that token needs
an existing browser session. For an old or expired link, ask the agent for a new
one (`get_dashboard_link`), or use `dduo-solo-founder dashboard --tab tasks`.
The page signs in automatically and keeps the requested Task/Plan selected.
No project reconfiguration is needed. Do not manually append a device token to
a URL. If access was revoked, the
infrastructure manager must issue a new authorized membership rather than
sharing another person's token.

## The project is read-only during transfer

`remote-transfer-prepare` intentionally changes the authority to
`transfer_pending` before the final backup, so ordinary mutations return a
conflict. The first `remote-host` on the restored destination intentionally
stays read-only and prints an `activation_receipt`. If the move is abandoned at
that point, discard the clone and run
`remote-transfer-cancel --new-node-not-activated` on the old host. If the move
should continue, pass that receipt to
`remote-transfer-retire --activation-receipt '<RECEIPT>' --destination-api-url '<HTTPS-API-URL>' --yes`
on the source, using the API URL printed by `remote-host`. The source verifies
the public certificate and complete HTTPS route to the exact read-only project
before finalizing; a failure blocks retirement and volume cleanup. Do not
disable TLS verification to bypass a certificate or routing error.
It prints a `finalization_receipt`; use that on the destination with
`remote-host --public-ip <PUBLIC-IP> --finalization-receipt '<RECEIPT>'` to make the new generation
writable.

Do not cancel after source finalization. If retirement stopped during Docker
cleanup, rerun `remote-transfer-retire --yes`: its private marker preserves the
final receipt and resumes cleanup without contacting PostgreSQL. Retiring
deletes only the old project's isolated volumes; recovery remains possible from
the final archive and separately stored recovery key.

## `remote-host` says the persistent user service is unavailable

A VPS host must keep the shared dDuo agent alive across logout and reboot.
Follow the exact username printed by the command:

```bash
sudo loginctl enable-linger <VPS-USER>
systemctl --user status
```

Then rerun `remote-host`; do not start the bridge manually or continue with a
partially hosted project. dDuo checks this before password rotation, database
replacement or deployment promotion. If an update intentionally stopped the
agent, the next project start reactivates the still-enabled unit; a reboot also
starts it automatically.

`remote-preflight` checks the persistent Linux user service, Docker access and
VPS resources before project creation or transfer. Follow its exact failure:
the server needs at least 1 vCPU, 1 GiB physical RAM, 2 GiB active disk-backed
swap and 5 GiB free in Docker storage after swap. This Beta requires Docker
storage and the root filesystem to share one backing filesystem. Ensure swap
survives reboot. These are
VPS requirements, not workstation or Mac hardware minimums; the remote client
does not need a local Docker memory stack.

## Codex is not saving turns

Codex keeps hook trust as a native security boundary. Identify the active
surface first; if it is unknown, ask one concise question rather than assuming
Desktop from the client name or VS Code from its terminal.

- **Codex Desktop:** open **Settings > Hooks**, select **dDuo Solo Founder**, then
  choose **Review** and **Trust all**. After installing/updating, fully quit and
  reopen the app before opening a fresh chat.
- **Codex CLI:** start a new CLI session and use `/hooks` if that version offers
  native review. It is not a command to send to a graphical chat.
- **Codex VS Code extension:** reload the VS Code window and open a new graphical
  conversation. Its approval path is not verified; use only native approval
  actually offered by that extension. If no UI is available, stop IDE acceptance
  and report the limitation. Desktop or CLI approval does not verify the IDE.

Preserve the selected `--only`, `--surface`, profile and executable when rerunning
the installer's `--verify`. A successful runtime check is not evidence that the
IDE delivered lifecycle events; use the [acceptance record](vscode-lifecycle-validation.md).

If Setup reports that the dDuo lifecycle is incomplete instead of asking for
approval, do not keep clicking Trust. Update or reinstall dDuo, apply the matching
handoff above, and reopen Setup. The installer accepts the package only when Codex
discovers all three lifecycle events.

On Windows, the current installer uses native runtime `.exe` entrypoints and
Node-based lifecycle hooks. Missing `sh` or old npm/batch-launcher errors are
not an inherent limitation of the current adapter. Reinstall the intended
release, check that its selected Node and Codex executables are available,
apply the matching handoff above, and reopen Setup. Do not patch installed hooks into an
unverified shell workaround. If discovery still fails, retain the diagnostic
error without credentials and run `doctor` from the affected project.

## Setup says the Codex version is incompatible

The Codex hook manifest uses `additionalContextLimit=0` so Codex does not apply
a second truncation below dDuo's own 9,000-unit Founder Brief budget. The
supported runtime requires Codex 0.150.0 or newer. Update the Codex installation selected
by Setup, rerun the Setup check and follow the
[surface-specific handoff](installation.md#required-handoff); do not
edit the installed manifest manually. Claude does not use this field and its
hook schema remains unchanged.

## Founder Brief context is partial or omitted

This is an explicit budget outcome, not broken JSON. The complete automatic
Founder Brief is capped at 9,000 client-safe units, measured as the greater of
Unicode code points and UTF-16 units. Every emitted line remains a complete JSON
value. Stable priority decides which items are full, partial or omitted; lower
priority data is not used merely to fill a gap left by a larger important item.

A partial memory contains an authored excerpt and a `full_memory` pointer. Use
`explain_memory` with that memory ID when the complete text, sources or revision
chain is materially necessary. An omitted memory or task remains authoritative
in PostgreSQL and can still be requested directly. Do not increase a client
hook limit to compensate: Codex already delegates the limit to dDuo and the
Claude payload is below 10,000 units.

An identical `SessionStart` payload after native context compaction is expected.
The model can no longer rely on its earlier inline context, so dDuo deliberately
rehydrates the bounded Founder Brief. Observability gives the new delivery its
own occurrence while retaining the same content hash for comparison.

Ordinary prompt hooks should usually appear as **delta** and omit unchanged
manual/profile/Work records. **Snapshot** is expected at SessionStart or when
the private delivery baseline is missing, corrupt, incompatible, or lacks a
native session identifier. **Fallback** means the live source or safe composer
was unavailable. “Stable context reused” is the exact prior JSONL
representation retained by the live session, not a claim about provider cache
tokens.

## Codex asks to connect Claude, or Claude asks to connect Codex

One supported subscription is enough. The first supported project chat sets a
Codex or Claude preference, while `source_client` remains provenance. Before a
sleep pass the memory host verifies that preference and can use the other
already-verified host subscription only when the preferred login or executable
is unavailable before model output. It never switches subscriptions to bypass a
rate limit, timeout or invalid output. Open a fresh chat after updating if Setup
still presents the old Codex-only wording; do not delete queued jobs or change
their provider by hand.

## The Work dashboard opens the project picker

Use the Work link returned by dDuo after a task change. It includes both the
project UUID and the Work tab. If you opened a bare dashboard URL, paste the
project UUID from that link into the picker once.

## Work appears empty after an update or restore

Check the selected project and view: Sprint shows the active sprint, Backlog
contains unfinished tasks without a sprint, and History contains archived
sprint work and unsprinted tasks with status `done` or `cancelled`. Planned sprints do not become
active automatically. Existing work is not assigned to a new sprint during
upgrade or restore. Backup recovery preserves membership and immutable
closure history; it does not decide where work should go next.

If a sprint action reports a version conflict, refresh the sprint or closure
preview and repeat the intended action against current data. A project can
have only one active sprint. Closing a sprint keeps completed outcomes in
History and moves unfinished work to the explicit planned destination or
Backlog; it does not mark that work done. Check task IDs and view filters
before creating duplicate tasks or restoring again.

## Observability values are unavailable, estimated, mixed, or empty

| Label | Meaning |
| --- | --- |
| **Reported** | Usable counters returned by the provider or CLI. |
| **Estimated** | Documented deterministic estimate, not exact provider consumption. |
| **Unavailable** | Missing, invalid or uncorrelated data; never interpreted as zero or an operation not running. |
| **API equivalent** | Hypothetical API price for subscription usage, not a bill or remaining quota. |
| **Mixed/unknown** | A client estimate cannot identify a single model. |
| **System / legacy** | Background or older events without an attributable member; not another memory store. |

Keep embedding attributable cost separate from interactive/sleep API equivalents
and keep provider/model rows separate. Unknown prices stay unavailable; a known
subtotal is not a complete bill. Client-computed Claude estimates can use
configured rates. See [pricing](pricing.md) for snapshots and cache semantics.

Claude samples require its status-line adapter. If existing status-line output
breaks, repair telemetry from Setup rather than replacing `statusLine` by
hand. The adapter preserves the previous command and cannot block prompts.

Codex collects correlated numeric request usage at Stop. Unknown formats,
invalid session/baseline correlation, or a turn exceeding 512 MiB or 1,000
provider requests make its measurement unavailable while memory capture
continues. Temporary file/outbox failures retain a private content-free retry
source. Cache counters show quantities and weighted input coverage, not exact
cached text spans. Several badges indicate mixed measurement sources.

There is no historical backfill. Empty history before collection began is
expected; an older numeric event without an exact payload cannot show its
original text. **Inspect emitted context** loads the exact dDuo hook/MCP string
and linked turn/retrieval references. Its token estimate describes dDuo's
delivery, not the complete model prompt or provider consumption.

Founder Brief candidate/emitted sizes and included/partial/omitted references
explain the 9,000-unit budget. Omitted items remain fetchable directly.
`inline_expected` describes the delivery contract, not proof the model consumed
every byte. Stable context reused in a session is not provider cache usage.

Observability failures do not block work. If new operations ran but remain
absent, run `doctor` and check API/worker health without sharing project
content or credentials. Use the member filter for causal team attribution;
all members share the same project ledger.

## Task search is indexing or degraded

PostgreSQL still holds authoritative tasks. Exact reads, lists, CRUD and unique
title lookup remain available; conceptual search uses bounded lexical fallback,
not a dump of full tasks.

Indexing after update/restore is expected while Qdrant is reconciled. Valid
snapshot points are preserved; missing, stale or orphaned points are repaired
independently of the memory index. With `RESTORE_DEGRADED` or a pending index
after the outbox drains, run `doctor` and check API, worker, embeddings and
Qdrant. Do not overwrite healthy PostgreSQL to repair a derived index.

A collection epoch marker and exact cardinality guard detect deletion,
replacement or missing points before another paid query. Failed vector
queries also invalidate readiness. Startup reconciliation checks identities
and payloads as well, including an unrelated extra point offsetting a missing
one. See [Task semantic projection](architecture.md#task-semantic-projection).

## The project says the client must be updated

The server has detected an incompatible wire protocol. It cannot download or
activate code and its response is not an update source. Finish or stop the
current work safely, choose the intended official repository revision, run its
installer explicitly with the actual `--only` and `--surface`, complete Setup,
then follow the [surface-specific handoff](installation.md#required-handoff)
in the same project folder. Never
accept an executable URL, key or permission grant supplied by a
project response. See
[Client binding and explicit updates](client-binding-and-updates.md).

## Explicit technical repair

When the user explicitly asks for diagnosis, run `dduo-solo-founder doctor`
from the project folder. This is a diagnostic interface, not normal setup.
