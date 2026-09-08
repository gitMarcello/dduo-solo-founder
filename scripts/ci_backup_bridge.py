#!/usr/bin/env python3
"""Serve the smallest valid host supplement for the Compose CI backup smoke test."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def supplement_file(path: str, content: bytes) -> dict[str, str]:
    return {
        "path": path,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_payload(project_id: str) -> bytes:
    payload = {
        "version": 1,
        "project_id": project_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "files": [
            supplement_file("secrets/dduo.env", b"OPENAI_API_KEY=ci-placeholder\n"),
            supplement_file(
                "secrets/codex/auth.json",
                b'{"tokens":{"access_token":"ci-placeholder"}}',
            ),
        ],
        "codex_auth": {"included": True, "credential_store": "file"},
        "credentials_complete": True,
        "warnings": [],
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def handler(project_id: str, token: str) -> type[BaseHTTPRequestHandler]:
    payload = build_payload(project_id)

    class BackupBridgeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            parsed = urlparse(self.path)
            authorized = self.headers.get("Authorization") == f"Bearer {token}"
            requested_project = parse_qs(parsed.query).get("project_id", [])
            if not authorized:
                self.send_error(401)
                return
            if parsed.path != "/v1/backup/supplement" or requested_project != [project_id]:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return BackupBridgeHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(
        ("0.0.0.0", args.port),
        handler(args.project_id, args.token),
    ).serve_forever()


if __name__ == "__main__":
    main()
