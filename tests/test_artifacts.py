from __future__ import annotations

import base64
import uuid

import pytest

from dduo_solo_founder import artifacts
from dduo_solo_founder.artifacts import (
    attach_artifact_to_task,
    plan_artifacts,
    register_artifact,
    serialize_artifact,
    task_artifacts,
)
from dduo_solo_founder.models import Project, Session, Task, Turn, TurnArtifact
from dduo_solo_founder.schemas import ArtifactCreate
from dduo_solo_founder.task_views import task_view


async def test_artifacts_are_deduplicated_and_linked_to_turns(db_factory):
    async with db_factory() as db:
        project = Project(id=str(uuid.uuid4()), name="P", root_path="/tmp/p")
        session = Session(project_id=project.id, client="claude", external_id="s")
        db.add_all([project, session])
        await db.flush()
        turn = Turn(
            project_id=project.id,
            session_id=session.id,
            external_id="t",
            user_prompt="Read this",
        )
        db.add(turn)
        await db.flush()
        payload = ArtifactCreate(
            turn_id=turn.id,
            kind="document",
            filename="notes.md",
            mime_type="text/markdown",
            content_base64=base64.b64encode(b"release notes").decode(),
            extracted_text="release notes",
        )
        first, created = await register_artifact(db, project.id, payload)
        second, duplicated = await register_artifact(db, project.id, payload)
        await db.commit()
        assert created is True and duplicated is False and first.id == second.id
        assert await db.get(TurnArtifact, (turn.id, first.id))
        assert serialize_artifact(first)["extracted_text"] == "release notes"
        assert serialize_artifact(
            first,
            include_extracted_text=False,
            has_extracted_text=True,
        )["has_extracted_text"] is True


async def test_artifact_validation_rejects_invalid_sources(db_factory, monkeypatch):
    async with db_factory() as db:
        with pytest.raises(ValueError, match="valid base64"):
            await register_artifact(
                db,
                "p",
                ArtifactCreate(filename="bad", content_base64="not base64"),
            )
        with pytest.raises(ValueError, match="requires content"):
            await register_artifact(db, "p", ArtifactCreate())
        monkeypatch.setattr(
            artifacts,
            "get_settings",
            lambda: type("Settings", (), {"artifact_max_bytes": 1})(),
        )
        with pytest.raises(ValueError, match="configured size limit"):
            await register_artifact(
                db,
                "p",
                ArtifactCreate(content_base64=base64.b64encode(b"too large").decode()),
            )
        with pytest.raises(LookupError, match="turn not found"):
            await register_artifact(
                db,
                "p",
                ArtifactCreate(turn_id=str(uuid.uuid4()), summary="Known source"),
            )


async def test_working_task_artifacts_return_light_metadata_without_extracted_text(db_factory):
    async with db_factory() as db:
        project = Project(id=str(uuid.uuid4()), name="P", root_path="/tmp/p")
        task = Task(project_id=project.id, title="Inspect attachment")
        db.add_all([project, task])
        await db.flush()
        for index in range(12):
            await attach_artifact_to_task(
                db,
                task,
                ArtifactCreate(
                    filename=f"large-{index}-" + "f" * 300,
                    content_base64=base64.b64encode(f"binary-{index}".encode()).decode(),
                    extracted_text="x" * 100_000,
                    source_uri="https://example.test/" + "u" * 500,
                    summary="s" * 1_000,
                ),
            )
        await db.commit()

        working_rows = (
            await task_artifacts(
                db,
                [task.id],
                include_extracted_text=False,
                limit_per_task=10,
            )
        )[task.id]
        assert len(working_rows) == 10 and working_rows.total == 12
        working = working_rows[0]
        assert working["has_extracted_text"] is True
        assert "extracted_text" not in working
        assert len(working["filename"]) == 200
        assert working["filename"].endswith("f" * 67)
        assert len(working["source_uri"]) == 300
        assert working["source_uri"].endswith("u" * 100)
        assert len(working["summary"]) == 250
        assert working["summary"].endswith("s" * 83)
        assert set(working["truncated_fields"]) == {"filename", "source_uri", "summary"}
        view = task_view(task, "working", working_rows)
        assert len(view["attachments"]) == 10
        assert view["attachments_total"] == 12
        assert "attachments" in view["truncated_fields"]

        full_rows = (await task_artifacts(db, [task.id]))[task.id]
        assert len(full_rows) == 12
        assert full_rows[0]["extracted_text"] == "x" * 100_000
        assert "has_extracted_text" not in full_rows[0]
        assert await task_artifacts(db, []) == {}
        assert await plan_artifacts(db, []) == {}
