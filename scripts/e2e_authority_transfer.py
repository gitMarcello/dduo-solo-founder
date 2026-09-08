#!/usr/bin/env python3
"""Verify PostgreSQL migrations, Sprint restore, and authority handoff in isolation."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "99999999-9999-4999-8999-999999999991"
AUTHORITY_SECRET = "ci-authority-secret-with-enough-entropy-for-handoff"
EXPECTED_SCHEMA_REVISION = "f3b4c5d6e7f8"


def run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def compose(
    name: str,
    environment: dict[str, str],
    override: Path,
    *arguments: str,
) -> None:
    run(
        [
            "docker",
            "compose",
            "-p",
            name,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
            *arguments,
        ],
        environment=environment,
    )


def response(
    base: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    outgoing = {"Content-Type": "application/json", **(headers or {})}
    try:
        with urlopen(
            Request(f"{base}{path}", data=encoded, headers=outgoing, method=method),
            timeout=20,
        ) as response:
            status = response.status
            payload = response.read()
    except HTTPError as exc:
        status = exc.code
        payload = exc.read()
    return status, json.loads(payload) if payload else {}


def request(
    base: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    expected: int = 200,
) -> dict:
    status, payload = response(base, method, path, body=body, headers=headers)
    if status != expected:
        raise RuntimeError(
            f"{method} {path} returned {status}, expected {expected}: "
            f"{json.dumps(payload)[-1_000:]}"
        )
    return payload


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def database_snapshot(name: str, environment: dict[str, str], override: Path) -> dict:
    """Read durable rows directly so API projections cannot hide restore losses."""
    tables = {
        "sprints": "id",
        "tasks": "id",
        "plans": "id",
        "plan_work_items": "plan_id, position, task_id",
        "task_revisions": "task_id, version",
        "plan_revisions": "plan_id, version",
        "sprint_task_snapshots": "sprint_id, closure_version, task_id",
        "sprint_mutations": "project_id, idempotency_key",
    }
    fields = [
        "'revision', (SELECT version_num FROM alembic_version)",
        "'postgres_major', current_setting('server_version_num')::int / 10000",
    ]
    fields.extend(
        f"'{table}', (SELECT coalesce(json_agg(row), '[]'::json) "
        f"FROM (SELECT * FROM {table} ORDER BY {order}) row)"
        for table, order in tables.items()
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            name,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
            "exec",
            "-T",
            "postgres",
            "psql",
            "--no-psqlrc",
            "-U",
            "dduo_solo_founder",
            "-d",
            "dduo_solo_founder",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            "SELECT json_build_object(" + ", ".join(fields) + ")",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    snapshot = json.loads(result.stdout)
    require(snapshot["postgres_major"] == 16, "smoke must run against real PostgreSQL 16")
    require(
        snapshot["revision"] == EXPECTED_SCHEMA_REVISION,
        "full migration chain did not reach the expected schema head",
    )
    return snapshot


def seed_sprint_lifecycle(base: str) -> dict:
    path = f"/projects/{PROJECT_ID}"
    receipts = []

    def mutate(method: str, route: str, body: dict) -> dict:
        body = {**body, "idempotency_key": f"authority-smoke-{len(receipts)}"}
        result = request(base, method, route, body=body)
        receipts.append((method, route, body, result))
        return result

    current = mutate("POST", path + "/sprints", {"title": "Release Sprint"})
    following = mutate("POST", path + "/sprints", {"title": "Following Sprint"})
    route = path + f"/sprints/{current['id']}"
    current = mutate(
        "PATCH",
        route,
        {"expected_version": current["version"], "objective": "Preserve closed work on restore"},
    )
    current = mutate("POST", route + "/start", {"expected_version": current["version"]})
    require(current["status"] == "active", "Sprint did not start")
    epic = request(base, "POST", path + "/tasks", body={"title": "Release epic", "kind": "epic"})
    tasks = {}
    for status in ("done", "blocked", "cancelled"):
        tasks[status] = request(
            base,
            "POST",
            path + "/tasks",
            body={
                "title": f"Release work: {status}",
                "status": status,
                "sprint_id": current["id"],
                "epic_id": epic["id"],
            },
        )
    links = [tasks["blocked"]["id"], epic["id"], tasks["done"]["id"]]
    plan = request(
        base,
        "POST",
        path + "/plans",
        body={"title": "Release plan", "work_item_ids": links},
    )
    plan = request(
        base,
        "PATCH",
        path + f"/plans/{plan['id']}",
        body={"status": "executing", "expected_version": plan["version"]},
    )
    preview = request(base, "GET", route + "/close-preview")
    require(
        (preview["total"], preview["completed_count"], preview["unfinished_count"]) == (3, 2, 1),
        "close preview did not preserve the three execution outcomes",
    )
    archived = mutate(
        "POST",
        route + "/archive",
        {
            "expected_version": preview["sprint"]["version"],
            "unfinished_destination": "sprint",
            "destination_sprint_id": following["id"],
        },
    )
    first_history_path = route + f"/tasks?closure_version={archived['archive_version']}"
    first_history = request(base, "GET", first_history_path)
    outcomes = {row["id"]: row for row in first_history["items"]}
    for status, task in tasks.items():
        historical = outcomes[task["id"]]
        require(historical["outcome"] == status, f"closure lost the {status} outcome")
        require(historical["sprint_id"] == current["id"], "closure lost original Sprint placement")
        require(
            historical["destination_sprint_id"]
            == (following["id"] if status == "blocked" else current["id"]),
            "closure recorded the wrong destination",
        )
        live = request(base, "GET", path + f"/tasks/{task['id']}?detail=full")["task"]
        require(
            live["status"] == status and live["epic_id"] == epic["id"],
            "closing a Sprint changed task status or epic",
        )
        require(live["sprint_id"] == historical["destination_sprint_id"], "task was not carried")
        require(
            live["version"] == task["version"] + (status == "blocked"),
            "Sprint placement produced the wrong task revision",
        )
    require(
        request(base, "GET", path + f"/plans/{plan['id']}")["plan"] == plan,
        "Sprint closure changed the plan or its ordered links",
    )
    # A second closure must not rewrite the first closure's carry-over story.
    reopened = mutate("POST", route + "/reopen", {"expected_version": archived["version"]})
    require(reopened["status"] == "planned", "Sprint did not reopen to planned")
    closed_again = mutate(
        "POST",
        route + "/archive",
        {"expected_version": reopened["version"], "unfinished_destination": "backlog"},
    )
    require(
        closed_again["archive_version"] != archived["archive_version"],
        "second closure did not create a new history version",
    )
    require(
        request(base, "GET", first_history_path)["items"] == first_history["items"],
        "reopening and closing a Sprint rewrote earlier outcomes",
    )
    paths = [
        path + "/sprints",
        first_history_path,
        route + "/tasks",
        path + "/tasks?detail=full",
        path + f"/plans/{plan['id']}",
    ]
    return {
        "receipts": receipts,
        "views": {route: request(base, "GET", route) for route in paths},
        "carried_task_id": tasks["blocked"]["id"],
        "plan_id": plan["id"],
        "links": links,
    }


def verify_restored_sprints(base: str, seeded: dict) -> None:
    for route, expected in seeded["views"].items():
        require(request(base, "GET", route) == expected, f"restore changed API state: {route}")
    # Old expected versions intentionally replay after newer closures and transfer.
    for method, route, body, expected in seeded["receipts"]:
        require(
            request(base, method, route, body=body) == expected,
            f"restored Sprint mutation receipt did not replay: {route}",
        )


def verify_concurrent_sprint_start(base: str) -> None:
    path = f"/projects/{PROJECT_ID}/sprints"
    planned = [
        request(
            base,
            "POST",
            path,
            body={
                "title": f"Race Sprint {index}",
                "idempotency_key": f"authority-race-create-{index}",
            },
        )
        for index in range(2)
    ]
    barrier = Barrier(2, timeout=20)

    def start(item: dict) -> tuple[int, dict]:
        barrier.wait()
        return response(
            base,
            "POST",
            path + f"/{item['id']}/start",
            body={
                "expected_version": item["version"],
                "idempotency_key": f"authority-race-start-{item['id']}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(start, planned))
    require(
        sorted(status for status, _ in outcomes) == [200, 409],
        f"concurrent Sprint starts must yield one success and one conflict: {outcomes}",
    )
    active = request(base, "GET", path + "?status=active")
    require(active["total"] == 1, "concurrent start left more than one active Sprint")
    for item, (status, result) in zip(planned, outcomes):
        stored = request(base, "GET", path + f"/{item['id']}")
        if status == 200:
            require(
                stored == result and active["items"][0] == result,
                "winning Sprint start was not committed",
            )
            require(
                start_without_barrier(base, path, item) == result,
                "winning concurrent start receipt did not replay",
            )
        else:
            require(stored == item, "conflicting Sprint start was not rolled back")
    print("PostgreSQL concurrency passed: simultaneous Sprint starts returned exactly 200 + 409.")


def start_without_barrier(base: str, path: str, item: dict) -> dict:
    return request(
        base,
        "POST",
        path + f"/{item['id']}/start",
        body={
            "expected_version": item["version"],
            "idempotency_key": f"authority-race-start-{item['id']}",
        },
    )


def wait_for_health(base: str) -> None:
    for _ in range(90):
        try:
            request(base, "GET", "/health")
            return
        except (HTTPException, OSError, RuntimeError, URLError):
            time.sleep(1)
    raise RuntimeError(f"API did not become healthy: {base}")


def stack_environment(root: Path, *, api_port: int, web_port: int, node_id: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "DDUO_DATABASE_PASSWORD": "dduo_solo_founder",
            "OPENAI_API_KEY": "",
            "EMBEDDING_PROVIDER": "openai",
            "DDUO_CLI_BRIDGE_URL": "http://host.docker.internal:0",
            "DDUO_CLI_BRIDGE_TOKEN": "",
            "DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED": "false",
            "DDUO_SOLO_FOUNDER_API_PORT": str(api_port),
            "DDUO_SOLO_FOUNDER_WEB_PORT": str(web_port),
            "DDUO_SOLO_FOUNDER_BACKUP_SOURCE": str(root / "backups"),
            "DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE": str(root / "backup.key"),
            "DDUO_SOLO_FOUNDER_PROJECT_CONFIG_SOURCE": str(root / "project.toml"),
            "DDUO_SOLO_FOUNDER_PROJECT_ID": PROJECT_ID,
            "DDUO_NODE_ID": node_id,
            "DDUO_NODE_AUTHORITY_SECRET": AUTHORITY_SECRET,
        }
    )
    return environment


def prepare_files(root: Path) -> None:
    (root / "backups").mkdir(parents=True)
    (root / "backup.key").write_text("unconfigured\n", encoding="utf-8")
    (root / "project.toml").write_text(
        "\n".join(
            (
                "version = 1",
                f'id = "{PROJECT_ID}"',
                'name = "Authority transfer E2E"',
                "api_port = 1",
                "web_port = 2",
                "",
            )
        ),
        encoding="utf-8",
    )


def main() -> None:
    run_id = uuid.uuid4().hex[:12]
    source_name = f"dduo-authority-{run_id}-source"
    destination_name = f"dduo-authority-{run_id}-destination"
    for port in (18766, 18767):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"isolated smoke port {port} is unavailable") from exc
    api_image = os.getenv("DDUO_AUTHORITY_SMOKE_API_IMAGE", "dduo-authority-smoke-api")
    subprocess.run(
        ["docker", "image", "inspect", api_image],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="dduo-authority-e2e-") as temporary:
        root = Path(temporary)
        source_root = root / "source"
        destination_root = root / "destination"
        prepare_files(source_root)
        prepare_files(destination_root)
        override = root / "compose.authority.yaml"
        override.write_text(
            "\n".join(
                (
                    "services:",
                    "  api:",
                    f"    image: {api_image}",
                    "    build: null",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        source_env = stack_environment(
            source_root, api_port=18766, web_port=20766, node_id="node-old"
        )
        destination_env = stack_environment(
            destination_root, api_port=18767, web_port=20767, node_id="node-new"
        )
        source = "http://127.0.0.1:18766"
        destination = "http://127.0.0.1:18767"
        authority_headers = {"X-DDUO-Authority": AUTHORITY_SECRET}
        try:
            compose(source_name, source_env, override, "up", "-d", "postgres", "qdrant", "api")
            wait_for_health(source)
            request(
                source,
                "POST",
                "/projects",
                body={
                    "id": PROJECT_ID,
                    "name": "Authority transfer E2E",
                    "root_path": "/ci/source",
                },
            )
            request(
                source,
                "POST",
                f"/projects/{PROJECT_ID}/authority/initialize",
                body={"node_id": "node-old", "expected_generation": 1},
                headers=authority_headers,
            )
            seeded = seed_sprint_lifecycle(source)
            durable = database_snapshot(source_name, source_env, override)
            carried_revisions = [
                row
                for row in durable["task_revisions"]
                if row["task_id"] == seeded["carried_task_id"]
            ]
            require(
                [row["version"] for row in carried_revisions] == [1, 2],
                "carry-over did not persist exactly one new task revision",
            )
            require(
                all(row["snapshot"]["status"] == "blocked" for row in carried_revisions),
                "carry-over revision lost its blocked execution status",
            )
            require(
                [row["version"] for row in durable["plan_revisions"]] == [1, 2],
                "plan history did not persist both revisions",
            )
            require(
                all(
                    row["snapshot"]["work_item_ids"] == seeded["links"]
                    for row in durable["plan_revisions"]
                ),
                "plan revision lost its ordered task and epic links",
            )
            require(
                len(durable["sprint_mutations"]) == len(seeded["receipts"]),
                "Sprint mutation receipts were not persisted",
            )
            require(
                len(durable["sprint_task_snapshots"]) == 5,
                "both immutable Sprint closures were not persisted",
            )
            print(f"PostgreSQL 16 full migration chain passed through {durable['revision']}.")
            request(
                source,
                "POST",
                f"/projects/{PROJECT_ID}/authority/prepare"
                "?expected_generation=1&target_node_id=node-new",
            )

            compose(
                source_name,
                source_env,
                override,
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-Fc",
                "-U",
                "dduo_solo_founder",
                "-d",
                "dduo_solo_founder",
                "-f",
                "/tmp/authority.dump",
            )
            dump = root / "authority.dump"
            compose(
                source_name,
                source_env,
                override,
                "cp",
                "postgres:/tmp/authority.dump",
                str(dump),
            )
            compose(
                destination_name,
                destination_env,
                override,
                "up",
                "-d",
                "--wait",
                "postgres",
            )
            compose(
                destination_name,
                destination_env,
                override,
                "cp",
                str(dump),
                "postgres:/tmp/authority.dump",
            )
            compose(
                destination_name,
                destination_env,
                override,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--exit-on-error",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "-U",
                "dduo_solo_founder",
                "-d",
                "dduo_solo_founder",
                "/tmp/authority.dump",
            )
            compose(
                destination_name,
                destination_env,
                override,
                "up",
                "-d",
                "qdrant",
                "api",
            )
            wait_for_health(destination)
            activated = request(
                destination,
                "POST",
                f"/projects/{PROJECT_ID}/authority/activate",
                body={"node_id": "node-new", "expected_generation": 1},
                headers=authority_headers,
            )
            if activated.get("writable") is not False:
                raise RuntimeError("destination became writable before source finalization")
            finalized = request(
                source,
                "POST",
                f"/projects/{PROJECT_ID}/authority/finalize?expected_generation=1",
                body={"activation_receipt": activated["activation_receipt"]},
            )
            completed = request(
                destination,
                "POST",
                f"/projects/{PROJECT_ID}/authority/complete",
                body={
                    "node_id": "node-new",
                    "expected_generation": 1,
                    "finalization_receipt": finalized["finalization_receipt"],
                },
                headers=authority_headers,
            )
            if completed.get("state") != "active" or completed.get("generation") != 2:
                raise RuntimeError("destination did not own the next authority generation")
            require(
                database_snapshot(destination_name, destination_env, override) == durable,
                "PostgreSQL restore changed Sprint, task, plan, revision, or receipt rows",
            )
            verify_restored_sprints(destination, seeded)
            require(
                database_snapshot(destination_name, destination_env, override) == durable,
                "replaying restored Sprint receipts changed durable state",
            )
            print(
                "Sprint restore passed: lifecycle, two closure histories, statuses, ordered plan "
                "links, task/plan revisions, and all mutation receipts preserved."
            )
            verify_concurrent_sprint_start(destination)
            request(
                source,
                "POST",
                f"/projects/{PROJECT_ID}/tasks",
                body={"title": "source must remain fenced"},
                expected=409,
            )
            created = request(
                destination,
                "POST",
                f"/projects/{PROJECT_ID}/tasks",
                body={"title": "destination is authoritative"},
            )
            if created.get("title") != "destination is authoritative":
                raise RuntimeError("destination write verification failed")
            print("Authority E2E passed: one source frozen, one restored destination activated.")
        except Exception:
            for name, environment in (
                (source_name, source_env),
                (destination_name, destination_env),
            ):
                try:
                    compose(name, environment, override, "logs", "--tail", "100", "api")
                except subprocess.CalledProcessError:
                    pass
            raise
        finally:
            for name, environment in (
                (destination_name, destination_env),
                (source_name, source_env),
            ):
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-p",
                        name,
                        "-f",
                        str(ROOT / "compose.yaml"),
                        "-f",
                        str(override),
                        "down",
                        "-v",
                        "--remove-orphans",
                    ],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                )


if __name__ == "__main__":
    main()
