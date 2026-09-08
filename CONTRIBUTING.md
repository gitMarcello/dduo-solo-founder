[**EN · English**](CONTRIBUTING.md) · [IT · Italiano](CONTRIBUTING.it.md)

# Contributing

dDuo Solo Founder is a Beta, project-isolated memory product. Keep changes
scoped, explain behavioral trade-offs, and add tests for every branch that can
affect persistence, client lifecycle, installation, recovery or project
isolation.

## Development setup

```bash
uv sync --extra dev --extra benchmark
uv run ruff check backend tests benchmarks scripts
uv run pytest
cd frontend && npm ci && npm run lint && npm test && npm run build
docker compose config
```

Python tests enforce at least 95% coverage with branch measurement enabled.
The frontend has independent statement, function, line and branch thresholds.
Installer changes must retain dry-run, isolated-home install/uninstall,
transaction rollback, legacy migration and checksum tests. Run
`node scripts/generate-checksums.mjs` only after the final source change.

The native OS matrix exercises the locked runtime installer, update/rollback,
MCP, hooks and synthetic authentication/sleep on Linux, macOS and Windows.
Preserve native Windows `.exe`/`.cmd` dispatch and Node hook coverage; do not
substitute a POSIX-only dry run for the native installation. Headless VPS
tests cover `remote-preflight`, `init --headless`, private Codex login and the
separation between host bootstrap and remote workstation binding.

Sprint changes must preserve a single active Sprint, explicit task placement,
optimistic version checks, idempotent mutation receipts, and compact immutable
closure metadata. Never auto-assign legacy tasks or rewrite past closure
outcomes. Test placement and project isolation in exact, paginated and semantic
reads, including completed Plans and versioned history.

External embedding changes should also pass `./scripts/e2e-live.sh` with a
maintainer-owned OpenAI key. The `Live OpenAI E2E` workflow provides the same
gate through a protected repository secret.

Backup changes must test authenticated encryption, malformed archives, complete
hash verification, PostgreSQL catalog validation, destination failure, write
generation races and retention before altering the recovery contract. Never
use a real project archive as a fixture. Restore changes must prove that
destructive operations happen only after authentication and explicit consent.

Sleep changes must preserve the single structured-batch contract, source-ID
validation, single-current-revision invariant, task isolation and the
no-generative-API fallback rule. Tests must distinguish source-client
provenance from the project-owned sleep executor and never consume a
maintainer's subscription in the default suite. A manual prerelease check
should run one real Codex chat and one Claude chat through the same project
executor before tagging.

## Agent Plugins contract

The root `plugin.json`, `skills/` and `mcp.json` are the portable Agent Plugins
1.0 surface. The Python application and Docker stack are the shared runtime;
do not move project data into plugin packaging. `PLUGIN_ROOT` may locate
packaged, read-only resources. `PLUGIN_DATA` must not become authoritative
memory, Work state, credentials or cross-client coordination state.

Codex and Claude Code are the only supported Beta clients. Keep their native
lifecycle manifests as thin adapters over the same runtime and MCP server. A
change to a root standard manifest, either client adapter, hook execution
setting, client identity check or MCP launch command is a compatibility and
permission-boundary change and needs focused review and tests. Do not claim
support for another Agent Plugins client merely because it can parse the root
manifest; it must have an explicit supported adapter and lifecycle test first.

Every configured repository must continue to resolve exactly one isolated
project authority, local or remote. A remote binding must never create or query
a local fallback. No installer, migration or convenience path may make one
project inherit another project's identity, secrets, PostgreSQL database,
Qdrant collections or device credential.

## Beta releases

Users explicitly choose the revision to install or update. The project API
cannot activate client code. The installer verifies checksums and locked
dependencies, then changes runtime and adapter transactionally; failure must
leave the prior installation usable.

Before tagging a Beta release:

1. update every version surface, the changelog and the curated bilingual
   `.github/release-notes/<VERSION>.md` presentation; publication reads the
   notes from the verified source archive, not from generated PR summaries;
2. regenerate `checksums.sha256` after the final source change;
3. run the complete backend, frontend, Compose, package and installer gates;
4. validate `plugin.json`, `mcp.json` and the bundled skill;
5. smoke-test the packaged installer in an isolated home and confirm rollback
   coverage for updates and transactional legacy-secret migration.

Beta acceptance covers clean installation, update, a new Codex chat and a new
Claude session. Automated native tests use synthetic accounts: report real
subscription, consent and public VPS checks separately for each OS actually
tested. Setup validates deployment prerequisites; stable promotion still
requires real-client acceptance checks.

`VERSION` must match the tag without its leading `v`; build immutable artifacts
from that reviewed commit. After updating, fully restart Codex and open a new
chat, or start a new Claude session, to reload plugins, hooks and MCP.

## Pull requests

- `main` is protected. Never push to it directly, including as an administrator.
- Work on a short-lived branch, open a pull request, and keep it up to date with
  `main`.
- Merge only after the required `CI gate` succeeds. It aggregates the complete
  Python matrix, including Windows, plus frontend, browser and Compose checks.
- The solo-founder workflow requires no approval count, but it never bypasses a
  failed or pending CI result.
- Describe the user-visible behavior and failure modes.
- Add an Alembic migration for every database schema change.
- Never commit credentials, project memories, Docker volumes or benchmark keys.
- Preserve compatibility for Codex and Claude unless the change explicitly
  documents a staged migration.
- Keep external actions outside dDuo Solo Founder; the active agent owns them.

Use conventional, imperative commit subjects. Security reports belong in the
private process described in [SECURITY.md](SECURITY.md), not public issues.
