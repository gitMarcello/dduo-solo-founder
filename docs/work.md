[**EN · English**](work.md) · [IT · Italiano](work.it.md)

# Work: Sprint, Backlog and History

Work organizes current execution separately from future work and completed
history. It preserves task identity, status, evidence, attachments and revision
history when placement changes. A Sprint is a time-bounded work grouping with
a title and objective; it is not another project or a separate memory.

## Choose the right view

| View | Content |
| --- | --- |
| Current sprint | Tasks explicitly assigned to the one active Sprint |
| Backlog | Unfinished tasks without a Sprint |
| Archive / History | Archived Sprints and completed or cancelled tasks without a Sprint |
| All work | Project-wide task inventory with explicit filters |
| Epics | Project-wide initiatives, independent of Sprint placement |
| Plans | Versioned design/decision documents; completed and superseded Plans remain available in history |

Board and List are presentations of the selected work scope. Switching a view
does not assign, close, move or delete tasks. A task's execution status and
Sprint placement are different fields. An Epic can span several Sprints;
assign its concrete tasks, not the Epic itself, to a Sprint.

Unfinished unsprinted tasks appear in Backlog; unsprinted `done` or `cancelled`
tasks appear in History. An empty Current sprint view means no tasks are
assigned to an active Sprint, not lost work. Updates invent no past Sprints.

## Plan and start a Sprint

Create a planned Sprint with a useful title and objective. Select its tasks
explicitly from Backlog or another permitted placement. Starting it changes
the Sprint from `planned` to `active`; each project can have only one active
Sprint. Starting another one requires resolving the current Sprint first.
Creating or starting a Sprint does not automatically assign all open work.

The assistant uses the smallest relevant Plan, Epic or Task for the request.
It can execute a clear user request without another confirmation for minimal
necessary Work. Proposing an adjacent initiative does not authorize a larger
scope. Use human titles and project-specific deep links in the conversation.

## Close with a preview

Before closing a Sprint, inspect the close preview: title, current version,
total, completed/cancelled and unfinished counts. Select the destination for
unfinished tasks: Backlog or another planned Sprint. Closing archives the
Sprint, keeps completed tasks with it, moves only its unfinished tasks to the
chosen destination, and preserves execution status.

Closure preserves immutable compact task metadata, outcome and destination;
full descriptions and evidence stay in task revisions. Later changes do not
rewrite closure history: a carried task remains an unfinished outcome of its
old Sprint. History can select a closure version separately from the live task.

The server validates the preview's `expected_version` under the same project
lock used by task writes. If work changed meanwhile, refresh the preview and
review it again. Mutations use an `idempotency_key`: retrying the same request
reuses its result; reusing that key for a different request is rejected.
These safeguards prevent a retry from creating duplicate Sprint operations.

Reopening an archived Sprint makes it planned. It does not pull carried tasks
back from their new destinations, erase old closure snapshots or start a
second active Sprint. Reassign work explicitly if that is the intended change.

## Organize older completed work

Legacy History remains readable without importing it into Sprints. To group
older completed work deliberately, select specific unsprinted tasks from this
project with status `done` or `cancelled`, review the selection and create a
historical Sprint. This creates an archived group and closure snapshots; it
does not infer dates, completion evidence or past Sprint membership. Unfinished
tasks, Epics and tasks already assigned to a Sprint are not valid selections.
There is no automatic historical task reassignment.

## Find details without loading the archive

Open a known task or Plan directly. For an ambiguous request, search once and
reuse the chosen ID. Compact cards provide status, priority, objective, next
action and version; request working/full detail for descriptions, dependencies,
evidence and extracted attachments only when needed. A known snapshot hash can
avoid retransmitting unchanged detail while preserving the returned identity.

Lists and history expose scope/placement, totals, limit and offset. Task search
uses a cursor and returns `next_cursor` for further matches; it does not provide
an inventory total. Read all list pages for a complete inventory; a search page
is not the whole archive.
Exact reads and PostgreSQL remain authoritative. Semantic search validates
current project, task version and placement before returning a match; degraded
indexing cannot silently place historical work in the current Sprint.

The automatic brief summarizes the active Sprint and relevant work without
injecting the complete archive on every turn. History and full evidence remain
available through explicit reads. PostgreSQL backups include Sprint membership,
mutation receipts and closure snapshots; restore does not reinterpret them.
See [memory engine](memory-engine.md) and [backup and recovery](backup-and-recovery.md).
