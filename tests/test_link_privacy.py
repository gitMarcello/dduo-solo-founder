from __future__ import annotations

import base64
import copy
import hashlib
import io
import json

import pytest

from dduo_solo_founder import hooks
from dduo_solo_founder.artifacts import register_artifact
from dduo_solo_founder.link_privacy import (
    contains_dashboard_access_tokens,
    redact_dashboard_access_tokens,
    redacted_render_version,
)
from dduo_solo_founder.schemas import (
    ArtifactCreate,
    BrowserSessionExchange,
    CompactionRecord,
    ContextObservation,
    RawEventCreate,
    StopCheck,
    TurnBegin,
    TurnCommit,
)


TOKEN = "dduo_link_" + "Az09_-" * 7 + "Z"
URL = f"https://memory.example/?project=project-one&access_token={TOKEN}&work=task-one"


@pytest.mark.parametrize(
    "token",
    [
        TOKEN,
        TOKEN + "extra",
        TOKEN.replace("_", "%5F"),
        TOKEN.replace("_", "%5f").replace("A", "%41"),
        "".join(f"%{ord(character):02x}" for character in TOKEN),
        "".join(f"%25{ord(character):02X}" for character in TOKEN),
    ],
)
def test_redaction_is_length_preserving_for_raw_and_encoded_links(token):
    original = f"Apri [attività](https://memory.example/?access_token={token}&work=one). 🧠"
    result = redact_dashboard_access_tokens(original)

    assert result != original
    assert token not in result
    assert len(result) == len(original)
    assert len(result.encode("utf-8")) == len(original.encode("utf-8"))
    assert result.startswith("Apri [attività](https://memory.example/?access_token=")
    assert result.endswith("&work=one). 🧠")
    assert not contains_dashboard_access_tokens(result)
    assert redact_dashboard_access_tokens(result) == result


def test_redaction_does_not_touch_other_tokens_short_prefixes_or_user_content():
    value = {
        "normal": "Memoria e sprint — 🧠",
        "incomplete": "dduo_link_" + "a" * 42,
        "other": "dduo_web_" + "a" * 43,
        "device": "dduo_dev_" + "a" * 43,
        "identity": [None, True, 42, 1.5],
    }
    assert redact_dashboard_access_tokens(value) == value
    assert not contains_dashboard_access_tokens(value)


def test_recursive_redaction_includes_nested_values_and_keys_without_mutating_input():
    value = {"result": [URL, {TOKEN: {"response": TOKEN}}, None], "count": 2}
    original = copy.deepcopy(value)
    result = redact_dashboard_access_tokens(value)

    assert value == original
    assert TOKEN not in json.dumps(result)
    assert contains_dashboard_access_tokens(value)
    assert not contains_dashboard_access_tokens(result)
    assert result["count"] == 2
    assert "project=project-one" in result["result"][0]


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (TurnBegin, {"session_id": "s", "external_id": "t", "user_prompt": URL}, "user_prompt"),
        (StopCheck, {"assistant_response": URL}, "assistant_response"),
        (StopCheck, {"topic_changed": True, "segment_summary": URL}, "segment_summary"),
        (TurnCommit, {"assistant_response": URL}, "assistant_response"),
        (TurnCommit, {"assistant_response": "done", "segment_summary": URL}, "segment_summary"),
        (RawEventCreate, {"event_type": "tool_result", "payload": {"result": [URL]}}, "payload"),
        (CompactionRecord, {"session_id": "s", "phase": "post", "summary": URL}, "summary"),
    ],
)
def test_api_inputs_from_unupdated_clients_redact_before_persistence(model, payload, field):
    original = copy.deepcopy(payload)
    result = model.model_validate(payload)

    assert TOKEN not in json.dumps(result.model_dump(mode="json"))
    assert getattr(result, field) == redact_dashboard_access_tokens(payload[field])
    assert payload == original


