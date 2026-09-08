"""Small, host-wide HTTPS gateway for isolated remote project stacks.

Project databases and vector stores remain separate Compose projects.  The only
shared component is Caddy, which terminates HTTPS for the VPS IP and proxies one
public port to each project's loopback-only dashboard.  The dashboard's nginx
continues to proxy ``/api`` to that project's private API container.
"""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from dduo_solo_founder.project_config import portable_file_lock


GATEWAY_VERSION = 1
GATEWAY_DIR = Path.home() / ".config" / "dduo-solo-founder" / "remote-gateway"
GATEWAY_REGISTRY = GATEWAY_DIR / "projects.json"
GATEWAY_CADDYFILE = GATEWAY_DIR / "Caddyfile"
FIRST_HTTPS_PORT = 443
ADDITIONAL_HTTPS_PORT_BASE = 24443
ADDITIONAL_HTTPS_PORTS = 1000
SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
SAFE_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$"
)


class RemoteGatewayError(RuntimeError):
    """Remote gateway configuration is invalid or unsafe."""


def public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError as exc:
        raise RemoteGatewayError("remote gateway host must be a public IP address") from exc
    if not address.is_global:
        raise RemoteGatewayError("remote gateway host must be a public IP address")
    return address.compressed


def _empty_registry() -> dict:
    return {"version": GATEWAY_VERSION, "public_ip": None, "email": None, "projects": {}}


