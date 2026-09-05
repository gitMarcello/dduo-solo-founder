import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dduo_solo_founder import client_binding, project_config
from dduo_solo_founder.client_binding import (
    BindingError,
    RemoteBindingApprovalRequired,
    approve_remote_binding,
    binding_from_project,
    canonical_https_url,
    load_binding,
    recovery_state_files,
    write_remote_project_config,
)
from dduo_solo_founder.client_http import ClientUpgradeRequired, ProjectHttpClient


def _root(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _remote(root: Path) -> Path:
    return write_remote_project_config(
        root,
        project_id="remote-project",
        name="Remote",
        api_url="https://203.0.113.10:9443/api/",
        dashboard_url="https://dashboard.example.test/base/",
    )


def test_remote_project_config_is_excluded_from_git_status(monkeypatch, tmp_path):
    root = _root(tmp_path, "remote-clean")
    info = root / ".git" / "info"
    info.mkdir()
    monkeypatch.setattr(project_config, "is_git_worktree", lambda _: True)
    monkeypatch.setattr(
        project_config.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=".git\n"),
    )

    _remote(root)

    assert (info / "exclude").read_text().splitlines() == ["/.dduo-solo-founder/"]


def test_legacy_local_binding_is_explicit_and_root_scoped(tmp_path):
    first = _root(tmp_path, "first")
    second = _root(tmp_path, "second")
    project = {"id": "p1", "name": "Local", "api_port": 18001, "web_port": 20001}
    binding = binding_from_project(first, project)
    clone = binding_from_project(second, project)
    assert binding.kind == "local"
    assert binding.api_url == "http://127.0.0.1:18001"
    assert binding.dashboard_link("tasks", "plans").endswith(
        "/?project=p1&tab=tasks&view=plans"
    )
    assert binding.binding_id != clone.binding_id


@pytest.mark.parametrize(
    "project",
    [
        {"id": "p", "binding": "remote", "api_url": "http://example.test"},
        {
            "id": "p",
            "binding": "remote",
            "api_url": "https://example.test?token=secret",
        },
        {
            "id": "p",
            "binding": "remote",
            "api_url": "https://example.test",
            "api_port": 18000,
        },
        {
            "id": "p",
            "binding": "local",
            "api_port": 18000,
            "web_port": 20000,
            "api_url": "https://example.test",
        },
    ],
)
def test_binding_never_falls_back_between_local_and_remote(tmp_path, project):
    with pytest.raises(BindingError):
        binding_from_project(_root(tmp_path), project, require_approval=False)


def test_remote_approval_binds_root_project_endpoint_and_private_token(tmp_path):
    root = _root(tmp_path, "approved")
    _remote(root)
    approvals = tmp_path / "host" / "approvals.json"
    credentials = tmp_path / "host" / "credentials"
    with pytest.raises(RemoteBindingApprovalRequired):
        binding_from_project(
            root,
            __import__("tomllib").loads(_remote(root).read_text()),
            approvals_path=approvals,
            credentials_dir=credentials,
        )
    binding = approve_remote_binding(
        root,
        "top-secret",
        approvals_path=approvals,
        credentials_dir=credentials,
    )
    assert binding.remote
    assert binding.api_url == "https://203.0.113.10:9443/api"
    assert binding.bearer_token == "top-secret"
    if os.name != "nt":
        assert binding.credential_path.stat().st_mode & 0o777 == 0o600
        assert approvals.stat().st_mode & 0o777 == 0o600

    clone = _root(tmp_path, "clone")
    clone_config = clone / ".dduo-solo-founder"
    clone_config.mkdir()
    clone_config.joinpath("project.toml").write_text(_remote(root).read_text())
    with pytest.raises(RemoteBindingApprovalRequired):
        approve_remote_binding(
            root,
            "top-secret",
            approvals_path=approvals,
            credentials_dir=credentials,
        ) and binding_from_project(
            clone,
            __import__("tomllib").loads(clone_config.joinpath("project.toml").read_text()),
            approvals_path=approvals,
            credentials_dir=credentials,
        )


