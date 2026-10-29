from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dduo_solo_founder import client_installation as ci
from dduo_solo_founder import client_readiness, launcher
from dduo_solo_founder.client_repair import repair_codex_client


@pytest.fixture
def registration(tmp_path, monkeypatch):
    directory = tmp_path / "Client con spazi & ü"
    directory.mkdir()
    executable = directory / "codex.exe"
    executable.write_bytes(b"MZ synthetic executable; never run")
    executable.chmod(0o755)
    profile = tmp_path / "managed profile"
    profile.mkdir()
    (profile / "config.toml").write_text("# existing trusted hooks\n", encoding="utf-8")
    records, scopes = tmp_path / "executables.json", tmp_path / "scopes.json"
    records.write_text(json.dumps({"version": 1, "clients": {
        "codex": {"launcher_path": str(tmp_path / "old-build" / "codex.exe")},
        "claude": {"launcher_path": str(tmp_path / "claude.exe")},
    }}), encoding="utf-8")
    scopes.write_text(json.dumps({"version": 1, "clients": {
        "codex": {"config_dir": str(profile)},
        "claude": {"config_dir": str(tmp_path / "claude-profile")},
    }}), encoding="utf-8")
    calls = []

    def version_only(argv, **kwargs):
        assert argv == [str(executable), "--version"]
        assert kwargs["check"] is False and kwargs["timeout"] <= 3
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.163.0-alpha.10.2", stderr="")

    monkeypatch.setattr(client_readiness.subprocess, "run", version_only)
    monkeypatch.setattr(client_readiness, "_codex_candidates", lambda: pytest.fail("client fallback"))
    monkeypatch.setattr(ci, "_path_candidate", lambda _: pytest.fail("PATH fallback"))
    monkeypatch.setattr(ci, "EXECUTABLE_RECORD", records)
    monkeypatch.setattr(ci, "SCOPE_RECORD", scopes)
    return SimpleNamespace(
        executable=executable, profile=profile, records=records, scopes=scopes, calls=calls,
        original=(records.read_bytes(), scopes.read_bytes()),
    )


def repair(registration, **overrides):
    return repair_codex_client(**{
        "executable": registration.executable,
        "surface": "desktop",
        "config_dir": registration.profile,
        **overrides,
    })


def assert_unchanged(registration):
    assert (registration.records.read_bytes(), registration.scopes.read_bytes()) == registration.original
    assert (registration.profile / "config.toml").read_text() == "# existing trusted hooks\n"


@pytest.mark.parametrize("surface", ["desktop", "cli", "vscode"])
def test_repair_replaces_only_selected_registration_after_version_check(registration, surface):
    value = repair(registration, surface=surface)
    assert value["registration_updated"] is True
    assert value["compatibility_verified"] is True
    assert value["hook_authorization"] == "not_checked"
    assert "ready" not in value
    assert value["surface"] == surface
    assert len(registration.calls) == 1
    records = json.loads(registration.records.read_text())
    assert records["clients"]["codex"] == {"launcher_path": str(registration.executable)}
    assert records["clients"]["claude"] == json.loads(registration.original[0])["clients"]["claude"]
    assert json.loads(registration.scopes.read_text()) == json.loads(registration.original[1])
    assert (registration.profile / "config.toml").read_text() == "# existing trusted hooks\n"


def test_dry_run_verifies_compatibility_without_writing(registration):
    value = repair(registration, dry_run=True)
    assert value["dry_run"] is True and value["registration_updated"] is False
    assert len(registration.calls) == 1
    assert_unchanged(registration)


def test_explicit_record_paths_can_be_used_without_default_records(registration, monkeypatch):
    monkeypatch.setattr(ci, "EXECUTABLE_RECORD", registration.profile / "unused.json")
    monkeypatch.setattr(ci, "SCOPE_RECORD", registration.profile / "unused-scopes.json")
    repair(registration, record_path=registration.records, scope_path=registration.scopes)
    assert not ci.EXECUTABLE_RECORD.exists()
    assert not ci.SCOPE_RECORD.exists()


@pytest.mark.parametrize("surface", ["unknown", "", "other"])
def test_surface_is_required_and_validated_before_probe(registration, surface):
    with pytest.raises(ci.ClientInstallationError, match="--surface"):
        repair(registration, surface=surface)
    assert not registration.calls
    assert_unchanged(registration)


def test_profile_conflict_is_rejected_before_probe(registration, tmp_path):
    with pytest.raises(ci.ClientInstallationError, match="manages Codex"):
        repair(registration, config_dir=tmp_path / "different")
    assert not registration.calls
    assert_unchanged(registration)