def _validate_projects(projects: object) -> dict:
    if not isinstance(projects, dict):
        raise RemoteGatewayError("remote gateway registry has an unsupported format")
    web_ports: set[int] = set()
    https_ports: set[int] = set()
    for project_id, value in projects.items():
        if not isinstance(project_id, str) or not SAFE_PROJECT_ID.fullmatch(project_id):
            raise RemoteGatewayError("remote gateway registry contains an invalid project")
        if not isinstance(value, dict):
            raise RemoteGatewayError("remote gateway registry contains an invalid project")
        try:
            web_port = int(value["web_port"])
            https_port = int(value["https_port"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteGatewayError(
                "remote gateway registry contains an invalid project"
            ) from exc
        if (
            not 1 <= web_port <= 65535
            or not 1 <= https_port <= 65535
            or web_port in web_ports
            or https_port in https_ports
        ):
            raise RemoteGatewayError("remote gateway registry contains conflicting ports")
        web_ports.add(web_port)
        https_ports.add(https_port)
    return projects


def load_gateway_registry(path: Path | None = None) -> dict:
    path = path or GATEWAY_REGISTRY
    if not path.exists():
        return _empty_registry()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteGatewayError("remote gateway registry is unreadable") from exc
    if value.get("version") != GATEWAY_VERSION:
        raise RemoteGatewayError("remote gateway registry has an unsupported format")
    _validate_projects(value.get("projects"))
    return value


def _atomic_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _allocated_port(project_id: str, projects: dict) -> int:
    existing = projects.get(project_id)
    if existing:
        return int(existing["https_port"])
    used = {int(value["https_port"]) for value in projects.values()}
    if FIRST_HTTPS_PORT not in used:
        return FIRST_HTTPS_PORT
    start = int(hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8], 16)
    start %= ADDITIONAL_HTTPS_PORTS
    for offset in range(ADDITIONAL_HTTPS_PORTS):
        candidate = ADDITIONAL_HTTPS_PORT_BASE + ((start + offset) % ADDITIONAL_HTTPS_PORTS)
        if candidate not in used:
            return candidate
    raise RemoteGatewayError("remote gateway has no free HTTPS project port")


def register_gateway_project(
    project_id: str,
    name: str,
    web_port: int,
    host: str,
    *,
    email: str | None = None,
    registry_path: Path | None = None,
) -> dict:
    """Register or update one project while preserving every other stack."""
    registry_path = registry_path or GATEWAY_REGISTRY
    normalized_ip = public_ip(host)
    if not SAFE_PROJECT_ID.fullmatch(project_id) or not (1 <= int(web_port) <= 65535):
        raise RemoteGatewayError("project id and loopback web port are required")
    normalized_email = email.strip() if email else None
    if normalized_email and not SAFE_EMAIL.fullmatch(normalized_email):
        raise RemoteGatewayError("ACME account email is invalid")
    with portable_file_lock(registry_path):
        registry = load_gateway_registry(registry_path)
        configured_ip = registry.get("public_ip")
        if configured_ip and configured_ip != normalized_ip:
            raise RemoteGatewayError("this gateway is already bound to another public IP")
        for other_id, other in registry["projects"].items():
            if other_id != project_id and int(other["web_port"]) == int(web_port):
                raise RemoteGatewayError(
                    "another remote project already uses this loopback dashboard port"
                )
        registry["public_ip"] = normalized_ip
        if normalized_email:
            registry["email"] = normalized_email
        https_port = _allocated_port(project_id, registry["projects"])
        registry["projects"][project_id] = {
            "name": name.strip() or project_id,
            "web_port": int(web_port),
            "https_port": https_port,
        }
        _atomic_private_json(registry_path, registry)
    return {
        "project_id": project_id,
        "https_port": https_port,
        "api_url": f"https://{_url_host(normalized_ip)}:{https_port}/api",
        "dashboard_url": f"https://{_url_host(normalized_ip)}:{https_port}",
    }


def unregister_gateway_project(project_id: str, path: Path | None = None) -> bool:
    path = path or GATEWAY_REGISTRY
    with portable_file_lock(path):
        registry = load_gateway_registry(path)
        removed = registry["projects"].pop(project_id, None) is not None
        if removed:
            _atomic_private_json(path, registry)
        return removed


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def render_caddyfile(registry: dict) -> str:
    host = public_ip(str(registry.get("public_ip") or ""))
    _validate_projects(registry.get("projects"))
    email = str(registry.get("email") or "").strip()
    global_lines = ["{", "\tadmin off", "\tpersist_config off"]
    if email:
        global_lines.append(f"\temail {email}")
    global_lines.append("}")
    blocks = ["\n".join(global_lines)]
    assigned_ports = {
        int(project["https_port"]) for project in registry["projects"].values()
    }
    if FIRST_HTTPS_PORT not in assigned_ports:
        # TLS-ALPN certificate issuance and renewal always enter through 443,
        # even when every remaining project is exposed on an additional port.
        blocks.append(
            "\n".join(
                [
                    f"https://{_url_host(host)}:{FIRST_HTTPS_PORT} {{",
                    "\ttls {",
                    "\t\tissuer acme {",
                    "\t\t\tprofile shortlived",
                    "\t\t}",
                    "\t}",
                    "\trespond 404",
                    "}",
                ]
            )
        )
    for _, project in sorted(
        registry["projects"].items(), key=lambda item: int(item[1]["https_port"])
    ):
        https_port = int(project["https_port"])
        web_port = int(project["web_port"])
        blocks.append(
            "\n".join(
                [
                    f"https://{_url_host(host)}:{https_port} {{",
                    "\ttls {",
                    "\t\tissuer acme {",
                    "\t\t\tprofile shortlived",
                    "\t\t}",
                    "\t}",
                    "\tencode zstd gzip",
                    "\theader {",
                    '\t\tStrict-Transport-Security "max-age=31536000"',
                    '\t\tX-Content-Type-Options "nosniff"',
                    '\t\tReferrer-Policy "no-referrer"',
                    '\t\tX-Frame-Options "DENY"',
                    "\t\t-Server",
                    "\t}",
                    f"\treverse_proxy 127.0.0.1:{web_port}",
                    "}",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def write_caddyfile(
    registry_path: Path | None = None,
    destination: Path | None = None,
) -> Path:
    registry = load_gateway_registry(registry_path or GATEWAY_REGISTRY)
    if not registry["projects"]:
        raise RemoteGatewayError("remote gateway has no registered projects")
    destination = destination or GATEWAY_CADDYFILE
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(render_caddyfile(registry), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
