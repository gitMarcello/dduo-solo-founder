from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import pytest

from dduo_solo_founder import local_setup, project_config, project_secrets


def project(tmp_path: Path, name: str, key: str | None = None) -> tuple[Path, dict]:
    root = tmp_path / name
    root.mkdir()
    configured = local_setup.prepare_local_project(root)
    if key:
        project_secrets.save_project_secrets(configured["id"], {"OPENAI_API_KEY": key})
    return root, configured


def test_prepare_creates_identity_before_credentials_and_is_idempotent(tmp_path):
    root, configured = project(tmp_path, "new-project")
    assert configured["binding"] == "local"
    assert project_secrets.load_project_secrets(configured["id"]) == {}
    assert local_setup.prepare_local_project(root) == configured
    nested = root / "src"
    nested.mkdir()
    assert local_setup.prepare_local_project(nested) == configured
    assert not (nested / project_config.CONFIG_PATH).exists()


def test_listing_is_read_only_deduplicated_and_contains_no_secret_material(tmp_path):
    source, first = project(tmp_path, "source", "fake-key-shared")
    _, duplicate = project(tmp_path, "duplicate", "fake-key-shared")
    _, second = project(tmp_path, "other", "fake-key-other")
    project(tmp_path, "no-key")
    target = tmp_path / "target"
    target.mkdir()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    choices = local_setup.embedded_key_choices(target)
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert len(choices) == 2
    assert {"project_id": second["id"], "name": "other"} in choices
    assert any(choice in choices for choice in (
        {"project_id": first["id"], "name": "source"},
        {"project_id": duplicate["id"], "name": "duplicate"},
    ))
    serialized = json.dumps(choices)
    assert "fake-key" not in serialized
    assert str(source) not in serialized
    assert "hash" not in serialized
    assert not (target / project_config.CONFIG_PATH).exists()


def test_explicit_reuse_copies_only_embeddings_and_preserves_target_secrets(tmp_path):
    _, source = project(tmp_path, "source", "fake-key-shared")
    project_secrets.save_project_secrets(source["id"], {"DDUO_DATABASE_PASSWORD": "source-db"})
    target, destination = project(tmp_path, "target")
    project_secrets.save_project_secrets(destination["id"], {"DDUO_SESSION_SECRET": "target-session"})
    local_setup.embedded_key_choices(target)
    assert "OPENAI_API_KEY" not in project_secrets.load_project_secrets(destination["id"])
    local_setup.reuse_embeddings_key(target, source["id"].upper())
    assert project_secrets.load_project_secrets(destination["id"]) == {
        "OPENAI_API_KEY": "fake-key-shared", "DDUO_SESSION_SECRET": "target-session",
    }
    local_setup.reuse_embeddings_key(target, source["id"])
    assert project_secrets.load_project_secrets(source["id"])["DDUO_DATABASE_PASSWORD"] == "source-db"
    assert all(choice["project_id"] != destination["id"] for choice in local_setup.embedded_key_choices(target))


def test_reuse_never_overwrites_a_different_project_key(tmp_path):
    _, source = project(tmp_path, "source", "source-key")
    target, destination = project(tmp_path, "target", "destination-key")
    with pytest.raises(ValueError, match="not replaced"):
        local_setup.reuse_embeddings_key(target, source["id"])
    assert project_secrets.load_project_secrets(destination["id"])["OPENAI_API_KEY"] == "destination-key"


@pytest.mark.parametrize("condition", ["remote", "deployment", "retired", "copied", "unregistered", "invalid-id"])
def test_prepare_and_reuse_reject_unsafe_existing_identity(tmp_path, condition):
    target, configured = project(tmp_path, "target")
    path = target / project_config.CONFIG_PATH
    if condition == "remote":
        path.write_text(path.read_text().replace('binding = "local"', 'binding = "remote"'))
    elif condition == "deployment":
        path.write_text(path.read_text() + 'deployment = "remote"\n')
    elif condition == "retired":
        (target / local_setup.RETIRED_NODE_FILE).write_text("{}")
    elif condition == "copied":
        copied = tmp_path / "copy"
        shutil.copytree(target, copied)
        target = copied
    elif condition == "unregistered":
        project_config.unregister_project_config(configured["id"])
    else:
        path.write_text(path.read_text().replace(configured["id"], "not-a-uuid"))
    original = path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        local_setup.prepare_local_project(target)
    with pytest.raises((ValueError, RuntimeError)):
        local_setup.reuse_embeddings_key(target, configured["id"])
    with pytest.raises((ValueError, RuntimeError)):
        local_setup.embedded_key_choices(target)
    assert path.read_bytes() == original


