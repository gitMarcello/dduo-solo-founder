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
import subprocess
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
    from dduo_solo_founder.backup import verify_archive

    project_id = os.environ["DDUO_SOLO_FOUNDER_PROJECT_ID"]
    api_port = int(os.environ["DDUO_SOLO_FOUNDER_API_PORT"])
    web_port = int(os.environ["DDUO_SOLO_FOUNDER_WEB_PORT"])
    key = Path(os.environ["DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE"]).read_text().strip()

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
        print("Production restore CLI passed; encrypted archive, recovery proof, history replay verified.")


if __name__ == "__main__":
    main()
