from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dduo_solo_founder import project_activation, project_config, project_secrets
from dduo_solo_founder.models import Base


def assert_private_file(path: Path) -> None:
    """Assert user-only POSIX permissions where mode bits are meaningful."""
    assert path.is_file()
    if os.name == "nt":
        # Native Windows exposes synthetic POSIX mode bits; access is governed by ACLs.
        return
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR


class FakeEmbeddings:
    def __init__(self):
        self.settings = SimpleNamespace(
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            embedding_index_version="test-v1",
            task_embedding_index_version="v1",
            task_index_max_characters=16_000,
            task_index_max_utf8_bytes=7_500,
        )
        self.records: dict[str, dict] = {}
        self.search_results: list[dict] = []
        self.search_error: Exception | None = None
        self.inventory_points: dict[str, dict] = {}
        self.deleted_points: list[str] = []
        self.reset_projects: list[str] = []

    def upsert(self, project_id: str, memory_id: str, text: str, payload: dict) -> None:
        self.records[memory_id] = {"project_id": project_id, "text": text, **payload}

    def search(self, project_id: str, query: str, limit: int) -> list[dict]:
        if self.search_error:
            raise self.search_error
        return self.search_results[:limit]

    def collection(self, project_id: str) -> str:
        return f"test_{project_id}"

    def inventory(self, project_id: str) -> dict[str, dict]:
        return self.inventory_points

    def delete_points(self, project_id: str, memory_ids: list[str]) -> None:
        self.deleted_points.extend(memory_ids)

    def reset_collection(self, project_id: str) -> str:
        self.reset_projects.append(project_id)
        return self.collection(project_id)


@pytest.fixture(autouse=True)
def isolate_port_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(project_activation, "ACTIVATION_DIR", tmp_path / "activation")
    monkeypatch.setattr(
        project_config, "REGISTRY_PATH", tmp_path / "dduo-solo-founder/projects.json"
    )
    monkeypatch.setattr(project_config, "port_available", lambda _: True)
    config = tmp_path / "dduo-solo-founder/config"
    monkeypatch.setattr(project_secrets, "CONFIG_DIR", config)
    monkeypatch.setattr(project_secrets, "LEGACY_ENV_FILE", config / "env")
    monkeypatch.setattr(
        project_secrets,
        "RETIRED_LEGACY_ENV_FILE",
        config / "env.alpha-retired",
    )
    monkeypatch.setattr(
        project_secrets,
        "PROJECT_SECRETS_DIR",
        config / "project-secrets",
    )


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(db_factory, monkeypatch):
    from dduo_solo_founder.db import get_session
    from dduo_solo_founder.main import app

    fake_embeddings = FakeEmbeddings()

    async def override_session():
        async with db_factory() as session:
            yield session

    monkeypatch.setattr("dduo_solo_founder.main.embedding_service", lambda: fake_embeddings)
    app.dependency_overrides[get_session] = override_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, fake_embeddings
    app.dependency_overrides.clear()


def project_payload(**overrides):
    payload = {
        "id": str(uuid.uuid4()),
        "name": "dDuo Solo Founder Test",
        "root_path": "/tmp/dduo-solo-founder-test",
        "cause": "Keep project context available",
        "principles": ["Be accurate"],
        "objectives": ["Ship safely"],
    }
    payload.update(overrides)
    return payload
