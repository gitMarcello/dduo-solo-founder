"""Small, host-local onboarding operations; never start services or borrow auth."""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from pathlib import Path

from . import project_config, project_secrets


RETIRED_NODE_FILE = Path(".dduo-solo-founder/retired-node.json")


def _project_id(value: object) -> str:
    if not isinstance(value, str) or any(character in value for character in "\x00\r\n"):
        raise ValueError("a valid source project identifier is required")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise ValueError("a valid source project identifier is required") from exc


def _workspace(root: Path) -> Path:
    if not root.expanduser().exists():
        raise ValueError("the project folder must already exist")
    resolved = project_config.find_workspace_root(root)
    if not resolved.is_dir():
        raise ValueError("the project folder must already exist")
    if (resolved / RETIRED_NODE_FILE).exists() or (resolved / RETIRED_NODE_FILE).is_symlink():
        raise ValueError("this local memory node was retired; use its remote binding")
    return resolved


def _local_project(root: Path) -> dict:
    path = root / project_config.CONFIG_PATH
    if path.is_symlink():
        raise ValueError("project configuration must not be a symbolic link")
    project = project_config.load_project(root)
    _project_id(project.get("id"))
    if project_config.project_binding_kind(project) != "local":
        raise ValueError("this project uses remote memory; local setup cannot replace it")
    if project.get("deployment", "local") != "local":
        raise ValueError("this project is a VPS deployment; use its dedicated setup")
    return project


def prepare_local_project(root: Path) -> dict:
    """Prepare isolated identity before credentials, without launching anything."""
    root = _workspace(root)
    path = root / project_config.CONFIG_PATH
    if path.exists() or path.is_symlink():
        project = _local_project(root)
        # Never let new_project_config implicitly register a copied identity.
        project_config.validate_project_registration(project["id"], root)
    else:
        project_config.new_project_config(root)
        project = _local_project(root)
    with embedding_key_lock(project["id"]):
        project_secrets.ensure_project_secret_environment(project["id"])
    return project


def _local_sources(root: Path):
    """Read validated registry claims, not arbitrary secret directories."""
    registry = project_config._read_registry(project_config.REGISTRY_PATH)
    projects = registry["projects"]
    for raw_id, entry in projects.items():
        try:
            project_id = _project_id(raw_id)
            claim = project_config._registered_root_claim(project_id, entry)
            source = Path(claim["root_path"])
            if source == root or not source.is_dir():
                continue
            # A stale registry child must not resolve to its configured parent.
            if not (source / project_config.CONFIG_PATH).is_file() or _workspace(source) != source:
                continue
            project = _local_project(source)
            if project["id"] != project_id:
                continue
            if project_config._claim_conflict(projects, project_id, claim):
                continue
            key = project_secrets.load_project_secrets(project_id).get("OPENAI_API_KEY", "")
            if len(key) >= 8 and not any(character in key for character in "\x00\r\n"):
                yield project, key
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            # An unrelated stale or inaccessible checkout is not a credential source.
            continue


def embedded_key_choices(root: Path) -> list[dict[str, str]]:
    """Offer names only; discovering an existing key never copies it."""
    root = _workspace(root)
    if (root / project_config.CONFIG_PATH).exists() or (root / project_config.CONFIG_PATH).is_symlink():
        project = _local_project(root)
        projects = project_config._read_registry(project_config.REGISTRY_PATH)["projects"]
        claim = project_config._root_claim(root)
        if project["id"] not in projects:
            raise RuntimeError("this project is not registered on this computer")
        project_config._assert_matching_claim(project["id"], projects[project["id"]], claim)
        if project_config._claim_conflict(projects, project["id"], claim):
            raise RuntimeError("this checkout is claimed by another project")
    choices: list[dict[str, str]] = []
    seen: set[str] = set()
    for project, key in _local_sources(root):
        if key in seen:
            continue
        seen.add(key)
        choices.append({"project_id": project["id"], "name": str(project.get("name") or "Project")})
    return choices


def embedding_key_lock(project_id: str) -> AbstractContextManager[None]:
    """Serialize explicit embedding-key writes for one private project store."""
    path = project_secrets.project_secret_dir(project_id) / "embeddings-key.json"
    return project_config.portable_file_lock(path)


def reuse_embeddings_key(root: Path, source_project_id: str) -> None:
    """Copy only the chosen embedding credential after explicit user consent."""
    source_id = _project_id(source_project_id)
    root = _workspace(root)
    target = _local_project(root)
    project_config.validate_project_registration(target["id"], root)
    selected = next(
        ((project, key) for project, key in _local_sources(root) if project["id"] == source_id),
        None,
    )
    if selected is None:
        raise ValueError("the selected local embedding key is no longer available")
    _, key = selected
    with embedding_key_lock(target["id"]):
        existing = project_secrets.load_project_secrets(target["id"]).get("OPENAI_API_KEY")
        if existing and existing != key:
            raise ValueError("this project already has a different embedding key; it was not replaced")
        if not existing:
            project_secrets.save_project_secrets(target["id"], {"OPENAI_API_KEY": key})