@pytest.mark.parametrize("profile_kind", ["missing", "file", "inaccessible"])
def test_invalid_profile_never_creates_or_probes_a_client(registration, monkeypatch, profile_kind):
    (registration.profile / "config.toml").unlink()
    if profile_kind == "inaccessible":
        original_access = ci.os.access
        monkeypatch.setattr(ci.os, "access", lambda path, mode:
                            False if path == registration.profile else original_access(path, mode))
    else:
        registration.profile.rmdir()
        if profile_kind == "file":
            registration.profile.write_text("not a directory")
    with pytest.raises(ci.ClientInstallationError, match="existing, accessible"):
        repair(registration)
    assert not registration.calls
    assert (registration.records.read_bytes(), registration.scopes.read_bytes()) == registration.original


def test_missing_selected_executable_does_not_fallback(registration, tmp_path):
    with pytest.raises(ci.ClientInstallationError, match="not an executable"):
        repair(registration, executable=tmp_path / "missing.exe")
    assert not registration.calls
    assert_unchanged(registration)


@pytest.mark.parametrize("failure", ["old", "unknown", "nonzero", "timeout", "oserror"])
def test_failed_version_check_leaves_both_records_unchanged(registration, monkeypatch, failure):
    def rejected(argv, **kwargs):
        assert argv == [str(registration.executable), "--version"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "oserror":
            raise OSError("cannot start selected executable")
        return SimpleNamespace(
            returncode=1 if failure == "nonzero" else 0,
            stdout="codex-cli 0.149.0" if failure == "old" else "unknown", stderr="",
        )
    monkeypatch.setattr(client_readiness.subprocess, "run", rejected)
    with pytest.raises(client_readiness.CodexVersionError):
        repair(registration)
    assert_unchanged(registration)


def test_repair_rolls_back_both_records_if_second_write_fails(registration, monkeypatch):
    original_write = ci._write_private_json

    def fail_scope(path, value):
        if path == registration.scopes:
            raise OSError("disk error")
        original_write(path, value)

    monkeypatch.setattr(ci, "_write_private_json", fail_scope)
    with pytest.raises(ci.ClientInstallationError, match="could not persist"):
        repair(registration)
    assert_unchanged(registration)


def test_profile_is_rechecked_after_version_probe(registration, monkeypatch, tmp_path):
    def change_scope(argv, **kwargs):
        payload = json.loads(registration.scopes.read_text())
        payload["clients"]["codex"]["config_dir"] = str(tmp_path / "concurrent-profile")
        registration.scopes.write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.163.0", stderr="")

    monkeypatch.setattr(client_readiness.subprocess, "run", change_scope)
    with pytest.raises(ci.ClientInstallationError, match="another Codex profile"):
        repair(registration)
    assert registration.records.read_bytes() == registration.original[0]
    assert "concurrent-profile" in registration.scopes.read_text()


def test_explicit_npm_node_is_used_without_shell_or_path(registration, monkeypatch):
    package = registration.executable.parent / "node_modules" / "@openai" / "codex"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@openai/codex", "bin": {"codex": "cli.js"},
    }))
    (package / "cli.js").write_text("// synthetic payload")
    shim = registration.executable.with_suffix(".cmd")
    shim.write_text("@echo off\n")
    shim.chmod(0o755)
    node = registration.executable.parent / "node.exe"
    node.write_bytes(b"MZ synthetic node")
    node.chmod(0o755)
    calls = []

    def version(argv, **kwargs):
        calls.append(argv)
        assert not kwargs.get("shell")
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.163.0", stderr="")

    monkeypatch.setattr(client_readiness.subprocess, "run", version)
    value = repair(registration, executable=shim, node_executable=node)
    assert calls == [[str(node), str(package / "cli.js"), "--version"]]
    assert value["client_node_executable"] == str(node)
    assert json.loads(registration.records.read_text())["clients"]["codex"]["node_executable"] == str(node)


@pytest.mark.parametrize("missing", ["--client-executable", "--surface", "--client-config-dir"])
def test_cli_requires_all_explicit_choices(registration, missing):
    options = {
        "--client-executable": str(registration.executable),
        "--surface": "desktop",
        "--client-config-dir": str(registration.profile),
    }
    argv = ["repair-codex-client"]
    for flag, value in options.items():
        if flag != missing:
            argv.extend([flag, value])
    result = CliRunner().invoke(launcher.app, argv)
    assert result.exit_code == 2
    assert not registration.calls
    assert_unchanged(registration)


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_reports_only_registration_and_version_verification(registration, dry_run):
    argv = ["repair-codex-client", "--client-executable", str(registration.executable),
            "--surface", "desktop", "--client-config-dir", str(registration.profile)]
    result = CliRunner().invoke(launcher.app, argv + (["--dry-run"] if dry_run else []))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["registration_updated"] is not dry_run
    if dry_run:
        assert_unchanged(registration)


def test_cli_reports_repair_failure_without_writing(registration):
    result = CliRunner().invoke(launcher.app, [
        "repair-codex-client", "--client-executable", str(registration.executable),
        "--surface", "unknown", "--client-config-dir", str(registration.profile),
    ])
    assert result.exit_code == 2
    assert_unchanged(registration)