def test_provisional_approval_rollback_cannot_replace_a_newer_same_binding(tmp_path):
    root = _root(tmp_path, "compare-and-swap")
    config = _remote(root)
    project = __import__("tomllib").loads(config.read_text())
    approvals = tmp_path / "host" / "approvals.json"
    credentials = tmp_path / "host" / "credentials"

    _, first = client_binding.begin_provisional_remote_approval(
        root,
        "first-token",
        approvals_path=approvals,
        credentials_dir=credentials,
    )
    second_binding, second = client_binding.begin_provisional_remote_approval(
        root,
        "second-token",
        approvals_path=approvals,
        credentials_dir=credentials,
    )

    assert client_binding.finish_provisional_remote_approval(first, commit=False) is False
    assert client_binding.finish_provisional_remote_approval(second, commit=True) is True
    registry = json.loads(approvals.read_text())
    assert list(registry["bindings"]) == [second_binding.binding_id]
    assert "provisional_id" not in registry["bindings"][second_binding.binding_id]
    assert (
        binding_from_project(
            root,
            project,
            approvals_path=approvals,
            credentials_dir=credentials,
        ).bearer_token
        == "second-token"
    )


def test_common_http_client_authenticates_and_leaves_updates_explicit(tmp_path):
    root = _root(tmp_path)
    project = {
        "id": "p1",
        "name": "Remote",
        "binding": "remote",
        "api_url": "https://198.51.100.7/api",
    }
    binding = binding_from_project(root, project, require_approval=False)
    binding = type(binding)(**{**binding.__dict__, "bearer_token": "secret"})
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(
            200,
            headers={"X-DDUO-Client-Status": "grace", "X-DDUO-Target-Release": "r-2"},
            request=httpx.Request(method, url),
            json={"ok": True},
        )

    client = ProjectHttpClient(
        binding,
        component="mcp",
        session_id="session",
        request_function=request,
    )
    assert client.json("GET", "/health") == {"ok": True}
    method, url, options = calls[0]
    assert (method, url) == ("GET", "https://198.51.100.7/api/health")
    assert options["headers"]["Authorization"] == "Bearer secret"
    assert options["headers"]["X-DDUO-Client-Protocol"] == "1"
    assert options["headers"]["X-DDUO-Binding-ID"] == binding.binding_id


def test_upgrade_block_is_fail_closed_and_invalid_target_is_not_trusted(tmp_path):
    root = _root(tmp_path)
    binding = binding_from_project(
        root, {"id": "p", "api_port": 18000, "web_port": 20000}
    )
    response = httpx.Response(
        426,
        headers={"X-DDUO-Target-Release": "https://evil.test/payload"},
        request=httpx.Request("GET", binding.api_url),
    )
    client = ProjectHttpClient(
        binding,
        component="hook",
        request_function=lambda *args, **kwargs: response,
    )
    with pytest.raises(ClientUpgradeRequired) as raised:
        client.request("GET", "/health")
    assert raised.value.directive.target_release is None


