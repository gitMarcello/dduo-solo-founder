"""Resolve one explicit local or remote client binding for a project checkout.

Remote credentials and approvals deliberately live outside repositories.  A
checked-in project file can therefore describe a remote endpoint, but hooks do
not contact it until the current user approves the exact root, project id and
endpoint fingerprint on this machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from dduo_solo_founder.project_config import (
    exclude_local_config,
    find_workspace_root,
    load_project,
    portable_file_lock,
    toml_string,
    validate_project_registration,
)


CONFIG_DIR = Path.home() / ".config" / "dduo-solo-founder"
REMOTE_APPROVALS_PATH = CONFIG_DIR / "remote-bindings.json"
REMOTE_CREDENTIALS_DIR = CONFIG_DIR / "remote-credentials"
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class BindingError(RuntimeError):
    """A project binding is invalid or cannot be used safely."""


class RemoteBindingApprovalRequired(BindingError):
    """The exact remote endpoint has not been approved for this checkout."""


@dataclass(frozen=True)
class ProjectBinding:
    project_id: str
    name: str
    root_path: Path
    kind: str
    api_url: str
    dashboard_url: str | None
    binding_id: str
    api_port: int | None = None
    web_port: int | None = None
    credential_path: Path | None = None
    bearer_token: str | None = None

    @property
    def remote(self) -> bool:
        return self.kind == "remote"

    def dashboard_link(
        self,
        tab: str = "tasks",
        view: str | None = None,
        *,
        work_id: str | None = None,
        plan_id: str | None = None,
    ) -> str | None:
        if self.dashboard_url is None:
            return None
        from dduo_solo_founder.project_config import (
            DASHBOARD_TABS,
            WORK_VIEWS,
            dashboard_item_url,
        )

        if tab not in DASHBOARD_TABS:
            raise ValueError(f"unsupported dashboard tab: {tab}")
        if view is not None and (tab != "tasks" or view not in WORK_VIEWS):
            raise ValueError("unsupported dashboard work view")
        values = {"project": self.project_id, "tab": tab}
        if view:
            values["view"] = view
        dashboard_url = f"{self.dashboard_url.rstrip('/')}/?{urlencode(values)}"
        if work_id or plan_id:
            return dashboard_item_url(dashboard_url, work_id=work_id, plan_id=plan_id)
        return dashboard_url


@dataclass(frozen=True)
class ProvisionalRemoteApproval:
    """One compare-and-swap lease for a provisional approval registry entry."""

    approvals_path: Path
    binding_id: str
    provisional_id: str
    previous_entry: dict | None
    registry_existed: bool
    credential_path: Path
    credential_sha256: str
    previous_credential_existed: bool
    previous_credential: bytes
    previous_credential_mode: int | None


def canonical_https_url(value: str, *, field: str) -> str:
    """Return one canonical HTTPS URL, allowing DNS names and IP literals."""
    if any(ord(character) < 32 for character in str(value)) or any(
        character in str(value) for character in ('"', "\\")
    ):
        raise BindingError(f"{field} contains unsafe characters")
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
    except ValueError as exc:
        raise BindingError(f"{field} is not a valid HTTPS URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise BindingError(f"{field} must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise BindingError(f"{field} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise BindingError(f"{field} must not contain a query or fragment")
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname + (f":{port}" if port is not None else "")
    path = (parsed.path or "").rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def _binding_digest(root: Path, project_id: str, api_url: str) -> str:
    material = "\0".join((str(root.resolve()), project_id, api_url)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def endpoint_fingerprint(
    project_id: str, api_url: str, dashboard_url: str | None = None
) -> str:
    material = "\0".join((project_id, api_url, dashboard_url or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _private_write(path: Path, value: str) -> None:
    _private_write_bytes(path, value.encode("utf-8"))


def _private_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(value)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _private_file_snapshot(path: Path) -> tuple[bool, bytes, int | None]:
    if not path.exists():
        return False, b"", None
    _require_private_file(path)
    return True, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _restore_private_file(
    path: Path,
    snapshot: tuple[bool, bytes, int | None],
) -> None:
    existed, content, mode = snapshot
    if not existed:
        path.unlink(missing_ok=True)
        return
    _private_write_bytes(path, content)
    if mode is not None:
        path.chmod(mode)


def _read_approvals(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "bindings": {}}
    _require_private_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BindingError(f"remote binding approvals are unreadable: {path}") from exc
    if value.get("version") != 1 or not isinstance(value.get("bindings"), dict):
        raise BindingError(f"remote binding approvals have an unsupported format: {path}")
    return value


def _credential_path(binding_id: str, directory: Path) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", binding_id):
        raise BindingError("invalid binding identifier")
    return directory / f"{binding_id}.token"


def _require_private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise BindingError(f"private dDuo state must be a regular file: {path}")
    if os.name == "nt":
        return
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise BindingError(f"private dDuo state has the wrong owner: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise BindingError(f"remote credential permissions are unsafe: {path}")


def binding_from_project(
    root: Path,
    project: dict,
    *,
    require_approval: bool = True,
    approvals_path: Path | None = None,
    credentials_dir: Path | None = None,
    enforce_project_claim: bool = False,
) -> ProjectBinding:
    """Normalize a local or remote project binding without endpoint fallback.

    Runtime adapters pass ``enforce_project_claim=True``. Setup, migration,
    restore, and explicit rebind code use the parser while establishing a new
    claim and must not accidentally contact the resulting endpoint.
    """
    root = find_workspace_root(root)
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        raise BindingError("project configuration has no id")
    if not SAFE_IDENTIFIER.fullmatch(project_id):
        raise BindingError("project id contains unsupported characters")
    name = str(project.get("name") or root.name)
    kind = str(project.get("binding") or "local").strip().lower()
    if kind == "local":
        if enforce_project_claim:
            try:
                validate_project_registration(project_id, root)
            except RuntimeError as exc:
                raise BindingError(str(exc)) from exc
        if project.get("api_url") or project.get("dashboard_url"):
            raise BindingError("local binding cannot contain remote URLs")
        try:
            api_port = int(project["api_port"])
            web_port = int(project["web_port"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BindingError("local binding requires API and dashboard ports") from exc
        if not (1 <= api_port <= 65535 and 1 <= web_port <= 65535):
            raise BindingError("local binding ports are out of range")
        binding_id = _binding_digest(root, project_id, f"local:{api_port}:{web_port}")
        return ProjectBinding(
            project_id=project_id,
            name=name,
            root_path=root,
            kind="local",
            api_url=f"http://127.0.0.1:{api_port}",
            dashboard_url=f"http://127.0.0.1:{web_port}",
            binding_id=binding_id,
            api_port=api_port,
            web_port=web_port,
        )
    if kind != "remote":
        raise BindingError("binding must be exactly local or remote")
    if project.get("api_port") is not None or project.get("web_port") is not None:
        raise BindingError("remote binding cannot contain local ports")
    api_url = canonical_https_url(str(project.get("api_url") or ""), field="api_url")
    dashboard_value = project.get("dashboard_url")
    dashboard_url = (
        canonical_https_url(str(dashboard_value), field="dashboard_url")
        if dashboard_value
        else None
    )
    endpoint_authority = "\0".join((api_url, dashboard_url or ""))
    binding_id = _binding_digest(root, project_id, endpoint_authority)
    approvals_path = approvals_path or REMOTE_APPROVALS_PATH
    credentials_dir = credentials_dir or REMOTE_CREDENTIALS_DIR
    credential_path = _credential_path(binding_id, credentials_dir)
    token: str | None = None
    if require_approval:
        approval = _read_approvals(approvals_path)["bindings"].get(binding_id)
        expected = {
            "root_path": str(root.resolve()),
            "project_id": project_id,
            "api_url": api_url,
            "dashboard_url": dashboard_url,
            "endpoint_fingerprint": endpoint_fingerprint(
                project_id, api_url, dashboard_url
            ),
        }
        if not isinstance(approval, dict) or any(approval.get(key) != value for key, value in expected.items()):
            raise RemoteBindingApprovalRequired(
                "remote binding requires local approval for this repository and endpoint"
            )
        if not credential_path.exists():
            raise RemoteBindingApprovalRequired("remote binding credential is missing")
        try:
            _require_private_file(credential_path)
            token = credential_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RemoteBindingApprovalRequired("remote binding credential is missing") from exc
        if not token:
            raise RemoteBindingApprovalRequired("remote binding credential is empty")
    return ProjectBinding(
        project_id=project_id,
        name=name,
        root_path=root,
        kind="remote",
        api_url=api_url,
        dashboard_url=dashboard_url,
        binding_id=binding_id,
        credential_path=credential_path,
        bearer_token=token,
    )


def load_binding(root: Path, **kwargs) -> ProjectBinding:
    resolved = find_workspace_root(root)
    kwargs.setdefault("enforce_project_claim", True)
    return binding_from_project(resolved, load_project(resolved), **kwargs)


def _approve_remote_binding(
    root: Path,
    token: str,
    *,
    approvals_path: Path | None = None,
    credentials_dir: Path | None = None,
    provisional_id: str | None = None,
) -> tuple[ProjectBinding, ProvisionalRemoteApproval | None]:
    normalized_token = str(token).strip()
    if not normalized_token:
        raise BindingError("remote credential cannot be empty")
    root = find_workspace_root(root)
    project = load_project(root)
    approvals_path = approvals_path or REMOTE_APPROVALS_PATH
    credentials_dir = credentials_dir or REMOTE_CREDENTIALS_DIR
    binding = binding_from_project(
        root,
        project,
        require_approval=False,
        approvals_path=approvals_path,
        credentials_dir=credentials_dir,
    )
    if not binding.remote or binding.credential_path is None:
        raise BindingError("only a remote binding can be approved")
    with portable_file_lock(approvals_path):
        registry_existed = approvals_path.exists()
        registry = _read_approvals(approvals_path)
        previous_entry = registry["bindings"].get(binding.binding_id)
        previous_credential = _private_file_snapshot(binding.credential_path)
        approval = {
            "root_path": str(root.resolve()),
            "project_id": binding.project_id,
            "api_url": binding.api_url,
            "dashboard_url": binding.dashboard_url,
            "endpoint_fingerprint": endpoint_fingerprint(
                binding.project_id, binding.api_url, binding.dashboard_url
            ),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        if provisional_id is not None:
            approval["provisional_id"] = provisional_id
        registry["bindings"][binding.binding_id] = approval
        credential_bytes = (normalized_token + "\n").encode("utf-8")
        try:
            _private_write(binding.credential_path, credential_bytes.decode("utf-8"))
            _private_write(
                approvals_path,
                json.dumps(registry, indent=2, sort_keys=True) + "\n",
            )
            approved = binding_from_project(
                root,
                project,
                approvals_path=approvals_path,
                credentials_dir=credentials_dir,
            )
        except BaseException:
            if isinstance(previous_entry, dict):
                registry["bindings"][binding.binding_id] = previous_entry
            else:
                registry["bindings"].pop(binding.binding_id, None)
            _restore_private_file(binding.credential_path, previous_credential)
            if not registry_existed and not registry["bindings"]:
                approvals_path.unlink(missing_ok=True)
            else:
                _private_write(
                    approvals_path,
                    json.dumps(registry, indent=2, sort_keys=True) + "\n",
                )
            raise
        mutation = (
            ProvisionalRemoteApproval(
                approvals_path=approvals_path,
                binding_id=binding.binding_id,
                provisional_id=provisional_id,
                previous_entry=(
                    dict(previous_entry) if isinstance(previous_entry, dict) else None
                ),
                registry_existed=registry_existed,
                credential_path=binding.credential_path,
                credential_sha256=hashlib.sha256(credential_bytes).hexdigest(),
                previous_credential_existed=previous_credential[0],
                previous_credential=previous_credential[1],
                previous_credential_mode=previous_credential[2],
            )
            if provisional_id is not None
            else None
        )
    return approved, mutation


def approve_remote_binding(
    root: Path,
    token: str,
    *,
    approvals_path: Path | None = None,
    credentials_dir: Path | None = None,
) -> ProjectBinding:
    """Approve and credential the exact remote binding already declared by a project."""
    binding, _ = _approve_remote_binding(
        root,
        token,
        approvals_path=approvals_path,
        credentials_dir=credentials_dir,
    )
    return binding


def begin_provisional_remote_approval(
    root: Path,
    token: str,
    *,
    approvals_path: Path | None = None,
    credentials_dir: Path | None = None,
) -> tuple[ProjectBinding, ProvisionalRemoteApproval]:
    """Install one identifiable approval that can be committed or rolled back safely."""
    binding, mutation = _approve_remote_binding(
        root,
        token,
        approvals_path=approvals_path,
        credentials_dir=credentials_dir,
        provisional_id=uuid.uuid4().hex,
    )
    assert mutation is not None
    return binding, mutation


def finish_provisional_remote_approval(
    mutation: ProvisionalRemoteApproval,
    *,
    commit: bool,
    preserve_credential: bool = False,
) -> bool:
    """Commit or undo only this lease, preserving every concurrent registry entry."""
    path = mutation.approvals_path
    with portable_file_lock(path):
        if not path.exists():
            return False
        registry = _read_approvals(path)
        current = registry["bindings"].get(mutation.binding_id)
        if (
            not isinstance(current, dict)
            or current.get("provisional_id") != mutation.provisional_id
        ):
            # Another successful operation superseded this exact binding. Its
            # state is authoritative and must never be rolled back by us.
            return False
        if not preserve_credential:
            try:
                credential = mutation.credential_path.read_bytes()
                _require_private_file(mutation.credential_path)
            except (OSError, BindingError):
                return False
            if hashlib.sha256(credential).hexdigest() != mutation.credential_sha256:
                # A writer that did not carry this lease changed the token.
                # Leave both files untouched rather than pairing an approval
                # with the wrong credential.
                return False
        if commit:
            stable = dict(current)
            stable.pop("provisional_id", None)
            registry["bindings"][mutation.binding_id] = stable
        else:
            if not preserve_credential:
                _restore_private_file(
                    mutation.credential_path,
                    (
                        mutation.previous_credential_existed,
                        mutation.previous_credential,
                        mutation.previous_credential_mode,
                    ),
                )
            if mutation.previous_entry is None:
                registry["bindings"].pop(mutation.binding_id, None)
            else:
                registry["bindings"][mutation.binding_id] = mutation.previous_entry
        if not commit and not mutation.registry_existed and not registry["bindings"]:
            path.unlink(missing_ok=True)
        else:
            _private_write(path, json.dumps(registry, indent=2, sort_keys=True) + "\n")
    return True


def write_remote_project_config(
    root: Path,
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str | None = None,
) -> Path:
    """Write only non-secret remote binding data inside a project checkout."""
    if not SAFE_IDENTIFIER.fullmatch(project_id):
        raise BindingError("remote project id contains unsupported characters")
    root = find_workspace_root(root)
    api_url = canonical_https_url(api_url, field="api_url")
    dashboard_url = (
        canonical_https_url(dashboard_url, field="dashboard_url") if dashboard_url else None
    )
    path = root / ".dduo-solo-founder" / "project.toml"
    lines = [
        "version = 2",
        f"id = {toml_string(project_id)}",
        f"name = {toml_string(name)}",
        'binding = "remote"',
        f"api_url = {toml_string(api_url)}",
    ]
    if dashboard_url:
        lines.append(f"dashboard_url = {toml_string(dashboard_url)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    exclude_local_config(root)
    return path


def recovery_state_files(
    root: Path,
    *,
    config_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Path]:
    """Enumerate only recovery files owned by this exact project binding.

    The full-recovery caller explicitly includes secrets.  Root-specific
    approvals and ephemeral session pins are intentionally excluded because a
    restored checkout must establish new local authority.
    """
    root = find_workspace_root(root)
    project = load_project(root)
    binding = binding_from_project(
        root,
        project,
        require_approval=False,
        enforce_project_claim=(
            str(project.get("binding") or "local").strip().lower() == "local"
        ),
    )
    config_root = config_root or (Path.home() / ".config" / "dduo-solo-founder")
    files: dict[str, Path] = {}

    def include(archive_path: str, source: Path) -> None:
        # Do not follow a symlink out of an allowlisted owner directory.
        if source.is_file() and not source.is_symlink():
            files[archive_path] = source

    include("binding/project.toml", root / ".dduo-solo-founder" / "project.toml")
    if binding.remote:
        include(
            "binding/remote-credential.token",
            config_root / "remote-credentials" / f"{binding.binding_id}.token",
        )

    hook_root = config_root / "hook-state"
    if hook_root.is_dir() and not hook_root.is_symlink():
        for source in sorted(hook_root.glob("*.json")):
            if source.is_symlink():
                continue
            try:
                state = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or str(state.get("project_id") or "") != binding.project_id:
                continue
            # Unqualified legacy hook files cannot prove that they belong to
            # this binding after a remote move or restore. Preserve them on
            # disk for manual recovery, but never package them automatically.
            state_binding = state.get("binding_id")
            if state_binding != binding.binding_id:
                continue
            include(f"host-state/hooks/{source.name}", source)

    scope = hashlib.sha256(
        f"{binding.project_id}\0{binding.binding_id}".encode("utf-8")
    ).hexdigest()
    scoped_mcp = config_root / "mcp-observability" / f"{scope}.json"
    if scoped_mcp.is_file() and not scoped_mcp.is_symlink():
        include("host-state/mcp-observability.json", scoped_mcp)
    else:
        # Read the pre-binding-v2 queue only as a compatibility fallback.  Its
        # exact project-id filename is the legacy isolation boundary; once a
        # binding-scoped queue exists it is the sole authoritative source.
        include(
            "host-state/mcp-observability.json",
            config_root / "mcp-observability" / f"{binding.project_id}.json",
        )
    return files