def test_browser_authentication_token_is_not_redacted_by_capture_rules():
    # Browser auth must still receive the actual credential, not the copy used
    # for memory. Redaction is not a global BaseModel or transport transform.
    assert BrowserSessionExchange(ticket=TOKEN).ticket == TOKEN


def test_context_observation_redacts_valid_snapshot_without_breaking_measurements():
    content = f"[Attività]({URL}) — 🧠"
    byte_count = len(content.encode("utf-8"))
    payload = {
        "event_id": "link-observation",
        "operation": "context.mcp_tool_result",
        "scope": "requested",
        "client": "codex",
        "characters": len(content),
        "utf8_bytes": byte_count,
        "estimated_tokens": (byte_count + 3) // 4,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {"result": byte_count},
        "components": [{
            "name": "result", "utf8_bytes": byte_count,
            "estimated_tokens": (byte_count + 3) // 4,
        }],
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "producer_version": "test",
        "render_version": "r" * 80,
        "occurred_at": "2026-09-12T12:00:00+00:00",
    }

    observation = ContextObservation.model_validate(payload)

    assert observation.content == redact_dashboard_access_tokens(content)
    assert observation.content_sha256 == hashlib.sha256(observation.content.encode()).hexdigest()
    assert observation.content_sha256 != payload["content_sha256"]
    assert observation.characters == payload["characters"]
    assert observation.component_bytes == payload["component_bytes"]
    assert len(observation.render_version) == 80
    assert observation.render_version.endswith("+access-link-redacted-v1")
    assert ContextObservation.model_validate(observation.model_dump()) == observation
    assert redacted_render_version(observation.render_version) == observation.render_version
    assert StopCheck(context_observations=[payload]).context_observations[0] == observation
    with pytest.raises(ValueError, match="content_sha256"):
        ContextObservation.model_validate({**payload, "content_sha256": "0" * 64})


def test_short_render_label_marks_redaction_once():
    label = redacted_render_version("mcp-v1")
    assert label == "mcp-v1+access-link-redacted-v1"
    assert redacted_render_version(label) == label


def test_hook_input_redacts_before_prompt_stop_staging_and_network(monkeypatch):
    payload = {
        "session_id": "session-one",
        "turn_id": "turn-one",
        "prompt": URL,
        "assistant_response": f"[Task]({URL})",
        "attachments": [{"summary": URL}],
    }
    monkeypatch.setattr(hooks.sys, "stdin", io.StringIO(json.dumps(payload)))

    captured = hooks.read_input()

    assert captured == redact_dashboard_access_tokens(payload)
    assert captured["session_id"] == "session-one"
    assert captured["turn_id"] == "turn-one"
    assert TOKEN not in json.dumps(captured)


@pytest.mark.parametrize(
    "fields",
    [
        {"source_uri": URL},
        {"summary": URL},
        {"extracted_text": URL},
        {"metadata": {"source": [URL]}},
        {"filename": f"{TOKEN}.md"},
        {
            "filename": "chat.md",
            "mime_type": "text/markdown",
            "content_base64": base64.b64encode(URL.encode()).decode(),
        },
        {
            # Do not trust the MIME declaration to decide whether UTF-8 text
            # is safe to archive. The original bytes/hash must not be altered.
            "mime_type": "application/octet-stream",
            "content_base64": base64.b64encode(URL.replace("_", "%5f").encode()).decode(),
        },
        {"filename": "chat.txt", "content_base64": base64.b64encode(URL.encode("utf-16")).decode()},
        {"filename": "chat.txt", "content_base64": base64.b64encode(URL.encode("utf-32")).decode()},
    ],
)
async def test_artifacts_with_access_links_are_rejected_before_database_access(fields):
    payload = ArtifactCreate(**fields)
    original = payload.model_dump()

    with pytest.raises(ValueError, match="private dashboard access link") as error:
        await register_artifact(None, "test-project", payload)

    assert TOKEN not in str(error.value)
    assert payload.model_dump() == original
