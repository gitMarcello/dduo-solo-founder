#!/usr/bin/env python3
"""Run the production restore CLI on an isolated, explicitly named test host.

The parent smoke owns the two Compose stacks. A real destination normally has
its own Docker engine; this adapter assigns a distinct Compose name and ports
because CI runs both hosts on one engine. No archive, database, HTTP response,
restore helper, credential validation, or recovery proof is mocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--compose-name", required=True)
    parser.add_argument("--compose-override", type=Path, required=True)
    args = parser.parse_args()
    # Import only after the parent has installed a disposable HOME. Every
    # module-level path (keys, registry, hooks, auth, settings) is then isolated.
    expected_home = args.project_root.parent / "home"
    if Path.home().resolve() != expected_home.resolve():
        raise RuntimeError("restore smoke refuses a non-disposable HOME")
    if not args.compose_name.startswith("dduo-authority-"):
        raise RuntimeError("restore smoke refuses a non-test Compose target")

    from typer.testing import CliRunner

    from dduo_solo_founder import launcher, project_config
    from dduo_solo_founder.backup import BackupError, verify_archive

    project_id = os.environ["DDUO_SOLO_FOUNDER_PROJECT_ID"]
    api_port = int(os.environ["DDUO_SOLO_FOUNDER_API_PORT"])
    web_port = int(os.environ["DDUO_SOLO_FOUNDER_WEB_PORT"])
    key = Path(os.environ["DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE"]).read_text().strip()
    postgres_image = os.environ["DDUO_SMOKE_POSTGRES_IMAGE"]
    postgres_platform = os.environ["DDUO_SMOKE_POSTGRES_PLATFORM"]
    postgres_version = os.environ["DDUO_SMOKE_POSTGRES_VERSION"]
    if not re.fullmatch(r"postgres@sha256:[0-9a-f]{64}", postgres_image):
        raise RuntimeError("restore smoke requires a pinned PostgreSQL digest")
    if postgres_platform not in {"linux/amd64", "linux/arm64"}:
        raise RuntimeError("restore smoke requires an explicit supported platform")
    native_run = subprocess.run
    standalone_images: list[str] = []
    drill_names: set[str] = set()
    verified_drills: set[str] = set()

    def pinned_postgres_run(command, **kwargs):
        # Test-host routing only: production currently launches its standalone
        # dump verifier and restore drill with a floating tag. Pin BOTH here;
        # do not let a cached 16.14 drill certify a 16.15 destination run.
        if isinstance(command, list) and command[:2] == ["docker", "run"] and "postgres:16-alpine" in command:
            command = list(command)
            command[command.index("postgres:16-alpine")] = postgres_image
            command[2:2] = ["--platform", postgres_platform]
            standalone_images.append(postgres_image)
            if "--name" in command:
                name = command[command.index("--name") + 1]
                if not name.startswith("dduo-restore-drill-"):
                    raise RuntimeError("unexpected standalone PostgreSQL target")
                drill_names.add(name)
        result = native_run(command, **kwargs)
        if (
            isinstance(command, list) and command[:2] == ["docker", "exec"]
            and command[2] in drill_names and command[2] not in verified_drills
            and "pg_restore" in command and result.returncode == 0
        ):
            name = command[2]
            client = native_run(
                ["docker", "exec", name, "psql", "--version"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            server = native_run(
                ["docker", "exec", name, "psql", "-U", "dduo", "-d", "dduo_restore_drill",
                 "-Atc", "SHOW server_version"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            if client.split()[2] != postgres_version or server.split()[0] != postgres_version:
                raise RuntimeError("standalone restore drill used an unexpected PostgreSQL version")
            verified_drills.add(name)
            print("Standalone restore drill evidence: " + json.dumps({
                "image": postgres_image, "platform": postgres_platform,
                "psql": client, "server": server,
            }), flush=True)
        return result

    verifier_version = native_run(
        ["docker", "run", "--rm", "--platform", postgres_platform, postgres_image,
         "pg_restore", "--version"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if verifier_version.split()[2] != postgres_version:
        raise RuntimeError("standalone dump verifier used an unexpected PostgreSQL version")
    print("Standalone dump verifier evidence: " + json.dumps({
        "image": postgres_image, "platform": postgres_platform, "pg_restore": verifier_version,
    }), flush=True)

    def isolated_compose(
        project: dict,
        *arguments: str,
        capture_output: bool = False,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        if str(project["id"]) != project_id:
            raise RuntimeError("restore tried to operate on another project")
        arguments = list(arguments)
        if arguments[:2] == ["up", "-d"] and arguments == ["up", "-d"]:
            # There are deliberately no sleep workers, provider calls, or web
            # builds in this data/authority smoke. The actual API still starts.
            arguments.extend(["postgres", "qdrant", "api"])
        return subprocess.run(
            [
                "docker", "compose", "-p", args.compose_name,
                "-f", str(ROOT / "compose.yaml"),
                "-f", str(args.compose_override), *arguments,
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=capture_output,
            text=capture_output or input_text is not None,
            check=False,
            **({"input": input_text, "encoding": "utf-8"} if input_text is not None else {}),
        )

    args.project_root.mkdir(parents=True, exist_ok=True)
    # The source bridge has already supplied an authenticated artificial host
    # envelope. Restore must not start a host-level background agent in CI.
    with (
        patch.object(launcher, "compose", isolated_compose),
        patch.object(launcher.subprocess, "run", pinned_postgres_run),
        patch.object(launcher, "compose_name", lambda _id: args.compose_name),
        patch.object(launcher, "ensure_cli_bridge", lambda: None),
        patch.object(project_config, "_available_port_pair", lambda *_a, **_kw: (api_port, web_port)),
    ):
        result = CliRunner().invoke(
            launcher.app,
            [
                "backup", "restore", str(args.archive), "--project-root", str(args.project_root),
                "--recovery-key", key, "--yes",
            ],
        )
        print(result.stdout)
        if result.exit_code != 0:
            raise RuntimeError("production backup restore CLI failed") from result.exception
        if len(standalone_images) < 2 or not verified_drills or drill_names != verified_drills:
            raise RuntimeError("production restore omitted the pinned dump verifier or restore drill")
        proof = args.project_root / launcher.DISASTER_RECOVERY_FILE
        if not proof.is_file():
            raise RuntimeError("production restore did not write its recovery proof")
        evidence = json.loads(proof.read_text())
        if evidence["project_id"] != project_id or not evidence["credentials_complete"]:
            raise RuntimeError("production restore proof does not match the archived project")
        # Retry the exact portable history through production code: the CLI
        # already did the first import; this must preserve every existing row.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="dduo-history-replay-") as temporary:
            extracted = Path(temporary)
            verify_archive(args.archive, key, extract_to=extracted)
            rows = launcher._portable_backup_history_rows(extracted, project_id)
            if not rows:
                raise RuntimeError("regression smoke requires nonempty portable history")
            def stored_rows() -> dict[str, dict]:
                result = isolated_compose(
                    {"id": project_id}, "exec", "-T", "postgres", "psql",
                    "-U", "dduo_solo_founder", "-d", "dduo_solo_founder", "-At", "-c",
                    "SELECT coalesce(json_agg(row), '[]'::json) "
                    "FROM (SELECT * FROM backup_records ORDER BY id) row",
                    capture_output=True,
                )
                result.check_returncode()
                return {row["id"]: row for row in json.loads(result.stdout)}

            first = stored_rows()
            for row in rows:
                stored = first.get(row["id"])
                if stored is None:
                    raise RuntimeError("restore omitted a portable history row")
                for field, expected in row.items():
                    actual = stored[field]
                    if field in {"created_at", "completed_at", "verified_at"} and expected:
                        expected = datetime.fromisoformat(expected)
                        actual = datetime.fromisoformat(actual)
                    if actual != expected:
                        raise RuntimeError(f"portable history field changed: {field}")
            replayed = launcher._restore_portable_backup_history(extracted, {"id": project_id})
            if replayed != len(rows):
                raise RuntimeError("portable history replay did not visit every row")
            if stored_rows() != first:
                raise RuntimeError("replaying portable history changed durable rows")
            # Source-generation is validated as a nonnegative Python integer,
            # but PostgreSQL's integer column has a narrower domain. A second
            # row that overflows SQL must roll back the first valid new row.
            # Only this private extracted test fixture is changed.
            rollback_rows = [deepcopy(rows[0]), deepcopy(rows[0])]
            for row in rollback_rows:
                row["id"] = str(uuid.uuid4())
            rollback_rows[1]["source_generation"] = 2**31
            history_path = extracted / "history" / "backup-records.json"
            original_history = history_path.read_bytes()
            try:
                history_path.write_text(json.dumps(rollback_rows), encoding="utf-8")
                try:
                    launcher._restore_portable_backup_history(extracted, {"id": project_id})
                except BackupError as exc:
                    if "psql" not in str(exc) or "exit code" not in str(exc):
                        raise RuntimeError("rollback fixture did not reach the real SQL command") from exc
                else:
                    raise RuntimeError("SQL error did not reject the invalid history batch")
                if stored_rows() != first:
                    raise RuntimeError("SQL error partially committed a history batch")
            finally:
                history_path.write_bytes(original_history)
        print("Production restore CLI passed; encrypted archive, recovery proof, history replay, "
              "and real SQL-error rollback verified.")


if __name__ == "__main__":
    main()
