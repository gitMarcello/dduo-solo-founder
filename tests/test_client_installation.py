from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dduo_solo_founder.client_installation import (
    ClientInstallationError,
    persist_client_installation,
    resolve_client_installation,
)
from dduo_solo_founder import client_readiness
from dduo_solo_founder import client_installation as ci
from dduo_solo_founder.native_process import client_command


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("family,package_name", [
    ("codex", "@openai/codex"), ("claude", "@anthropic-ai/claude-code"),
])
def test_npm_shell_shim_pins_node_before_readiness_and_keeps_it_after_path_changes(
    monkeypatch, tmp_path, family, package_name
):
    package = tmp_path / "node_modules" / package_name
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": package_name, "bin": {family: "cli.js"}}))
    entrypoint = package / "cli.js"
    entrypoint.write_text("#!/usr/bin/env node\n")
    launcher = _executable(tmp_path / family)
    node = _executable(tmp_path / "selected-node")
    monkeypatch.setattr("dduo_solo_founder.client_installation.shutil.which", lambda name: str(node) if name == "node" else None)
    installation = resolve_client_installation(
        family, executable=launcher, config_dir=tmp_path / "profile",
        record_path=tmp_path / "executables.json", scope_path=tmp_path / "scopes.json",
    )
    assert installation.management_command.executable == launcher
    assert installation.management_command.node_executable == node
    monkeypatch.setenv("PATH", str(tmp_path / "other-node"))
    assert client_command(
        str(launcher), ["--version"], client=family,
        node_executable=str(installation.management_command.node_executable),
    ) == [str(node), str(entrypoint), "--version"]

    # Being inside an npm package does not turn a vendor's native binary into JS.
    native = package / "native-client"
    native.write_bytes(b"\x7fELF native payload")
    native.chmod(0o755)
    selected_native = resolve_client_installation(
        family, executable=native, config_dir=tmp_path / "profile",
        record_path=tmp_path / "executables.json", scope_path=tmp_path / "scopes.json",
    )
    assert selected_native.management_command.node_executable is None


def test_explicit_client_profile_is_used_for_command_and_environment(tmp_path: Path) -> None:
    launcher = _executable(tmp_path / "codex")
    config = tmp_path / "custom codex config"

    installation = resolve_client_installation(
        "codex",
        surface_value="vscode",
        config_dir=config,
        executable=launcher,
        record_path=tmp_path / "executables.json",
        scope_path=tmp_path / "scopes.json",
    )

    assert installation.surface_hint == "vscode"
    assert installation.management_command.executable == launcher
    assert installation.environment({"PATH": ""})["CODEX_HOME"] == str(config)


def test_saved_profile_rejects_another_config_before_runtime_fallback(tmp_path: Path) -> None:
    launcher = _executable(tmp_path / "claude")
    records = tmp_path / "executables.json"
    scopes = tmp_path / "scopes.json"
    first = resolve_client_installation(
        "claude",
        config_dir=tmp_path / "claude-a",
        executable=launcher,
        record_path=records,
        scope_path=scopes,
    )
    persist_client_installation(first, record_path=records, scope_path=scopes)

    with pytest.raises(ClientInstallationError, match="another Claude profile|manages Claude"):
        resolve_client_installation(
            "claude",
            config_dir=tmp_path / "claude-b",
            executable=launcher,
            record_path=records,
            scope_path=scopes,
        )


