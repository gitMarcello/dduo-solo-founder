#!/usr/bin/env python3
"""Exercise actual snapshot descriptors with Linux rootful Docker and UID 1000.

Requires cached node:22-alpine and postgres:16-alpine (or explicit --*-image).
Builds an isolated Node + official Alpine docker-cli test image. No new daemon,
real project, registry, credentials, runtime or existing volume is touched.
The Docker socket grants daemon access; every test resource is UUID-qualified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "scripts" / "fixtures"


def docker(*args: str, timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, timeout=timeout,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-image", default="node:22-alpine")
    parser.add_argument("--postgres-image", default="postgres:16-alpine")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    info = json.loads(docker("info", "--format", "{{json .}}").stdout)
    if info["OSType"] != "linux" or any("rootless" in item for item in info["SecurityOptions"]):
        raise RuntimeError("This reproduction requires Linux rootful Docker, not rootless mode.")
    endpoint = os.environ.get("DOCKER_HOST") or docker(
        "context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}',
    ).stdout.strip()
    if not endpoint.startswith("unix://"):
        raise RuntimeError("This smoke requires a local Unix Docker socket, never a remote daemon.")
    images = {}
    for key, image in (("node", args.node_image), ("postgres", args.postgres_image)):
        inspected = json.loads(docker("image", "inspect", image).stdout)[0]
        images[key] = {
            "id": inspected["Id"], "platform": f'{inspected["Os"]}/{inspected["Architecture"]}',
            "digests": inspected.get("RepoDigests", []),
        }
    prefix = f"dduo-snapshot-smoke-{uuid.uuid4().hex[:12]}"
    source, output = f"{prefix}-source", f"{prefix}-output"
    installer_image = f"{prefix}:installer"
    label = f"dduo.snapshot-smoke={prefix}"
    volumes: list[str] = []
    image_built = False
    report = None
    cleanup_errors = []
    try:
        print("Building isolated Linux installer test image.", flush=True)
        with tempfile.TemporaryDirectory(prefix="dduo-snapshot-context-") as context:
            docker(
                "build", "--pull=false", "--build-arg", f"NODE_IMAGE={args.node_image}",
                "--label", label, "-t", installer_image,
                "-f", str(FIXTURES / "upgrade_snapshot.Dockerfile"), context, timeout=300,
            )
        image_built = True
        for volume in (source, output):
            docker("volume", "create", "--label", label, volume)
            volumes.append(volume)
        mountpoint = json.loads(docker("volume", "inspect", output).stdout)[0]["Mountpoint"]
        if not re.fullmatch(r"/[^\r\n]+/" + re.escape(output) + r"/_data", mountpoint):
            raise RuntimeError("Unexpected disposable volume mountpoint; refusing host bind mount.")
        seed = """
const fs = require('node:fs');
const crypto = require('node:crypto');
fs.mkdirSync('/source/private', { mode: 0o700 });
fs.writeFileSync('/source/PG_VERSION', '16\\n', { mode: 0o600 });
fs.writeFileSync('/source/private/unicode.txt', 'Caffè — memoria artificiale 🚀\\n', { mode: 0o600 });
fs.writeFileSync('/source/private/binary', crypto.randomBytes(2500000), { mode: 0o600 });
for (const path of ['/source', '/source/private', '/source/PG_VERSION',
 '/source/private/unicode.txt', '/source/private/binary']) fs.chownSync(path, 999, 999);
fs.chmodSync('/source', 0o700);
fs.chownSync('/output', 1000, 1000);
fs.chmodSync('/output', 0o700);
"""
        docker(
            "run", "--rm", "--network", "none", "--name", f"{prefix}-seed", "--label", label,
            "--user", "0:0", "-v", f"{source}:/source", "-v", f"{output}:/output",
            installer_image, "node", "-e", seed,
        )
        # Docker Desktop's host socket is forwarded to /var/run/docker.sock
        # inside its Linux daemon. On native Linux use the effective Unix path.
        socket = "/var/run/docker.sock" if info.get("OperatingSystem") == "Docker Desktop" else endpoint[7:]
        socket_gid = docker(
            "run", "--rm", "--network", "none", "--name", f"{prefix}-socket", "--label", label,
            "--user", "0:0", "-v", f"{socket}:/var/run/docker.sock:ro",
            installer_image, "stat", "-c", "%g", "/var/run/docker.sock",
        ).stdout.strip()
        if not socket_gid.isdigit():
            raise RuntimeError("Invalid Docker socket group.")
        print("Reproducing old EPERM and exercising production helper as UID 1000.", flush=True)
        result = docker(
            "run", "--rm", "--network", "none", "--name", f"{prefix}-installer", "--label", label,
            "--user", "1000:1000", "--group-add", socket_gid,
            "-v", f"{socket}:/var/run/docker.sock:ro",
            "-v", f"{output}:{mountpoint}",
            "-v", f"{ROOT / 'bin' / 'upgrade-snapshot.mjs'}:/test/upgrade-snapshot.mjs:ro",
            "-v", f"{FIXTURES / 'upgrade_snapshot.mjs'}:/test/smoke.mjs:ro",
            installer_image, "node", "/test/smoke.mjs", prefix, source, mountpoint,
            images["postgres"]["id"], timeout=300,
        )
        report = json.loads(result.stdout)
        report["images"] = images
        report["docker_daemon"] = {"os": info["OSType"], "rootless": False}
    except subprocess.CalledProcessError as exc:
        # Only test resource commands run here; don't print Docker info/account
        # metadata or expand to unrelated container logs for troubleshooting.
        raise RuntimeError(f"Isolated snapshot test command failed: {exc.stderr[-4000:]}") from exc
    finally:
        owned = docker(
            "ps", "--all", "--filter", f"label={label}", "--format", "{{.Names}}", check=False,
        )
        for name in owned.stdout.splitlines():
            if not name.startswith(f"{prefix}-"):
                cleanup_errors.append("Unexpected labeled resource; left intact.")
                continue
            if docker("rm", "--force", name, check=False).returncode:
                cleanup_errors.append(f"Container cleanup failed: {name}")
        for volume in reversed(volumes):
            if docker("volume", "rm", volume, check=False).returncode:
                cleanup_errors.append(f"Volume cleanup failed: {volume}")
        if image_built and docker("image", "rm", installer_image, check=False).returncode:
            cleanup_errors.append("Disposable installer image cleanup failed.")
        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))
    report["cleanup"] = "all disposable containers, volumes and installer image removed"
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