def test_recovery_enumerator_is_binding_scoped_and_excludes_authority(tmp_path):
    root = _root(tmp_path)
    _remote(root)
    config = tmp_path / "config"
    data = tmp_path / "data"
    binding = approve_remote_binding(
        root,
        "recover-me",
        approvals_path=config / "remote-bindings.json",
        credentials_dir=config / "remote-credentials",
    )
    state = config / "hook-state" / "session.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"project_id": binding.project_id, "binding_id": binding.binding_id}))
    other = state.with_name("other.json")
    other.write_text(json.dumps({"project_id": "other", "binding_id": binding.binding_id}))
    scope = __import__("hashlib").sha256(
        f"{binding.project_id}\0{binding.binding_id}".encode()
    ).hexdigest()
    for path in (
        config / "mcp-observability" / f"{scope}.json",
        config / "update-trust.json",
        config / "session-pins" / "ignored.json",
        data / "client-current.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    files = recovery_state_files(root, config_root=config, data_root=data)
    assert "binding/remote-credential.token" in files
    assert "host-state/hooks/session.json" in files
    assert "host-state/mcp-observability.json" in files
    assert "host-state/update-queue.json" not in files
    assert "host-state/update-trust.json" not in files
    assert "host-state/client-current.json" not in files
    assert "host-state/client-previous.json" not in files
    assert all("approval" not in name and "session-pins" not in name for name in files)
    assert other not in files.values()


def test_binding_dashboard_links_and_https_canonicalization_are_strict(tmp_path):
    root = _root(tmp_path)
    local = binding_from_project(
        root, {"id": "p", "api_port": 18000, "web_port": 20000}
    )
    assert local.remote is False
    assert local.dashboard_link("memory") == (
        "http://127.0.0.1:20000/?project=p&tab=memory"
    )
    assert local.dashboard_link("tasks", work_id="t/1") == (
        "http://127.0.0.1:20000/?project=p&tab=tasks&work=t%2F1"
    )
    assert local.dashboard_link("tasks", plan_id="p/1") == (
        "http://127.0.0.1:20000/?project=p&tab=tasks&view=plans&plan=p%2F1"
    )
    no_dashboard = type(local)(**{**local.__dict__, "dashboard_url": None})
    assert no_dashboard.dashboard_link() is None
    with pytest.raises(ValueError, match="unsupported dashboard tab"):
        local.dashboard_link("unknown")
    with pytest.raises(ValueError, match="unsupported dashboard work view"):
        local.dashboard_link("memory", "plans")

    assert canonical_https_url(" HTTPS://[2001:db8::1]:443/api/ ", field="api") == (
        "https://[2001:db8::1]:443/api"
    )
    for value, message in (
        ("https://example.test/\nunsafe", "unsafe characters"),
        ("https://example.test:bad", "valid HTTPS URL"),
        ("https://user:secret@example.test", "must not contain credentials"),
        ("https://example.test/#fragment", "query or fragment"),
    ):
        with pytest.raises(BindingError, match=message):
            canonical_https_url(value, field="api")


@pytest.mark.parametrize(
    ("project", "message"),
    [
        ({"api_port": 1, "web_port": 2}, "no id"),
        ({"id": "../escape", "api_port": 1, "web_port": 2}, "unsupported"),
        ({"id": "p", "api_port": "bad", "web_port": 2}, "requires API"),
        ({"id": "p", "api_port": 0, "web_port": 2}, "out of range"),
        ({"id": "p", "binding": "shared"}, "exactly local or remote"),
    ],
)
def test_binding_rejects_invalid_identity_local_ports_and_kind(tmp_path, project, message):
    with pytest.raises(BindingError, match=message):
        binding_from_project(_root(tmp_path), project, require_approval=False)


def test_approval_registry_and_credentials_fail_closed(tmp_path):
    root = _root(tmp_path)
    config_path = _remote(root)
    project = __import__("tomllib").loads(config_path.read_text())
    approvals = tmp_path / "host/approvals.json"
    credentials = tmp_path / "host/credentials"

    with pytest.raises(BindingError, match="cannot be empty"):
        approve_remote_binding(
            root, " ", approvals_path=approvals, credentials_dir=credentials
        )

    approvals.parent.mkdir(parents=True)
    approvals.write_text("not-json")
    approvals.chmod(0o600)
    with pytest.raises(BindingError, match="unreadable"):
        binding_from_project(
            root,
            project,
            approvals_path=approvals,
            credentials_dir=credentials,
        )
    approvals.write_text('{"version":2,"bindings":{}}')
    with pytest.raises(BindingError, match="unsupported format"):
        binding_from_project(
            root,
            project,
            approvals_path=approvals,
            credentials_dir=credentials,
        )

    binding = approve_remote_binding(
        root, "secret", approvals_path=approvals.with_name("valid.json"), credentials_dir=credentials
    )
    valid_approvals = approvals.with_name("valid.json")
    assert binding.credential_path is not None
    binding.credential_path.unlink()
    with pytest.raises(RemoteBindingApprovalRequired, match="credential is missing"):
        binding_from_project(
            root,
            project,
            approvals_path=valid_approvals,
            credentials_dir=credentials,
        )
    binding.credential_path.write_text("\n")
    binding.credential_path.chmod(0o600)
    with pytest.raises(RemoteBindingApprovalRequired, match="credential is empty"):
        binding_from_project(
            root,
            project,
            approvals_path=valid_approvals,
            credentials_dir=credentials,
        )


def test_private_binding_files_reject_links_and_unsafe_modes(tmp_path):
    target = tmp_path / "target"
    target.write_text("{}")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(BindingError, match="regular file"):
        client_binding._require_private_file(link)
    if os.name != "nt":
        target.chmod(0o644)
        with pytest.raises(BindingError, match="permissions are unsafe"):
            client_binding._require_private_file(target)
    with pytest.raises(BindingError, match="invalid binding identifier"):
        client_binding._credential_path("../escape", tmp_path)


def test_private_binding_files_reject_wrong_ownership_and_restore_snapshots(
    monkeypatch, tmp_path
):
    private = tmp_path / "private.json"
    private.write_text("before")
    private.chmod(0o600)
    if hasattr(os, "getuid") and os.name != "nt":
        real_uid = os.getuid()
        monkeypatch.setattr(client_binding.os, "getuid", lambda: real_uid + 1)
        with pytest.raises(BindingError, match="wrong owner"):
            client_binding._require_private_file(private)
        monkeypatch.setattr(client_binding.os, "getuid", lambda: real_uid)

    client_binding._restore_private_file(private, (True, b"restored", 0o640))
    assert private.read_bytes() == b"restored"
    if os.name != "nt":
        assert private.stat().st_mode & 0o777 == 0o640


def test_remote_approval_rolls_back_registry_and_credential_on_final_validation_failure(
    monkeypatch, tmp_path
):
    root = _root(tmp_path, "transaction")
    _remote(root)
    approvals = tmp_path / "host" / "approvals.json"
    credentials = tmp_path / "host" / "credentials"
    stable = approve_remote_binding(
        root,
        "stable-token",
        approvals_path=approvals,
        credentials_dir=credentials,
    )
    original_registry = approvals.read_bytes()
    original_credential = stable.credential_path.read_bytes()
    original_binding_from_project = client_binding.binding_from_project

    def fail_final_validation(*args, **kwargs):
        if kwargs.get("require_approval") is False:
            return original_binding_from_project(*args, **kwargs)
        raise RuntimeError("final validation failed")

    monkeypatch.setattr(client_binding, "binding_from_project", fail_final_validation)
    with pytest.raises(RuntimeError, match="final validation failed"):
        client_binding._approve_remote_binding(
            root,
            "replacement-token",
            approvals_path=approvals,
            credentials_dir=credentials,
        )

    assert approvals.read_bytes() == original_registry
    assert stable.credential_path.read_bytes() == original_credential


def test_provisional_approval_refuses_missing_or_changed_transaction_state(tmp_path):
    missing_root = _root(tmp_path, "missing-registry")
    _remote(missing_root)
    missing_approvals = tmp_path / "missing" / "approvals.json"
    missing_credentials = tmp_path / "missing" / "credentials"
    _, missing = client_binding.begin_provisional_remote_approval(
        missing_root,
        "token",
        approvals_path=missing_approvals,
        credentials_dir=missing_credentials,
    )
    missing_approvals.unlink()
    assert client_binding.finish_provisional_remote_approval(missing, commit=True) is False

    deleted_root = _root(tmp_path, "deleted-credential")
    _remote(deleted_root)
    deleted_approvals = tmp_path / "deleted" / "approvals.json"
    deleted_credentials = tmp_path / "deleted" / "credentials"
    _, deleted = client_binding.begin_provisional_remote_approval(
        deleted_root,
        "token",
        approvals_path=deleted_approvals,
        credentials_dir=deleted_credentials,
    )
    deleted.credential_path.unlink()
    assert client_binding.finish_provisional_remote_approval(deleted, commit=True) is False

    changed_root = _root(tmp_path, "changed-credential")
    _remote(changed_root)
    changed_approvals = tmp_path / "changed" / "approvals.json"
    changed_credentials = tmp_path / "changed" / "credentials"
    _, changed = client_binding.begin_provisional_remote_approval(
        changed_root,
        "token",
        approvals_path=changed_approvals,
        credentials_dir=changed_credentials,
    )
    changed.credential_path.write_text("different-token\n")
    changed.credential_path.chmod(0o600)
    assert client_binding.finish_provisional_remote_approval(changed, commit=True) is False


def test_provisional_approval_rollback_restores_the_previous_stable_binding(tmp_path):
    root = _root(tmp_path, "stable-rollback")
    _remote(root)
    approvals = tmp_path / "rollback" / "approvals.json"
    credentials = tmp_path / "rollback" / "credentials"
    original = approve_remote_binding(
        root,
        "original-token",
        approvals_path=approvals,
        credentials_dir=credentials,
    )
    previous_entry = json.loads(approvals.read_text())["bindings"][original.binding_id]
    _, mutation = client_binding.begin_provisional_remote_approval(
        root,
        "temporary-token",
        approvals_path=approvals,
        credentials_dir=credentials,
    )

    assert client_binding.finish_provisional_remote_approval(mutation, commit=False) is True
    registry = json.loads(approvals.read_text())
    assert registry["bindings"][original.binding_id] == previous_entry
    assert original.credential_path.read_text().strip() == "original-token"


def test_load_and_approve_only_declared_remote_bindings(monkeypatch, tmp_path):
    root = _root(tmp_path)
    local_path = root / ".dduo-solo-founder/project.toml"
    local_path.parent.mkdir()
    local_path.write_text(
        'version = 2\nid = "local"\nname = "Local"\nbinding = "local"\n'
        "api_port = 18000\nweb_port = 20000\n"
    )
    registry = tmp_path / "projects.json"
    monkeypatch.setattr(project_config, "REGISTRY_PATH", registry)
    project_config.register_project_config(
        root,
        __import__("tomllib").loads(local_path.read_text()),
        registry_path=registry,
    )
    assert load_binding(root).project_id == "local"
    with pytest.raises(BindingError, match="only a remote binding"):
        approve_remote_binding(
            root,
            "token",
            approvals_path=tmp_path / "approvals.json",
            credentials_dir=tmp_path / "credentials",
        )

    with pytest.raises(BindingError, match="unsupported characters"):
        write_remote_project_config(
            root,
            project_id="../remote",
            name="Remote",
            api_url="https://example.test",
        )
    path = write_remote_project_config(
        root,
        project_id="remote",
        name='Line\n"Quoted"\\Name\tapi_url = "https://evil.test"',
        api_url="https://example.test/",
    )
    loaded = __import__("tomllib").loads(path.read_text())
    assert loaded["name"] == 'Line\n"Quoted"\\Name\tapi_url = "https://evil.test"'
    assert loaded["api_url"] == "https://example.test"
    assert "dashboard_url" not in loaded


def test_recovery_state_uses_only_valid_local_hook_and_legacy_mcp_files(
    monkeypatch, tmp_path
):
    root = _root(tmp_path)
    config_path = root / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'version = 2\nid = "local"\nname = "Local"\nbinding = "local"\n'
        "api_port = 18000\nweb_port = 20000\n"
    )
    registry = tmp_path / "projects.json"
    monkeypatch.setattr(project_config, "REGISTRY_PATH", registry)
    project_config.register_project_config(
        root,
        __import__("tomllib").loads(config_path.read_text()),
        registry_path=registry,
    )
    config = tmp_path / "config"
    data = tmp_path / "data"
    hooks = config / "hook-state"
    hooks.mkdir(parents=True)
    (hooks / "invalid.json").write_text("not-json")
    (hooks / "list.json").write_text("[]")
    (hooks / "wrong-binding.json").write_text(
        json.dumps({"project_id": "local", "binding_id": "wrong"})
    )
    legacy = config / "mcp-observability/local.json"
    legacy.parent.mkdir()
    legacy.write_text("{}")
    files = recovery_state_files(root, config_root=config, data_root=data)
    assert files["host-state/mcp-observability.json"] == legacy
    assert "binding/remote-credential.token" not in files
    assert not any(name.startswith("host-state/hooks/") for name in files)