def test_stale_saved_launcher_requires_repair_and_never_uses_path(tmp_path: Path, monkeypatch) -> None:
    records = tmp_path / "executables.json"
    scopes = tmp_path / "scopes.json"
    records.write_text(
        json.dumps({"version": 1, "clients": {"codex": {"launcher_path": str(tmp_path / "gone")}}}),
        encoding="utf-8",
    )
    scopes.write_text(
        json.dumps({"version": 1, "clients": {"codex": {"config_dir": str(tmp_path / "config")}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    _executable(tmp_path / "codex")

    with pytest.raises(ClientInstallationError, match="saved client executable"):
        resolve_client_installation(
            "codex",
            config_dir=tmp_path / "config",
            record_path=records,
            scope_path=scopes,
        )


def test_saved_node_launcher_without_its_node_record_requires_repair(tmp_path: Path) -> None:
    launcher = _executable(tmp_path / "codex")
    launcher.write_text("#!/usr/bin/env node\nprocess.exit(0)\n", encoding="utf-8")
    records = tmp_path / "executables.json"
    records.write_text(
        json.dumps({"version": 1, "clients": {"codex": {"launcher_path": str(launcher)}}}),
        encoding="utf-8",
    )

    with pytest.raises(ClientInstallationError, match="requires its recorded Node executable"):
        resolve_client_installation(
            "codex",
            config_dir=tmp_path / "config",
            record_path=records,
            scope_path=tmp_path / "scopes.json",
        )


def test_malformed_persistent_record_requires_repair_without_path_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = tmp_path / "executables.json"
    records.write_text("not json", encoding="utf-8")
    _executable(tmp_path / "codex")
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ClientInstallationError, match="client record.*setup repair"):
        resolve_client_installation(
            "codex",
            config_dir=tmp_path / "config",
            record_path=records,
            scope_path=tmp_path / "scopes.json",
        )


def test_persisted_records_are_private_and_do_not_include_secrets(tmp_path: Path) -> None:
    launcher = _executable(tmp_path / "codex")
    records = tmp_path / "private" / "executables.json"
    scopes = tmp_path / "private" / "scopes.json"
    installation = resolve_client_installation(
        "codex",
        config_dir=tmp_path / "config",
        executable=launcher,
        record_path=records,
        scope_path=scopes,
    )
    persist_client_installation(installation, record_path=records, scope_path=scopes)

    if os.name != "nt":
        assert os.stat(records).st_mode & 0o777 == 0o600
        assert os.stat(scopes).st_mode & 0o777 == 0o600
    assert set(json.loads(records.read_text())["clients"]["codex"]) == {"launcher_path"}
    assert json.loads(scopes.read_text())["clients"]["codex"] == {
        "config_dir": str(tmp_path / "config")
    }


def test_registration_rolls_back_both_files_if_the_second_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _executable(tmp_path / "claude")
    records = tmp_path / "executables.json"
    scopes = tmp_path / "scopes.json"
    records.write_text('{"version": 1, "clients": {}}\n', encoding="utf-8")
    scopes.write_text('{"version": 1, "clients": {}}\n', encoding="utf-8")
    before_records, before_scopes = records.read_bytes(), scopes.read_bytes()
    installation = resolve_client_installation(
        "claude",
        config_dir=tmp_path / "config",
        executable=launcher,
        record_path=records,
        scope_path=scopes,
    )

    from dduo_solo_founder import client_installation

    real_write = client_installation._write_private_json
    calls = 0

    def fail_second(path: Path, value: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated scope failure")
        real_write(path, value)

    monkeypatch.setattr(client_installation, "_write_private_json", fail_second)
    with pytest.raises(ClientInstallationError, match="could not persist"):
        persist_client_installation(installation, record_path=records, scope_path=scopes)

    assert records.read_bytes() == before_records
    assert scopes.read_bytes() == before_scopes


def test_vscode_readiness_names_the_correct_reload_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _executable(tmp_path / "codex")
    installation = resolve_client_installation(
        "codex", surface_value="vscode", config_dir=tmp_path / "config", executable=launcher,
        record_path=tmp_path / "records.json", scope_path=tmp_path / "scopes.json",
    )
    monkeypatch.setattr(
        client_readiness,
        "subscription_auth_status",
        lambda _client, **_kwargs: client_readiness.SubscriptionAuthStatus(
            "codex", True, "authenticated", "codex login"
        ),
    )
    monkeypatch.setattr(
        client_readiness,
        "codex_hook_status",
        lambda _root, **_kwargs: client_readiness.CodexHookStatus(
            False, "authorization_required", 3, ["untrusted"]
        ),
    )

    action = client_readiness.client_readiness(
        "codex", tmp_path, installation=installation
    )["actions"][0]
    assert "Reload the VS Code window" in action["detail"]
    assert "fully quit" not in action["detail"].lower()


@pytest.mark.parametrize("family, config_key", [("codex", "CODEX_HOME"), ("claude", "CLAUDE_CONFIG_DIR")])
def test_daemon_reuses_saved_custom_scope_without_ambient_path_or_profile(tmp_path, monkeypatch, family, config_key):
    launcher = _executable(tmp_path / family)
    config = tmp_path / "custom profile"
    records, scopes = tmp_path / "executables.json", tmp_path / "scopes.json"
    selected = resolve_client_installation(
        family, config_dir=config, executable=launcher, record_path=records, scope_path=scopes,
    )
    persist_client_installation(selected, record_path=records, scope_path=scopes)
    monkeypatch.setenv("PATH", "")
    restored = resolve_client_installation(
        family, environment={}, record_path=records, scope_path=scopes,
    )
    assert restored.config_dir == config
    assert restored.management_command == selected.management_command
    with pytest.raises(ClientInstallationError, match="manages"):
        resolve_client_installation(
            family, environment={config_key: str(tmp_path / "other")},
            record_path=records, scope_path=scopes,
        )


@pytest.mark.parametrize("document", [
    {"clients": []}, {"clients": None}, {"clients": "invalid"},
    {"clients": {"codex": "invalid"}}, {"clients": {"codex": None}},
    {"clients": {"codex": {}}}, {"clients": {"codex": {"launcher_path": None}}},
    {"version": 2, "clients": {}}, {"version": True, "clients": {}},
])
def test_structurally_invalid_record_never_falls_back_to_path(tmp_path, monkeypatch, document):
    from dduo_solo_founder import client_installation

    records = tmp_path / "executables.json"
    records.write_text(json.dumps({"version": 1, **document}))
    monkeypatch.setattr(
        client_installation, "_path_candidate",
        lambda _family: pytest.fail("invalid record attempted PATH fallback"),
    )
    with pytest.raises(ClientInstallationError, match="invalid; run setup repair"):
        resolve_client_installation(
            "codex", config_dir=tmp_path / "config", record_path=records,
            scope_path=tmp_path / "scopes.json",
        )


def test_scope_record_requires_a_configuration_path(tmp_path):
    scopes = tmp_path / "scopes.json"
    scopes.write_text(json.dumps({"version": 1, "clients": {"codex": {"config_dir": None}}}))
    with pytest.raises(ClientInstallationError, match="invalid; run setup repair"):
        resolve_client_installation(
            "codex", record_path=tmp_path / "executables.json", scope_path=scopes,
        )


def test_explicit_repair_ignores_only_selected_malformed_entry_and_keeps_scope_conflict(tmp_path):
    launcher = _executable(tmp_path / "codex")
    records, scopes = tmp_path / "records.json", tmp_path / "scopes.json"
    documents = {
        records: {"version": 1, "clients": {"codex": None, "claude": {"launcher_path": str(tmp_path / "saved-claude")}}},
        scopes: {"version": 1, "clients": {"codex": {"config_dir": str(tmp_path / "config")}}},
    }
    for path, value in documents.items():
        path.write_text(json.dumps(value))
    selected = resolve_client_installation(
        "codex", executable=launcher, config_dir=tmp_path / "config", repair=True,
        record_path=records, scope_path=scopes,
    )
    assert selected.management_command.executable == launcher
    assert json.loads(records.read_text()) == documents[records]
    with pytest.raises(ClientInstallationError, match="manages Codex"):
        resolve_client_installation(
            "codex", executable=launcher, config_dir=tmp_path / "other", repair=True,
            record_path=records, scope_path=scopes,
        )
    with pytest.raises(ClientInstallationError, match="requires an explicit"):
        resolve_client_installation("codex", executable=launcher, repair=True)
    records.write_text(json.dumps({"version": 1, "clients": {"codex": None, "claude": None}}))
    with pytest.raises(ClientInstallationError, match="invalid; run setup repair"):
        resolve_client_installation(
            "codex", executable=launcher, config_dir=tmp_path / "config", repair=True,
            record_path=records, scope_path=scopes,
        )


def test_bootstrap_explicit_node_is_pinned_and_same_saved_launcher_does_not_rediscover_node(tmp_path, monkeypatch):
    from dduo_solo_founder import client_installation

    launcher = _executable(tmp_path / "codex")
    launcher.write_text("#!/usr/bin/env node\n")
    node = _executable(tmp_path / "pinned-node")
    records, scopes = tmp_path / "records.json", tmp_path / "scopes.json"
    monkeypatch.setattr(
        client_installation.shutil, "which",
        lambda _name: pytest.fail("explicit bootstrap rediscovered Node via PATH"),
    )
    installation = resolve_client_installation(
        "codex", executable=launcher, node_executable=node, config_dir=tmp_path / "config",
        record_path=records, scope_path=scopes,
    )
    persist_client_installation(installation, record_path=records, scope_path=scopes)
    selected = resolve_client_installation(
        "codex", executable=launcher, environment={}, record_path=records, scope_path=scopes,
    )
    assert selected.management_command.node_executable == node
    with pytest.raises(ClientInstallationError, match="requires an explicit client executable"):
        resolve_client_installation("codex", node_executable=node)


@pytest.mark.parametrize("document", [{}, {"version": 1}, {"clients": {}}, {"version": None, "clients": {}}])
@pytest.mark.parametrize("repair", [False, True])
def test_existing_records_require_version_and_clients_even_during_targeted_repair(tmp_path, document, repair):
    launcher = _executable(tmp_path / "codex")
    records = tmp_path / "records.json"
    records.write_text(json.dumps(document))
    with pytest.raises(ClientInstallationError, match="invalid; run setup repair"):
        resolve_client_installation(
            "codex", executable=launcher, config_dir=tmp_path / "config", repair=repair,
            record_path=records, scope_path=tmp_path / "scopes.json",
        )


@pytest.mark.parametrize("options", [
    {"surface_value": "invalid"}, {"config_dir": "relative"},
    {"config_dir": "\x00bad"}, {"config_dir": "bad\npath"},
    {"config_dir": ""}, {"executable": "relative"},
])
def test_invalid_selection_is_rejected_before_discovery(tmp_path, monkeypatch, options):
    monkeypatch.setattr(ci, "_path_candidate", lambda _: pytest.fail("unexpected discovery"))
    with pytest.raises(ClientInstallationError):
        resolve_client_installation(
            "codex", record_path=tmp_path / "records", scope_path=tmp_path / "scopes",
            **options,
        )


@pytest.mark.parametrize("surface", ["cli", "vscode", "desktop", "unknown"])
def test_desktop_fallback_is_never_used_to_claim_cli_or_vscode_support(tmp_path, monkeypatch, surface):
    desktop = _executable(tmp_path / "desktop-codex")
    monkeypatch.setenv(ci.CODEX_DESKTOP_EXECUTABLE_ENV, str(desktop))
    monkeypatch.setattr(ci.shutil, "which", lambda _: None)
    options = dict(surface_value=surface, environment={}, record_path=tmp_path / "records",
                   scope_path=tmp_path / "scopes")
    if surface in {"desktop", "unknown"}:
        selected = resolve_client_installation("codex", **options)
        assert selected.management_command.executable == desktop
    else:
        with pytest.raises(ClientInstallationError, match="official CLI"):
            resolve_client_installation("codex", **options)


def test_path_discovery_and_probe_are_separate_and_node_is_required_for_js(tmp_path, monkeypatch):
    launcher = _executable(tmp_path / "claude")
    probe = _executable(tmp_path / "probe.js")
    node = _executable(tmp_path / "node")
    monkeypatch.setattr(ci.shutil, "which", lambda name: str(node if name == "node" else launcher))
    selected = resolve_client_installation(
        "claude", surface_value="vscode", probe_executable=probe, environment={},
        record_path=tmp_path / "records", scope_path=tmp_path / "scopes",
    )
    assert selected.management_command.executable == launcher
    assert selected.management_command.node_executable is None
    assert selected.probe_command.executable == probe
    assert selected.probe_command.node_executable == node
    assert selected.environment({}) == {"CLAUDE_CONFIG_DIR": str(selected.config_dir)}
    monkeypatch.setattr(ci.shutil, "which", lambda _: None)
    with pytest.raises(ClientInstallationError, match="Node is not available"):
        resolve_client_installation(
            "claude", executable=probe, environment={},
            record_path=tmp_path / "records", scope_path=tmp_path / "scopes",
        )
    with pytest.raises(ClientInstallationError, match="desktop"):
        resolve_client_installation("claude", surface_value="desktop")


@pytest.mark.parametrize("family,key", [("codex", "CODEX_HOME"), ("claude", "CLAUDE_CONFIG_DIR")])
def test_explicit_environment_config_must_be_accessible(tmp_path, family, key):
    config = tmp_path / "config"
    assert ci.default_config_dir(family, {key: str(config)}) == config
    config.write_text("not a directory")
    with pytest.raises(ClientInstallationError, match="accessible configuration"):
        ci.default_config_dir(family, {key: str(config)})


def test_selected_node_record_is_validated_and_cannot_silently_rediscover_node(tmp_path):
    launcher = _executable(tmp_path / "codex.js")
    records, scopes = tmp_path / "records", tmp_path / "scopes"
    for node in (123, str(tmp_path / "missing-node")):
        records.write_text(json.dumps({"version": 1, "clients": {
            "codex": {"launcher_path": str(launcher), "node_executable": node},
        }}))
        with pytest.raises(ClientInstallationError, match="repair"):
            resolve_client_installation("codex", executable=launcher, record_path=records, scope_path=scopes)
    records.write_text(json.dumps({"version": 1, "clients": {
        "codex": {"launcher_path": str(launcher)},
    }}))
    with pytest.raises(ClientInstallationError, match="recorded Node"):
        resolve_client_installation("codex", executable=launcher, record_path=records, scope_path=scopes)


def test_remove_only_selected_registration_preserves_other_client_and_vendor_files(tmp_path):
    records, scopes = tmp_path / "records", tmp_path / "scopes"
    vendor_files = []
    for family in ("codex", "claude"):
        launcher = _executable(tmp_path / family)
        vendor_files.append(launcher)
        selected = resolve_client_installation(
            family, executable=launcher, config_dir=tmp_path / f"{family}-config",
            record_path=records, scope_path=scopes,
        )
        persist_client_installation(selected, record_path=records, scope_path=scopes)
    ci.remove_client_installation("codex", record_path=records, scope_path=scopes)
    ci.remove_client_installation("codex", record_path=records, scope_path=scopes)
    for path in (records, scopes):
        assert set(json.loads(path.read_text())["clients"]) == {"claude"}
    assert all(path.exists() for path in vendor_files)


@pytest.mark.parametrize("existing", [False, True])
def test_persist_scope_failure_restores_absent_or_existing_registration(tmp_path, monkeypatch, existing):
    records, scopes = tmp_path / "records", tmp_path / "scopes"
    if existing:
        for path in (records, scopes):
            path.write_text('{"version":1,"clients":{}}')
    selected = resolve_client_installation(
        "codex", executable=_executable(tmp_path / "codex"), config_dir=tmp_path / "config",
        record_path=records, scope_path=scopes,
    )
    real_write = ci._write_private_json
    def fail_scope(path, value):
        if path == scopes:
            raise OSError("scope failure")
        return real_write(path, value)
    monkeypatch.setattr(ci, "_write_private_json", fail_scope)
    with pytest.raises(ClientInstallationError, match="could not persist"):
        persist_client_installation(selected, record_path=records, scope_path=scopes)
    for path in (records, scopes):
        assert path.exists() == existing
        if existing:
            assert json.loads(path.read_text())["clients"] == {}


def test_persist_reports_failed_rollback_instead_of_claiming_registration_succeeded(tmp_path, monkeypatch):
    records, scopes = tmp_path / "records", tmp_path / "scopes"
    records.write_text('{"version":1,"clients":{}}')
    selected = resolve_client_installation(
        "codex", executable=_executable(tmp_path / "codex"), config_dir=tmp_path / "config",
        record_path=records, scope_path=scopes,
    )
    def unavailable(*_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(ci, "_write_private_json", unavailable)
    monkeypatch.setattr(ci, "_write_private_bytes", unavailable)
    with pytest.raises(ClientInstallationError, match="rollback was incomplete"):
        persist_client_installation(selected, record_path=records, scope_path=scopes)