@pytest.mark.parametrize("condition", ["missing", "missing-config", "remote", "deployment", "retired", "wrong-id", "fingerprint", "no-key"])
def test_stale_sources_are_not_offered_or_used(tmp_path, condition):
    source, configured = project(tmp_path, "source", "source-key")
    target, _ = project(tmp_path, "target")
    path = source / project_config.CONFIG_PATH
    if condition == "missing":
        shutil.rmtree(source)
    elif condition == "missing-config":
        path.unlink()
    elif condition == "remote":
        path.write_text(path.read_text().replace('binding = "local"', 'binding = "remote"'))
    elif condition == "deployment":
        path.write_text(path.read_text() + 'deployment = "remote"\n')
    elif condition == "retired":
        (source / local_setup.RETIRED_NODE_FILE).write_text("{}")
    elif condition == "wrong-id":
        path.write_text(path.read_text().replace(configured["id"], "11111111-1111-4111-8111-111111111111"))
    elif condition == "fingerprint":
        registry = project_config._read_registry(project_config.REGISTRY_PATH)
        registry["projects"][configured["id"]]["root_fingerprint"] = "invalid"
        project_config._write_registry(project_config.REGISTRY_PATH, registry)
    else:
        project_secrets.replace_project_secrets(configured["id"], {})
    if condition == "fingerprint":
        with pytest.raises(RuntimeError, match="fingerprint"):
            local_setup.embedded_key_choices(target)
    else:
        assert local_setup.embedded_key_choices(target) == []
    with pytest.raises((ValueError, RuntimeError)):
        local_setup.reuse_embeddings_key(target, configured["id"])


@pytest.mark.parametrize("value", ["", "../source", "invalid", "\n11111111-1111-4111-8111-111111111111", None, "x\x00"])
def test_invalid_source_identifiers_do_not_create_target(tmp_path, value):
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError, match="identifier"):
        local_setup.reuse_embeddings_key(target, value)
    assert not (target / project_config.CONFIG_PATH).exists()


def test_missing_root_and_invalid_registry_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="already exist"):
        local_setup.prepare_local_project(tmp_path / "missing")
    target = tmp_path / "target"
    target.mkdir()
    project_config.REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    project_config.REGISTRY_PATH.write_text("invalid json")
    with pytest.raises(RuntimeError, match="invalid.*registry"):
        local_setup.embedded_key_choices(target)


def test_unregistered_secret_store_and_global_key_are_never_sources(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    project_secrets.save_project_secrets(
        "11111111-1111-4111-8111-111111111111", {"OPENAI_API_KEY": "orphan-key"},
    )
    project_secrets.LEGACY_ENV_FILE.write_text("OPENAI_API_KEY=legacy-key\n")
    assert local_setup.embedded_key_choices(target) == []
    destination = local_setup.prepare_local_project(target)
    with pytest.raises(ValueError, match="no longer available"):
        local_setup.reuse_embeddings_key(target, "11111111-1111-4111-8111-111111111111")
    assert project_secrets.load_project_secrets(destination["id"]) == {}


@pytest.mark.parametrize("value", ["short", "long-enough\x00-corrupt"])
def test_invalid_stored_keys_are_not_offered_or_copied(tmp_path, value):
    _, source = project(tmp_path, "source", value)
    target, destination = project(tmp_path, "target")
    assert local_setup.embedded_key_choices(target) == []
    with pytest.raises(ValueError, match="no longer available"):
        local_setup.reuse_embeddings_key(target, source["id"])
    assert project_secrets.load_project_secrets(destination["id"]) == {}


def test_reuse_waits_for_manual_key_write_and_preserves_its_new_value(tmp_path):
    _, source = project(tmp_path, "source", "source-key")
    target, destination = project(tmp_path, "target")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with local_setup.embedding_key_lock(destination["id"]):
            pending = pool.submit(local_setup.reuse_embeddings_key, target, source["id"])
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.1)
            project_secrets.save_project_secrets(destination["id"], {"OPENAI_API_KEY": "manual-key"})
        with pytest.raises(ValueError, match="not replaced"):
            pending.result(timeout=10)
    assert project_secrets.load_project_secrets(destination["id"])["OPENAI_API_KEY"] == "manual-key"


def test_prepare_waits_for_key_write_and_preserves_its_new_value(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _, project_id = project_config.new_project_config(target)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with local_setup.embedding_key_lock(project_id):
            pending = pool.submit(local_setup.prepare_local_project, target)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.1)
            project_secrets.save_project_secrets(project_id, {"OPENAI_API_KEY": "copied-key"})
        assert pending.result(timeout=10)["id"] == project_id
    assert project_secrets.load_project_secrets(project_id)["OPENAI_API_KEY"] == "copied-key"


@pytest.mark.parametrize("project_id", ["p1", "../outside", "/absolute/path"])
def test_key_lock_uses_native_traversal_safe_secret_path(project_id):
    expected = project_secrets.project_secret_dir(project_id) / "embeddings-key.json.lock"
    with local_setup.embedding_key_lock(project_id):
        assert expected.is_file()
        expected.resolve().relative_to(project_secrets.PROJECT_SECRETS_DIR.resolve())
    assert expected.parent.name not in {project_id, "outside"}


def test_key_lock_rejects_empty_project_identifier():
    with pytest.raises(ValueError, match="project id"):
        local_setup.embedding_key_lock(" ")
