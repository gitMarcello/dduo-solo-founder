from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from dduo_solo_founder import cli_bridge, project_config, project_secrets
from dduo_solo_founder.bridge_auth import project_bridge_token
from dduo_solo_founder.schemas import MemoryConsolidationPayload


SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {"type": "object"}}},
    "required": ["topics"],
}


@pytest.fixture(autouse=True)
def accept_explicit_test_project_claims(monkeypatch):
    """Most unit fixtures model an already activated project without a host registry."""
    monkeypatch.setattr(
        cli_bridge,
        "validate_project_registration",
        lambda *_args, **_kwargs: {},
    )
    # Native executable discovery is covered by test_client_readiness and
    # test_native_process. A unit fixture must never probe or sign into the
    # maintainer's real Desktop installation when PATH is mocked absent.
    monkeypatch.setattr(
        cli_bridge, "resolve_codex_executable", lambda: cli_bridge.shutil.which("codex"),
    )


def test_setup_cannot_inspect_or_mutate_a_copied_local_project(monkeypatch, tmp_path):
    root = tmp_path / "copied"
    config = root / ".dduo-solo-founder" / "project.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'version = 2\nid = "p1"\nname = "Copied"\nbinding = "local"\n'
        "api_port = 18001\nweb_port = 20001\n"
    )
    monkeypatch.setattr(
        cli_bridge,
        "validate_project_registration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("already claimed by another checkout")
        ),
    )
    writes = []
    monkeypatch.setattr(
        cli_bridge,
        "save_project_secrets",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )
    setup = cli_bridge.SetupService()

    assert setup._project_status(root) == {"ready": False}
    with pytest.raises(ValueError, match="Initialize this project"):
        setup.save_openai_key("project-secret", str(root))
    with pytest.raises(ValueError, match="Activate this project"):
        setup.configure_backup(str(root))
    assert writes == []


def bridge_response_for_error(error: Exception) -> tuple[int, dict]:
    class Runner:
        def generate(self, request):
            raise error

    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret", runner=Runner())
    sent = []
    handler._send = lambda status, value: sent.append((status, value))
    body = json.dumps({"provider": "codex"}).encode()
    handler.path = "/v1/generate"
    handler.headers = {
        "Authorization": "Bearer secret",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    return sent[0]


def test_bridge_rejects_public_network_peers_before_bearer_authentication():
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret")
    handler.client_address = ("8.8.8.8", 4242)
    handler.path = "/health"
    handler.headers = {"Authorization": "Bearer secret"}
    sent = []
    handler._send = lambda status, value: sent.append((status, value))

    handler.do_GET()

    assert sent == [(403, {"error": "untrusted_network"})]


def test_codex_runner_uses_ephemeral_schema_output(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")

    def run(command, **kwargs):
        schema_path = Path(command[command.index("--output-schema") + 1])
        written_schema = json.loads(schema_path.read_text())
        action = written_schema["$defs"]["MemoryActionPayload"]
        topic = written_schema["$defs"]["ConsolidatedTopicPayload"]
        assert set(action["required"]) == set(action["properties"])
        assert action["properties"]["metadata"] == {
            "additionalProperties": False,
            "properties": {},
            "required": [],
            "title": "Metadata",
            "type": "object",
        }
        assert set(topic["required"]) == set(topic["properties"])
        assert topic["properties"]["language"]["enum"] == ["it", "en"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"topics": []}')
        assert "--ephemeral" in command and "--ignore-rules" in command
        assert "--json" in command
        assert "--ignore-user-config" in command
        disabled = {
            command[index + 1] for index, value in enumerate(command[:-1]) if value == "--disable"
        }
        assert disabled == {"shell_tool", "unified_exec"}
        assert 'web_search="disabled"' in command
        assert command[command.index("--model") + 1] == "gpt-5.6-terra"
        config_values = {
            command[index + 1] for index, value in enumerate(command[:-1]) if value == "--config"
        }
        assert config_values == {
            'web_search="disabled"',
            'model_reasoning_effort="medium"',
        }
        assert kwargs["cwd"] != Path.cwd()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_bridge.subprocess, "run", run)
    result = cli_bridge.CliRunner().generate(
        {
            "provider": "codex",
            "instructions": "Segment topics",
            "input": {"turns": []},
            "schema": MemoryConsolidationPayload.model_json_schema(),
        }
    )
    assert result["ok"] and result["output"] == {"topics": []}
    assert result["model"] == "gpt-5.6-terra"


def test_codex_runner_pins_project_scoped_codex_home(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    captured = {}

    def project_environment(project_id, base):
        assert project_id == "project-one"
        return {**base, "CODEX_HOME": "/private/project-one/codex"}

    def run(command, **kwargs):
        captured["environment"] = kwargs["env"]
        Path(command[command.index("--output-last-message") + 1]).write_text('{"topics": []}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_bridge, "codex_environment", project_environment)
    monkeypatch.setattr(cli_bridge.subprocess, "run", run)
    result = cli_bridge.CliRunner().generate(
        {
            "provider": "codex",
            "project_id": "project-one",
            "instructions": "Segment",
            "input": {},
            "schema": SCHEMA,
        }
    )
    assert result["ok"] is True
    assert captured["environment"]["CODEX_HOME"] == "/private/project-one/codex"


def test_codex_runner_normalizes_final_jsonl_usage(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")

    def run(command, **kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"topics": []}')
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "model": "gpt-5-codex",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 10,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(cli_bridge.subprocess, "run", run)
    result = cli_bridge.CliRunner().generate(
        {"provider": "codex", "instructions": "Segment", "input": {}, "schema": SCHEMA}
    )
    assert result["model"] == "gpt-5-codex"
    assert result["usage"] == {
        "input_tokens": 100,
        "cached_input_tokens": 10,
        "output_tokens": 20,
        "reasoning_tokens": 5,
    }


def test_invalid_codex_json_keeps_content_free_telemetry(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")

    def run(command, **kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("not-json: private model output")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "model": "gpt-5-codex",
                    "usage": {"cached_input_tokens": 17},
                }
            ),
            stderr="private stderr",
        )

    monkeypatch.setattr(cli_bridge.subprocess, "run", run)
    with pytest.raises(cli_bridge.CliStructuredOutputError) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "Segment", "input": {}, "schema": SCHEMA}
        )
    payload = raised.value.response_payload()
    assert payload["error"] == "invalid_structured_output"
    assert payload["provider"] == "codex" and payload["model"] == "gpt-5-codex"
    assert payload["usage"] == {"cached_input_tokens": 17}
    assert isinstance(payload["duration_seconds"], float)
    assert "private" not in json.dumps(payload)


def test_codex_nonzero_preserves_content_free_telemetry(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=7,
            stdout=json.dumps(
                {
                    "type": "turn.failed",
                    "model": "gpt-5-codex",
                    "usage": {"input_tokens": 12, "cached_input_tokens": 2},
                }
            ),
            stderr="private codex failure detail",
        ),
    )

    with pytest.raises(cli_bridge.CliExecutionError) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "Segment", "input": {}, "schema": SCHEMA}
        )

    assert "private codex failure detail" in str(raised.value)
    payload = raised.value.response_payload()
    assert payload["error"] == "cli_unavailable"
    assert payload["provider"] == "codex" and payload["model"] == "gpt-5-codex"
    assert payload["usage"] == {"input_tokens": 12, "cached_input_tokens": 2}
    assert isinstance(payload["duration_seconds"], float)
    assert "private" not in json.dumps(payload)
    assert bridge_response_for_error(raised.value) == (503, payload)


def test_claude_runner_extracts_structured_output(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "interactive-session")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "expired-session-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid-api-key")
    monkeypatch.setenv("OPENAI_API_KEY", "paid-api-key")

    def run(command, **kwargs):
        assert "--safe-mode" in command and "--bare" not in command
        assert "--no-session-persistence" in command
        assert command[command.index("--tools") + 1] == ""
        assert "CLAUDECODE" not in kwargs["env"]
        assert "CLAUDE_CODE_SESSION_ID" not in kwargs["env"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {"topics": []}}),
            stderr="",
        )

    monkeypatch.setattr(cli_bridge.subprocess, "run", run)
    result = cli_bridge.CliRunner().generate(
        {
            "provider": "claude",
            "instructions": "Segment topics",
            "input": "{}",
            "schema": SCHEMA,
        }
    )
    assert result["output"] == {"topics": []}


def test_claude_runner_preserves_usage_metadata(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "structured_output": {"topics": []},
                    "total_cost_usd": "0.001234",
                    "modelUsage": {"claude-sonnet-4-6": {"costUSD": 0.001234}},
                    "usage": {
                        "input_tokens": 30,
                        "cache_read_input_tokens": 4,
                        "cache_creation_input_tokens": 2,
                        "output_tokens": 3,
                    },
                }
            ),
            stderr="",
        ),
    )
    result = cli_bridge.CliRunner().generate(
        {"provider": "claude", "instructions": "Segment", "input": {}, "schema": SCHEMA}
    )
    assert result["model"] == "claude-sonnet-4-6"
    assert result["usage"] == {
        "input_tokens": 30,
        "cached_input_tokens": 4,
        "cache_write_input_tokens": 2,
        "output_tokens": 3,
    }
    assert result["client_cost_usd"] == "0.001234"
    assert result["cost_source"] == "claude_code_client_estimate"


def test_claude_nonzero_preserves_content_free_telemetry(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "model": "claude-sonnet",
                    "result": "private claude failure detail",
                    "usage": {
                        "input_tokens": 21,
                        "cache_read_input_tokens": 8,
                        "output_tokens": 1,
                    },
                }
            ),
            stderr="",
        ),
    )

    with pytest.raises(cli_bridge.CliExecutionError) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "claude", "instructions": "Segment", "input": {}, "schema": SCHEMA}
        )

    assert "private claude failure detail" in str(raised.value)
    payload = raised.value.response_payload()
    assert payload["error"] == "cli_unavailable"
    assert payload["provider"] == "claude" and payload["model"] == "claude-sonnet"
    assert payload["usage"] == {
        "input_tokens": 21,
        "cached_input_tokens": 8,
        "output_tokens": 1,
    }
    assert "private" not in json.dumps(payload)
    assert bridge_response_for_error(raised.value) == (503, payload)


def test_timeout_preserves_partial_stdout_telemetry(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    partial_stdout = json.dumps(
        {
            "type": "turn.completed",
            "model": "gpt-5-codex",
            "usage": {"cached_input_tokens": 13},
        }
    ).encode()
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            cli_bridge.subprocess.TimeoutExpired(
                "codex", 10, output=partial_stdout, stderr=b"private timeout stderr"
            )
        ),
    )

    with pytest.raises(cli_bridge.CliTimeoutError) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "Segment", "input": {}, "schema": SCHEMA}
        )

    payload = raised.value.response_payload()
    assert payload["error"] == "cli_timeout"
    assert payload["provider"] == "codex" and payload["model"] == "gpt-5-codex"
    assert payload["usage"] == {"cached_input_tokens": 13}
    assert isinstance(payload["duration_seconds"], float)
    assert "private" not in json.dumps(payload)
    assert bridge_response_for_error(raised.value) == (504, payload)


def test_telemetry_parsers_merge_split_jsonl_and_ignore_unsafe_values():
    transcript = "\n".join(
        [
            json.dumps({"model": "gpt-5.6-codex"}),
            json.dumps(["not", "an", "event"]),
            "{not-json",
            json.dumps(
                {
                    "turn": {
                        "model": "unsafe model name",
                        "usage": {
                            "prompt_tokens": 41,
                            "cached_input_tokens": True,
                            "cache_read_input_tokens": 7,
                            "output_tokens": -1,
                            "completion_tokens": 2,
                            "reasoning_tokens": float("inf"),
                            "reasoning_output_tokens": 3,
                        },
                    }
                }
            ),
        ]
    )

    assert cli_bridge._codex_metadata(transcript) == {
        "model": "gpt-5.6-codex",
        "usage": {
            "input_tokens": 41,
            "cached_input_tokens": 7,
            "output_tokens": 2,
            "reasoning_tokens": 3,
        },
    }
    assert cli_bridge._claude_metadata(json.dumps([])) == {"usage": {}, "model": None}
    assert cli_bridge._captured_text(object()) == ""
    assert cli_bridge._json_envelope(json.dumps([])) == {}
    assert cli_bridge._redacted_cli_diagnostic("", "") is None


def test_codex_reroute_uses_the_target_model_or_refuses_to_guess():
    resolved = "\n".join(
        [
            json.dumps({"type": "model_reroute", "to_model": "gpt-5.6-luna"}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}}),
        ]
    )
    assert cli_bridge._codex_metadata(resolved) == {
        "usage": {"input_tokens": 10},
        "model": "gpt-5.6-luna",
        "model_rerouted": True,
    }

    unresolved = "\n".join(
        [
            json.dumps({"type": "model_reroute", "to_model": "unsafe model"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "model": "gpt-5.6-terra",
                    "usage": {"input_tokens": 10},
                }
            ),
        ]
    )
    assert cli_bridge._codex_metadata(unresolved) == {
        "usage": {"input_tokens": 10},
        "model": None,
        "model_rerouted": True,
    }


def test_claude_metadata_does_not_guess_across_models_or_accept_unsafe_costs():
    metadata = cli_bridge._claude_metadata(
        json.dumps(
            {
                "total_cost_usd": "0.0000000000001",
                "modelUsage": {
                    "claude-sonnet-4-6": {},
                    "claude-haiku-4-5": {},
                },
                "usage": {"input_tokens": 1},
            }
        )
    )
    assert metadata == {"usage": {"input_tokens": 1}, "model": None}


def test_claude_runner_classifies_binary_and_process_start_failures(monkeypatch):
    runner = cli_bridge.CliRunner()
    request = {"provider": "claude", "instructions": "x", "input": {}, "schema": SCHEMA}

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: None)
    with pytest.raises(cli_bridge.CliExecutionError) as missing:
        runner.generate(request)
    assert missing.value.kind == "dependency_unavailable"

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    for raised_error, expected_kind in (
        (FileNotFoundError("removed"), "dependency_unavailable"),
        (OSError("cannot start"), "bridge_unavailable"),
    ):
        monkeypatch.setattr(
            cli_bridge.subprocess,
            "run",
            lambda *args, error=raised_error, **kwargs: (_ for _ in ()).throw(error),
        )
        with pytest.raises(cli_bridge.CliExecutionError) as failed:
            runner.generate(request)
        assert failed.value.kind == expected_kind
        assert failed.value.provider == "claude"
        assert isinstance(failed.value.duration_seconds, float)


@pytest.mark.parametrize(
    ("envelope", "message"),
    [
        ({"model": "claude-sonnet"}, "no structured"),
        ({"structured_output": "[]"}, "invalid structured"),
    ],
)
def test_claude_runner_rejects_missing_or_non_object_output(monkeypatch, envelope, message):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        ),
    )

    with pytest.raises(cli_bridge.CliExecutionError, match=message) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "claude", "instructions": "x", "input": {}, "schema": SCHEMA}
        )
    assert raised.value.kind == "invalid_model_output"


def test_non_object_codex_output_keeps_telemetry_and_maps_to_422(monkeypatch):
    monkeypatch.setattr(
        cli_bridge.CliRunner,
        "_run_codex",
        staticmethod(
            lambda prompt, schema, timeout: (
                "[]",
                "private stderr",
                {"model": "gpt-5.6-codex", "usage": {"input_tokens": 8}},
            )
        ),
    )

    with pytest.raises(cli_bridge.CliStructuredOutputError) as raised:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "x", "input": {}, "schema": SCHEMA}
        )

    payload = raised.value.response_payload()
    assert payload == {
        "error": "invalid_structured_output",
        "provider": "codex",
        "duration_seconds": payload["duration_seconds"],
        "model": "gpt-5.6-codex",
        "usage": {"input_tokens": 8},
    }
    assert bridge_response_for_error(raised.value) == (422, payload)


def test_claude_runner_reports_stdout_when_the_cli_fails(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="nested session rejected",
            stderr="",
        ),
    )
    with pytest.raises(cli_bridge.CliExecutionError, match="nested session rejected"):
        cli_bridge.CliRunner().generate(
            {
                "provider": "claude",
                "instructions": "Segment topics",
                "input": "{}",
                "schema": SCHEMA,
            }
        )


def test_claude_runner_explains_expired_subscription_auth(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 401,
                    "result": "Failed to authenticate. API Error: 401",
                }
            ),
            stderr="",
        ),
    )
    with pytest.raises(cli_bridge.CliExecutionError) as error:
        cli_bridge.CliRunner().generate(
            {
                "provider": "claude",
                "instructions": "Segment topics",
                "input": "{}",
                "schema": SCHEMA,
            }
        )
    assert error.value.kind == "auth_required"
    assert "connected again" in str(error.value)


def test_runner_classifies_rate_limits_dependencies_and_process_start_failures(monkeypatch):
    rate = cli_bridge.classify_cli_failure(
        "claude", '{"api_error_status":429,"result":"rate limit"}', ""
    )
    dependency = cli_bridge.classify_cli_failure("codex", "", "command not found")
    assert rate.kind == "rate_limited" and rate.retry_after_seconds == 1800
    assert dependency.kind == "dependency_unavailable"

    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    with pytest.raises(cli_bridge.CliExecutionError) as error:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "x", "input": {}, "schema": SCHEMA}
        )
    assert error.value.kind == "dependency_unavailable"


def test_runner_classifies_plain_text_expired_login_and_usage_limit():
    expired = cli_bridge.classify_cli_failure(
        "claude", "Claude CLI subscription authentication is expired or invalid.", ""
    )
    limited = cli_bridge.classify_cli_failure("codex", "", "Usage limit reached")
    assert expired.kind == "auth_required"
    assert "connected again" in str(expired)
    assert limited.kind == "rate_limited" and limited.retry_after_seconds == 1800


def test_runner_classifies_invalid_codex_schema_before_any_limit_marker():
    error = cli_bridge.classify_cli_failure(
        "codex",
        "",
        "ERROR: invalid_json_schema: Invalid schema for response_format. Usage limit state unchanged.",
    )
    assert error.kind == "invalid_model_output"
    assert "structured memory format" in str(error)


def test_cli_failure_keeps_redacted_local_diagnostic():
    error = cli_bridge.classify_cli_failure(
        "codex",
        "",
        "Usage limit reached. Authorization: Bearer private-token token=another-secret",
    )
    assert error.kind == "rate_limited"
    assert "Usage limit reached" in (error.diagnostic or "")
    assert "private-token" not in (error.diagnostic or "")
    assert "another-secret" not in (error.diagnostic or "")


def test_setup_saves_a_private_embeddings_key_and_starts_one_official_login(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    root = tmp_path / "Project"
    (root / ".dduo-solo-founder").mkdir(parents=True)
    (root / ".dduo-solo-founder/project.toml").write_text('id = "p1"\n')
    secret_root = config_dir / "project-secrets"
    monkeypatch.setattr(project_secrets, "PROJECT_SECRETS_DIR", secret_root)
    monkeypatch.setattr(cli_bridge, "CONFIG_DIR", config_dir)
    setup = cli_bridge.SetupService()
    setup.save_openai_key("sk-test-embeddings-key", str(root))
    env_path = project_secrets.project_env_file("p1")
    assert env_path.read_text() == "OPENAI_API_KEY=sk-test-embeddings-key\n"
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o777 == 0o600
        assert env_path.parent.stat().st_mode & 0o777 == 0o700

    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider: SimpleNamespace(ready=False, as_dict=lambda: {"ready": False}),
    )
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    calls = []
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or process,
    )
    assert setup.start_auth("claude") == {"status": "waiting", "provider": "claude"}
    assert setup.start_auth("claude") == {"status": "waiting", "provider": "claude"}
    assert calls[0][0] == ["/bin/claude", "auth", "login", "--claudeai"]
    assert calls[0][1]["stdin"] is cli_bridge.subprocess.DEVNULL
    assert "OPENAI_API_KEY" not in calls[0][1]["env"]


def test_setup_reports_project_readiness_and_resumes_only_connected_provider(monkeypatch, tmp_path):
    root = tmp_path / "Project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text(
        'id = "p1"\nname = "Project"\napi_port = 18123\nweb_port = 20123\n'
    )
    monkeypatch.setattr(cli_bridge, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        project_secrets, "PROJECT_SECRETS_DIR", tmp_path / "config" / "project-secrets"
    )
    monkeypatch.setattr(cli_bridge, "load_backup_settings", lambda _: None)
    project_secrets.save_project_secrets("p1", {"OPENAI_API_KEY": "sk-test-key"})
    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider, **kwargs: SimpleNamespace(
            as_dict=lambda: {"ready": provider == "claude", "reason": "authenticated"}
        ),
    )
    monkeypatch.setattr(cli_bridge, "codex_environment", lambda project_id, base: base)
    monkeypatch.setattr(
        cli_bridge,
        "codex_hook_status",
        lambda root: SimpleNamespace(as_dict=lambda: {"ready": False}),
    )
    monkeypatch.setattr(
        cli_bridge,
        "claude_statusline_status",
        lambda root: {
            "ready": True,
            "installed": True,
            "reason": "ready",
            "project_root": str(root),
        },
    )
    monkeypatch.setattr(
        cli_bridge.SetupService,
        "_docker_status",
        staticmethod(lambda: {"installed": True, "running": True, "ready": True}),
    )
    posted = []
    monkeypatch.setattr(
        cli_bridge.httpx,
        "post",
        lambda url, **kwargs: (
            posted.append((url, kwargs))
            or SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"scheduled": 1})
        ),
    )
    monkeypatch.setattr(
        cli_bridge.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200, raise_for_status=lambda: None,
            json=lambda: {"state": "updated", "executor_provider": "claude"},
        ),
    )
    setup = cli_bridge.SetupService()
    status = setup.status(str(root))
    assert status["ready"] is True
    assert status["ready_for_client"] == {"claude": True, "codex": False}
    assert status["claude_telemetry"]["ready"] is True
    assert status["project"]["dashboard_url"].endswith("?project=p1&tab=tasks")
    assert setup.resume_memory(str(root), "claude")["resumed"] is False
    setup._auth["claude:p1"] = cli_bridge.AuthAttempt(
        provider="claude", process=SimpleNamespace(poll=lambda: 0), verified=True,
    )
    assert setup.resume_memory(str(root), "claude") == {"resumed": True, "provider": "claude"}
    assert posted[0][1]["json"] == {
        "trigger": "session_start",
        "provider": "claude",
        "resume_auth": True,
    }
    page = cli_bridge._setup_html()
    assert "/v1/setup/resume" in page and "/v1/setup/backup" in page
    assert "/v1/setup/claude-telemetry" in page
    assert "Claude usage telemetry" in page
    assert "Telemetria di utilizzo Claude" in page
    assert "dDuo checks everything else automatically" in page
    assert "dDuo verifica automaticamente tutto il resto" in page
    assert "The dDuo lifecycle is incomplete" in page
    assert "Il ciclo di vita dDuo è incompleto" in page
    assert "['hooks_missing', 'hooks_incomplete', 'hooks_disabled', 'check_failed']" in page
    assert "token" not in page.casefold()
    assert str(root) not in page
    assert "credentials: 'same-origin'" in page
    assert "history.replaceState" in page


def test_setup_claude_telemetry_is_local_accessible_and_install_or_repair_only():
    page = cli_bridge._setup_html()
    telemetry = page.split("function renderClaudeTelemetry", 1)[1].split(
        "function renderProject", 1
    )[0]

    assert 'id="claude-telemetry" class="system-row"' in page
    assert "Claude usage telemetry is ready." in page
    assert "La telemetria di utilizzo Claude è pronta." in page
    assert "Install Claude usage telemetry" in page
    assert "Repair Claude usage telemetry" in page
    assert "const ready = telemetry.ready === true" in telemetry
    assert "const installed = Boolean(telemetry.installed)" in telemetry
    assert "const canRepair = !ready && telemetryCanRepair(telemetry)" in telemetry
    assert "installed ? t('telemetryRepair') : t('telemetryInstall')" in telemetry
    assert "await call('/v1/setup/claude-telemetry', {})" in telemetry
    assert "renderClaudeTelemetry(state.claude_telemetry)" in page
    assert "'restore_state_missing'" in page
    assert "'project_adapter_missing'" in page
    assert "'user_fallback_missing'" in page
    assert "managed_override: 'telemetryManagedOverride'" in page
    assert "shared_adapter_unmanaged: 'telemetrySharedAdapter'" in page
    assert 'role="status" aria-live="polite"' in page
    assert "min-height: 44px" in page
    assert "@media (max-width: 680px)" in page
    assert "grid-template-columns: 1fr" in page
    assert "window.localStorage.getItem(LANGUAGE_STORAGE_KEY)" in page
    assert "window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)" in page
    assert "document.documentElement.lang = language" in page
    assert "function telemetryCanRepair(status)" in page
    assert "function setupErrorMessage(value)" in page
    assert "'Enter a valid OpenAI embeddings key.': 'errorInvalidOpenAI'" in page
    assert "if (lastSetupState) renderSetupState(lastSetupState)" in page
    assert "if (refreshAfterCurrent) continue" in page


def test_setup_installs_or_repairs_claude_telemetry_for_the_bound_project(
    monkeypatch,
    tmp_path,
):
    calls = []
    root = tmp_path / "Project"
    root.mkdir()
    monkeypatch.setattr(
        cli_bridge,
        "install_claude_statusline",
        lambda **kwargs: calls.append(("install-statusline", kwargs)) or True,
    )
    monkeypatch.setattr(
        cli_bridge,
        "claude_statusline_status",
        lambda project_root: calls.append(("status", project_root))
        or {
            "ready": True,
            "installed": True,
            "reason": "ready",
            "project_root": str(project_root),
        },
    )

    configured = cli_bridge.SetupService.configure_claude_telemetry(root)

    assert configured == {
        "ready": True,
        "installed": True,
        "reason": "ready",
        "project_root": str(root),
    }
    assert calls == [
        ("install-statusline", {"project_root": root.resolve(), "repair": True}),
        ("status", root.resolve()),
    ]
    with pytest.raises(ValueError, match="project root is required"):
        cli_bridge.SetupService.configure_claude_telemetry()


def test_setup_claude_telemetry_status_is_project_scoped_with_a_safe_host_fallback(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        cli_bridge,
        "claude_statusline_status",
        lambda root: {
            "ready": True,
            "installed": True,
            "reason": "ready",
            "project_root": str(root),
        },
    )
    assert cli_bridge.SetupService.claude_telemetry_status(tmp_path) == {
        "ready": True,
        "installed": True,
        "reason": "ready",
        "project_root": str(tmp_path),
    }

    monkeypatch.setattr(cli_bridge, "claude_statusline_installed", lambda: False)
    assert cli_bridge.SetupService.claude_telemetry_status() == {
        "ready": False,
        "installed": False,
        "reason": "project_root_unavailable",
        "detail": "Open Setup from a project to verify Claude telemetry precedence.",
    }


def test_setup_claude_telemetry_http_route_is_session_scoped_and_maps_failures(tmp_path):
    root = tmp_path / "Project"
    root.mkdir()
    access = cli_bridge.SetupAccessStore()
    ticket = access.issue_ticket(root)
    consumed = access.consume_ticket(ticket)
    assert consumed is not None
    session_id, _ = consumed

    class Setup:
        failure: Exception | None = None

        def configure_claude_telemetry(self, project_root):
            if self.failure is not None:
                raise self.failure
            return {
                "ready": True,
                "installed": True,
                "reason": "ready",
                "project_root": str(project_root),
            }

    setup = Setup()
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret", setup=setup, setup_access=access)
    handler.path = "/v1/setup/claude-telemetry"
    sent = []
    handler._send = lambda status, value: sent.append((status, value))

    def post(body, *, cookie=True):
        encoded = json.dumps(body).encode()
        handler.headers = {
            "Content-Length": str(len(encoded)),
            **({"Cookie": f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}"} if cookie else {}),
        }
        handler.rfile = io.BytesIO(encoded)
        handler.do_POST()
        return sent.pop()

    assert post({}) == (
        200,
        {
            "ready": True,
            "installed": True,
            "reason": "ready",
            "project_root": str(root),
        },
    )
    assert post({}, cookie=False) == (401, {"error": "unauthorized"})
    assert post({"project_root": str(tmp_path)}) == (
        403,
        {"error": "setup_scope_mismatch"},
    )

    setup.failure = ValueError("Claude telemetry could not be repaired")
    assert post({}) == (
        422,
        {"error": "invalid_request", "detail": "Claude telemetry could not be repaired"},
    )
    setup.failure = RuntimeError("private state failed")
    assert post({}) == (
        503,
        {"error": "setup_unavailable", "detail": "Setup could not complete. Try again."},
    )


def test_setup_enables_the_default_backup_and_schedules_an_initial_archive(monkeypatch, tmp_path):
    root = tmp_path / "Project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text(
        'id = "p1"\nname = "Project"\napi_port = 18123\nweb_port = 20123\n'
    )
    destination = tmp_path / "Backups"
    monkeypatch.setattr(cli_bridge, "default_backup_directory", lambda: destination)
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/bin/dduo-solo-founder")
    commands = []
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append((command, kwargs))
            or SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "configured": True,
                        "destination": str(destination / "project"),
                        "recovery_key": "recovery-key",
                        "recovery_key_new": True,
                    }
                ),
                stderr="",
            )
        ),
    )
    posts = []
    monkeypatch.setattr(
        cli_bridge.httpx,
        "post",
        lambda url, **kwargs: (
            posts.append((url, kwargs))
            or SimpleNamespace(status_code=202, json=lambda: {"scheduled": True})
        ),
    )
    result = cli_bridge.SetupService.configure_backup(str(root))
    assert result == {
        "configured": True,
        "scheduled": True,
        "destination": str(destination / "project"),
        "recovery_key": "recovery-key",
    }
    assert commands[0][0] == [
        "/bin/dduo-solo-founder",
        "backup",
        "configure",
        str(destination),
        "--project-root",
        str(root),
        "--json",
    ]
    assert posts[0][0].endswith("/projects/p1/backups/automatic")


def test_setup_backup_reports_safe_state_and_handles_recoverable_failures(monkeypatch, tmp_path):
    root = tmp_path / "Project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    project_file = config / "project.toml"
    project_file.write_text('id = "p1"\napi_port = 18123\n')
    destination = tmp_path / "Backups"
    monkeypatch.setattr(cli_bridge, "default_backup_directory", lambda: destination)

    assert cli_bridge.SetupService._backup_status(None)["project_ready"] is False
    monkeypatch.setattr(
        cli_bridge, "load_backup_settings", lambda _: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    assert cli_bridge.SetupService._backup_status(root)["configured"] is False

    project_file.unlink()
    with pytest.raises(ValueError, match="Activate this project"):
        cli_bridge.SetupService.configure_backup(str(root))
    project_file.write_text("not valid toml = [")
    with pytest.raises(ValueError, match="Activate this project"):
        cli_bridge.SetupService.configure_backup(str(root))
    project_file.write_text('id = "p1"\napi_port = 18123\n')

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/bin/dduo-solo-founder")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    with pytest.raises(ValueError, match="could not complete"):
        cli_bridge.SetupService.configure_backup(str(root))

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )
    with pytest.raises(ValueError, match="Check Docker"):
        cli_bridge.SetupService.configure_backup(str(root))

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="{", stderr=""),
    )
    with pytest.raises(ValueError, match="invalid response"):
        cli_bridge.SetupService.configure_backup(str(root))

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='{"configured": false}', stderr=""
        ),
    )
    with pytest.raises(ValueError, match="could not complete"):
        cli_bridge.SetupService.configure_backup(str(root))

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"configured": True, "destination": str(destination)}),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        cli_bridge.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.HTTPError("offline")),
    )
    assert cli_bridge.SetupService.configure_backup(str(root)) == {
        "configured": True,
        "scheduled": False,
        "destination": str(destination),
        "recovery_key": None,
    }


def test_setup_status_exposes_only_safe_native_auth_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_bridge, "USER_ENV", tmp_path / "env")
    monkeypatch.setattr(cli_bridge, "load_backup_settings", lambda _: None)
    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider: SimpleNamespace(
            as_dict=lambda: {"ready": False, "reason": "login_required"}
        ),
    )
    monkeypatch.setattr(
        cli_bridge.SetupService,
        "_docker_status",
        staticmethod(lambda: {"installed": True, "running": True, "ready": True}),
    )
    setup = cli_bridge.SetupService()
    setup._auth["claude"] = cli_bridge.AuthAttempt(
        provider="claude", process=SimpleNamespace(poll=lambda: None)
    )
    status = setup.status(str(tmp_path))
    assert status["clients"]["claude"]["setup_state"] == "waiting"
    assert "token" not in json.dumps(status)


def test_setup_caches_cli_and_hook_checks_between_page_refreshes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli_bridge, "USER_ENV", tmp_path / "env")
    monkeypatch.setattr(cli_bridge, "load_backup_settings", lambda _: None)
    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider: (
            calls.append(("auth", provider))
            or SimpleNamespace(as_dict=lambda: {"ready": False, "reason": "login_required"})
        ),
    )
    monkeypatch.setattr(
        cli_bridge,
        "codex_hook_status",
        lambda root: (
            calls.append(("hooks", str(root))) or SimpleNamespace(as_dict=lambda: {"ready": False})
        ),
    )
    monkeypatch.setattr(
        cli_bridge.SetupService,
        "_docker_status",
        staticmethod(lambda: {"installed": True, "running": True, "ready": True}),
    )
    monkeypatch.setattr(
        cli_bridge.httpx, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200)
    )
    setup = cli_bridge.SetupService()
    setup.status(str(tmp_path))
    setup.status(str(tmp_path))
    assert calls.count(("auth", "claude")) == 1
    assert calls.count(("auth", "codex")) == 1
    assert calls.count(("hooks", str(tmp_path.resolve()))) == 1


def test_runner_reports_missing_cli_and_invalid_contract(monkeypatch):
    runner = cli_bridge.CliRunner()
    with pytest.raises(ValueError, match="provider"):
        runner.generate({"provider": "other", "instructions": "x", "schema": SCHEMA})
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: None)
    with pytest.raises(cli_bridge.CliExecutionError, match="not available"):
        runner.generate(
            {
                "provider": "codex",
                "instructions": "x",
                "input": {},
                "schema": SCHEMA,
            }
        )


def test_http_bridge_requires_authentication_and_returns_structured_output(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda command: f"/usr/bin/{command}")

    class Runner:
        def generate(self, request):
            return {"ok": True, "output": {"topics": []}, "provider": request["provider"]}

    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret", runner=Runner())
    sent = []
    handler._send = lambda status, value: sent.append((status, value))

    handler.path = "/health"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop()[0] == 401

    handler.headers = {"Authorization": "Bearer secret"}
    handler.do_GET()
    health = sent.pop()[1]
    assert health["providers"] == {"codex": True, "claude": True}
    assert health["bridge_protocol_version"] == cli_bridge.BRIDGE_PROTOCOL_VERSION

    body = json.dumps({"provider": "codex"}).encode()
    handler.path = "/v1/generate"
    handler.headers = {
        "Authorization": "Bearer secret",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    assert sent.pop()[1]["output"] == {"topics": []}

    handler.path = "/missing"
    handler.do_GET()
    assert sent.pop()[0] == 404

    stopped = []
    handler.server.shutdown = lambda: stopped.append(True)

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(cli_bridge.threading, "Thread", ImmediateThread)
    handler.path = "/shutdown"
    handler.do_POST()
    assert sent.pop()[0] == 202 and stopped == [True]


def test_backup_supplement_is_project_scoped_bounded_and_authenticated(monkeypatch, tmp_path):
    project_id = "22222222-2222-4222-8222-222222222222"
    root = tmp_path / "project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text(
        f'id = "{project_id}"\nname = "Project"\napi_port = 18001\nweb_port = 20001\n'
    )
    registry = tmp_path / "projects.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    project_id: {
                        "root_path": str(root),
                        "api_port": 18001,
                        "web_port": 20001,
                    }
                },
            }
        )
    )
    host_config = tmp_path / "host"
    secret_root = host_config / "project-secrets"
    monkeypatch.setattr(cli_bridge, "REGISTRY_PATH", registry)
    monkeypatch.setattr(project_config, "REGISTRY_PATH", registry)
    monkeypatch.setattr(cli_bridge, "CONFIG_DIR", host_config)
    monkeypatch.setattr(project_secrets, "PROJECT_SECRETS_DIR", secret_root)
    monkeypatch.setattr(project_secrets, "LEGACY_ENV_FILE", host_config / "env")
    project_secrets.save_project_secrets(
        project_id,
        {
            "OPENAI_API_KEY": "sk-project",
            "DDUO_NODE_AUTHORITY_SECRET": "transfer-authority",
        },
    )
    codex = project_secrets.project_codex_home(project_id)
    codex.mkdir(parents=True)
    (codex / "auth.json").write_text('{"tokens":"private"}')
    hooks = host_config / "hook-state"
    hooks.mkdir(parents=True)
    (hooks / f"{project_id}-codex-session.json").write_text(
        json.dumps({"project_id": project_id, "queued": True})
    )
    (hooks / f"{project_id}-wrong-project.json").write_text(
        json.dumps({"project_id": "another-project", "queued": True})
    )
    unrelated = hooks / "other-project-codex-session.json"
    unrelated.write_text("must-not-leak")
    mcp = host_config / "mcp-observability"
    mcp.mkdir()
    (mcp / f"{project_id}.json").write_text("[]")

    result = cli_bridge.build_backup_supplement(project_id)
    assert result["project_id"] == project_id
    assert result["credentials_complete"] is True
    assert result["codex_auth"] == {"included": True, "credential_store": "file"}
    paths = {item["path"]: item for item in result["files"]}
    assert set(paths) == {
        "secrets/dduo.env",
        "secrets/codex/auth.json",
        "binding/project.toml",
        f"host-state/hooks/{project_id}-codex-session.json",
        "host-state/mcp-observability.json",
    }
    environment = base64.b64decode(paths["secrets/dduo.env"]["content_base64"])
    assert environment == (
        b"DDUO_NODE_AUTHORITY_SECRET=transfer-authority\nOPENAI_API_KEY=sk-project\n"
    )
    assert b"must-not-leak" not in b"".join(
        base64.b64decode(item["content_base64"]) for item in result["files"]
    )

    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret")
    sent = []
    handler._send = lambda status, value: sent.append((status, value))
    handler.path = f"/v1/backup/supplement?project_id={project_id}"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop()[0] == 401
    handler.headers = {"Authorization": "Bearer secret"}
    handler.do_GET()
    assert sent.pop()[1]["project_id"] == project_id

    scoped = project_bridge_token("secret", project_id)
    handler.headers = {"Authorization": f"Bearer {scoped}"}
    handler.do_GET()
    assert sent.pop()[1]["project_id"] == project_id
    handler.path = "/v1/backup/supplement?project_id=another-project"
    handler.do_GET()
    assert sent.pop()[0] == 401

    (config / "project.toml").write_text(
        f'id = "{project_id}"\nname = "Project"\napi_port = 18001\n'
        'web_port = 20001\ndeployment = "remote"\n'
    )
    incomplete_remote = cli_bridge.build_backup_supplement(project_id)
    assert incomplete_remote["credentials_complete"] is False
    assert "remote_runtime_secrets_unavailable" in incomplete_remote["warnings"]

    (codex / "auth.json").unlink()
    monkeypatch.setattr(cli_bridge, "ensure_project_codex_home", lambda _project_id: codex)
    environment_path = project_secrets.project_env_file(project_id)
    environment_path.write_text("DDUO_NODE_AUTHORITY_SECRET=transfer-authority\n")
    incomplete_credentials = cli_bridge.build_backup_supplement(project_id)
    assert incomplete_credentials["codex_auth"] == {
        "included": False,
        "credential_store": "unavailable",
    }
    assert "codex_auth_unavailable" in incomplete_credentials["warnings"]
    assert "openai_key_unavailable" in incomplete_credentials["warnings"]

    monkeypatch.setattr(cli_bridge, "MAX_BACKUP_SUPPLEMENT_FILES", 1)
    with pytest.raises(ValueError, match="too many files"):
        cli_bridge.build_backup_supplement(project_id)
    monkeypatch.setattr(cli_bridge, "MAX_BACKUP_SUPPLEMENT_FILES", 512)
    monkeypatch.setattr(cli_bridge, "MAX_BACKUP_SUPPLEMENT_BYTES", 0)
    with pytest.raises(ValueError, match="safe limit"):
        cli_bridge.build_backup_supplement(project_id)


def test_generate_scoped_token_cannot_select_another_projects_codex_credentials():
    class Runner:
        def generate(self, request):
            return {"project_id": request["project_id"]}

    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="master", runner=Runner())
    sent = []
    handler._send = lambda status, value: sent.append((status, value))
    token = project_bridge_token("master", "project-a")

    def generate(project_id):
        body = json.dumps({"provider": "codex", "project_id": project_id}).encode()
        handler.path = "/v1/generate"
        handler.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        return sent.pop()

    assert generate("project-a") == (200, {"project_id": "project-a"})
    assert generate("project-b")[0] == 401


def test_backup_supplement_rejects_unregistered_and_oversized_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_bridge, "REGISTRY_PATH", tmp_path / "missing.json")
    with pytest.raises(ValueError, match="not registered"):
        cli_bridge.build_backup_supplement("unknown")

    source = tmp_path / "oversized.json"
    source.write_bytes(b"x" * 5)
    monkeypatch.setattr(cli_bridge, "MAX_BACKUP_SUPPLEMENT_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="safe limit"):
        cli_bridge._supplement_file("host-state/hooks/state.json", source)

    target = tmp_path / "private.json"
    target.write_text("{}")
    symlink = tmp_path / "state.json"
    symlink.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        cli_bridge._supplement_file("host-state/hooks/state.json", symlink)


def test_setup_http_routes_keep_secrets_inside_the_local_page(monkeypatch, tmp_path):
    root = tmp_path / "Project"
    root.mkdir()
    calls = []

    class Setup:
        def status(self, project_root):
            calls.append(("status", project_root))
            return {"ready": False}

        def save_openai_key(self, key, project_root=None):
            calls.append(("key", key, project_root))

        def start_auth(self, provider, project_root=None):
            calls.append(("auth", provider, project_root))
            return {"status": "waiting", "provider": provider}

        def start_docker(self):
            calls.append(("docker",))
            return {"status": "starting"}

        def configure_backup(self, project_root):
            calls.append(("backup", project_root))
            return {"configured": True, "scheduled": True, "recovery_key": "private"}

        def resume_memory(self, project_root, provider):
            calls.append(("resume", project_root, provider))
            return {"resumed": True, "provider": provider}

        def configure_claude_telemetry(self, project_root):
            calls.append(("claude-telemetry", project_root))
            return {"ready": True, "installed": True, "reason": "ready"}

    access = cli_bridge.SetupAccessStore()
    ticket = access.issue_ticket(root)
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret", setup=Setup(), setup_access=access)
    sent = []
    html = []
    handler._send = lambda status, value: sent.append((status, value))
    handler._send_html = lambda value, **kwargs: html.append((value, kwargs))

    handler.path = f"/setup?ticket={ticket}"
    handler.headers = {}
    handler.do_GET()
    page, setup_response = html.pop()
    session_id = setup_response["setup_session"]
    cookie = f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}"
    assert "dDuo Solo Founder" in page
    assert "secret" not in page and str(root) not in page

    handler.path = f"/setup?ticket={ticket}"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop()[0] == 401

    handler.path = "/v1/setup/status"
    handler.headers = {"Cookie": cookie}
    handler.do_GET()
    assert sent.pop() == (200, {"ready": False})

    def post(path, body):
        payload = json.dumps(body).encode()
        handler.path = path
        handler.headers = {"Cookie": cookie, "Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        return sent.pop()

    assert post("/v1/setup/openai", {"key": "local-only-key"}) == (200, {"saved": True})
    assert post("/v1/setup/auth", {"provider": "claude"}) == (
        202,
        {"status": "waiting", "provider": "claude"},
    )
    assert post("/v1/setup/docker", {}) == (202, {"status": "starting"})
    assert post("/v1/setup/backup", {}) == (
        200,
        {"configured": True, "scheduled": True, "recovery_key": "private"},
    )
    assert post("/v1/setup/resume", {"provider": "claude"}) == (
        202,
        {"resumed": True, "provider": "claude"},
    )
    assert post("/v1/setup/claude-telemetry", {}) == (
        200,
        {"ready": True, "installed": True, "reason": "ready"},
    )
    assert ("claude-telemetry", root) in calls

    def activate(command, **kwargs):
        config = root / ".dduo-solo-founder"
        config.mkdir()
        (config / "project.toml").write_text(
            'id = "p1"\nname = "Project"\napi_port = 18001\nweb_port = 20001\n'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_bridge.subprocess, "run", activate)
    status, activated = post("/v1/setup/activate", {})
    assert status == 200 and activated["dashboard_url"].endswith("?project=p1&tab=tasks")
    assert ("key", "local-only-key", str(root)) in calls

    handler.path = "/v1/setup/status"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop()[0] == 401

    handler.path = f"/v1/setup/status?root={tmp_path}"
    handler.headers = {"Cookie": cookie}
    handler.do_GET()
    assert sent.pop() == (403, {"error": "setup_scope_mismatch"})


def test_host_agent_opens_setup_only_for_authenticated_api_calls(monkeypatch, tmp_path):
    root = tmp_path / "Project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text('id = "p1"\n')
    access = cli_bridge.SetupAccessStore()
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(
        token="private-token",
        server_address=("127.0.0.1", 45321),
        setup_access=access,
    )
    sent = []
    opened = []
    handler._send = lambda status, value: sent.append((status, value))
    monkeypatch.setattr(
        cli_bridge.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )

    def post(headers, body):
        payload = json.dumps(body).encode()
        handler.path = "/v1/setup/open"
        handler.headers = {**headers, "Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        return sent.pop()

    assert post({}, {"project_root": str(root)}) == (401, {"error": "unauthorized"})
    scoped_token = project_bridge_token("private-token", "p1")
    assert post({"Authorization": f"Bearer {scoped_token}"}, {"project_root": str(root)}) == (
        202,
        {"opened": True},
    )
    assert opened and "/setup?ticket=" in opened[0]
    assert "private-token" not in opened[0] and str(root) not in opened[0]
    monkeypatch.setattr(cli_bridge.webbrowser, "open", lambda _url: False)
    assert post({"Authorization": f"Bearer {scoped_token}"}, {"project_root": str(root)}) == (
        503,
        {"error": "setup_unavailable", "detail": "Setup could not open."},
    )
    assert post(
        {"Authorization": "Bearer private-token"}, {"project_root": str(root / "missing")}
    ) == (422, {"error": "invalid_request", "detail": "Setup could not open."})


def test_setup_tickets_are_one_use_root_bound_and_expire(tmp_path):
    now = [100.0]
    store = cli_bridge.SetupAccessStore(
        ticket_ttl_seconds=5,
        session_ttl_seconds=10,
        clock=lambda: now[0],
    )
    first = store.issue_ticket(tmp_path)
    now[0] = 106.0
    assert store.consume_ticket(first) is None

    second = store.issue_ticket(tmp_path)
    consumed = store.consume_ticket(second)
    assert consumed is not None
    session_id, root = consumed
    assert root == tmp_path.resolve()
    assert store.consume_ticket(second) is None
    assert store.session_root(session_id) == tmp_path.resolve()
    now[0] = 117.0
    assert store.session_root(session_id) is None


def test_setup_ticket_endpoint_requires_master_and_cookie_is_hardened(tmp_path):
    access = cli_bridge.SetupAccessStore()
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="master-secret", setup_access=access)
    sent = []
    handler._send = lambda status, value: sent.append((status, value))

    def request_ticket(authorization):
        body = json.dumps({"project_root": str(tmp_path)}).encode()
        handler.path = "/v1/setup/ticket"
        handler.headers = {
            "Authorization": authorization,
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        return sent.pop()

    scoped = project_bridge_token("master-secret", "p1")
    assert request_ticket(f"Bearer {scoped}")[0] == 401
    status, payload = request_ticket("Bearer master-secret")
    assert status == 201 and payload["expires_in_seconds"] == cli_bridge.SETUP_TICKET_TTL_SECONDS
    assert "master-secret" not in payload["ticket"]

    response_headers = []
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: response_headers.append((name, value))
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler._send_html("<html></html>", setup_session="browser-session")
    cookie = dict(response_headers)["Set-Cookie"]
    assert cookie == (
        f"{cli_bridge.SETUP_SESSION_COOKIE}=browser-session; Path=/; HttpOnly; "
        f"SameSite=Strict; Max-Age={cli_bridge.SETUP_SESSION_TTL_SECONDS}"
    )


def test_bridge_logs_never_include_setup_query_credentials(capsys):
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.address_string = lambda: "127.0.0.1"
    handler.log_message(
        '"%s" %s %s',
        "GET /setup?ticket=one-time-secret&root=/private/path HTTP/1.1",
        "200",
        "123",
    )
    logged = capsys.readouterr().out
    assert "one-time-secret" not in logged and "/private/path" not in logged
    assert "/setup?[REDACTED]" in logged


def test_runner_rejects_missing_schema_and_normalizes_empty_or_invalid_output(monkeypatch):
    runner = cli_bridge.CliRunner()
    with pytest.raises(ValueError, match="JSON schema"):
        runner.generate({"provider": "codex", "instructions": "x", "schema": {}})

    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(cli_bridge.CliExecutionError, match="no structured") as missing:
        runner.generate({"provider": "codex", "instructions": "x", "input": {}, "schema": SCHEMA})
    assert missing.value.kind == "invalid_model_output"

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": "not-json"}),
            stderr="stderr tail",
        ),
    )
    with pytest.raises(cli_bridge.CliExecutionError, match="invalid structured") as invalid:
        runner.generate({"provider": "claude", "instructions": "x", "input": {}, "schema": SCHEMA})
    assert invalid.value.kind == "invalid_model_output"


def test_cli_runner_handles_os_failures_and_claude_result_fallback(monkeypatch):
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "/bin/codex")
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(cli_bridge.CliExecutionError) as failed:
        cli_bridge.CliRunner().generate(
            {"provider": "codex", "instructions": "x", "input": {}, "schema": SCHEMA}
        )
    assert failed.value.kind == "bridge_unavailable"

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": json.dumps({"topics": []})}),
            stderr="",
        ),
    )
    result = cli_bridge.CliRunner().generate(
        {"provider": "claude", "instructions": "x", "input": {}, "schema": SCHEMA}
    )
    assert result["output"] == {"topics": []}


def test_setup_service_handles_prerequisite_and_native_login_failure_paths(monkeypatch, tmp_path):
    setup = cli_bridge.SetupService()
    monkeypatch.setattr(cli_bridge, "USER_ENV", tmp_path / "missing" / "env")
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: None)
    assert setup._docker_status() == {"installed": False, "running": False, "ready": False}
    assert setup._embeddings_ready() is False
    monkeypatch.setattr(
        cli_bridge,
        "load_project_secrets",
        lambda project_id: {"OPENAI_API_KEY": "configured-project-key"}
        if project_id == "p1"
        else {},
    )
    assert setup._embeddings_ready("p1") is True
    monkeypatch.setattr(
        cli_bridge,
        "load_project_secrets",
        lambda _project_id: (_ for _ in ()).throw(ValueError("unsafe secret file")),
    )
    assert setup._embeddings_ready("p1") is False
    assert setup._project_status(None) == {"ready": False}
    assert setup.start_docker()["status"] == "missing"
    with pytest.raises(ValueError, match="valid OpenAI"):
        setup.save_openai_key("short")
    with pytest.raises(ValueError, match="Unknown provider"):
        setup.start_auth("other")

    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider: SimpleNamespace(ready=False, as_dict=lambda: {"ready": False}),
    )
    with pytest.raises(ValueError, match="not installed"):
        setup.start_auth("codex")

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/bin/client")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cannot open")),
    )
    with pytest.raises(ValueError, match="Could not open"):
        setup.start_auth("codex")

    monkeypatch.setattr(cli_bridge.sys, "platform", "linux")
    assert setup.start_docker()["status"] == "action_required"
    assert setup.resume_memory(str(tmp_path), "codex") == {"resumed": False}


def test_bridge_http_errors_are_typed_and_setup_routes_fail_closed(monkeypatch, tmp_path):
    handler = object.__new__(cli_bridge.BridgeHandler)
    sent = []
    access = cli_bridge.SetupAccessStore()
    ticket = access.issue_ticket(tmp_path)
    session_id, _root = access.consume_ticket(ticket)
    handler.server = SimpleNamespace(token="secret", setup_access=access)
    handler._send = lambda status, value: sent.append((status, value))

    def post_generate(runner):
        body = json.dumps({"provider": "codex"}).encode()
        handler.server.runner = runner
        handler.path = "/v1/generate"
        handler.headers = {"Authorization": "Bearer secret", "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        return sent.pop()

    class TimeoutRunner:
        def generate(self, request):
            raise cli_bridge.subprocess.TimeoutExpired("codex", 1)

    class RateRunner:
        def generate(self, request):
            raise cli_bridge.CliExecutionError(
                "rate_limited", "later", retry_after_seconds=12, diagnostic="provider detail"
            )

    class InvalidRunner:
        def generate(self, request):
            raise ValueError("bad request")

    class BrokenRunner:
        def generate(self, request):
            raise RuntimeError("boom")

    assert post_generate(TimeoutRunner())[0] == 504
    rate_status, rate = post_generate(RateRunner())
    assert rate_status == 429 and rate["retry_after_seconds"] == 12
    assert rate["diagnostic"] == "provider detail"
    assert post_generate(InvalidRunner())[0] == 422
    assert post_generate(BrokenRunner())[0] == 503

    class FailingSetup:
        def save_openai_key(self, value, project_root=None):
            raise ValueError("bad key")

    handler.server.setup = FailingSetup()
    body = json.dumps({"key": "bad"}).encode()
    handler.path = "/v1/setup/openai"
    handler.headers = {
        "Cookie": f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    assert sent.pop() == (422, {"error": "invalid_request", "detail": "bad key"})

    handler.path = "/v1/setup/unknown"
    handler.headers = {
        "Cookie": f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    assert sent.pop()[0] == 404

    handler.path = "/v1/setup/openai"
    handler.headers = {}
    handler.do_POST()
    assert sent.pop()[0] == 401

    with pytest.raises(ValueError, match="invalid request size"):
        handler.headers = {"Content-Length": "0"}
        handler._body()
    invalid = json.dumps(["not", "an object"]).encode()
    handler.headers = {"Content-Length": str(len(invalid))}
    handler.rfile = io.BytesIO(invalid)
    with pytest.raises(ValueError, match="object"):
        handler._body()


def test_agent_main_requires_token_and_opens_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_bridge.sys, "argv", ["agent", "--port", "43123"])
    with pytest.raises(SystemExit, match="TOKEN"):
        cli_bridge.main()

    opened = []
    created = []

    class Server:
        def __init__(self, address, handler):
            created.append((address, handler))

        def serve_forever(self):
            created.append("served")

    class ImmediateTimer:
        def __init__(self, delay, callback):
            self.callback = callback

        def start(self):
            self.callback()

    monkeypatch.setattr(cli_bridge, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(cli_bridge.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(cli_bridge.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(
        cli_bridge.sys,
        "argv",
        [
            "agent",
            "--token",
            "secret",
            "--port",
            "43123",
            "--open-setup",
            "--project-root",
            str(tmp_path),
        ],
    )
    cli_bridge.main()
    assert created[0][0] == ("127.0.0.1", 43123)
    assert created[-1] == "served"
    assert opened[0].startswith("http://127.0.0.1:43123/setup?ticket=")
    assert "secret" not in opened[0] and str(tmp_path) not in opened[0]


def test_agent_main_requires_an_explicit_dynamic_port(monkeypatch):
    monkeypatch.delenv("DDUO_CLI_BRIDGE_PORT", raising=False)
    monkeypatch.setattr(cli_bridge.sys, "argv", ["agent", "--token", "secret"])
    with pytest.raises(SystemExit) as error:
        cli_bridge.main()
    assert error.value.code == 2


def test_agent_main_accepts_private_environment_port(monkeypatch):
    created = []

    class Server:
        def __init__(self, address, _handler):
            created.append(address)

        def serve_forever(self):
            return None

    monkeypatch.setenv("DDUO_CLI_BRIDGE_TOKEN", "private-token")
    monkeypatch.setenv("DDUO_CLI_BRIDGE_PORT", "43124")
    monkeypatch.setattr(cli_bridge, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(cli_bridge.sys, "argv", ["agent", "--host", "0.0.0.0"])

    cli_bridge.main()

    assert created == [("0.0.0.0", 43124)]


def test_cli_runner_serializes_consolidation_calls(monkeypatch):
    active = 0
    maximum = 0
    lock = threading.Lock()

    def run_codex(prompt, schema, timeout):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return '{"topics": []}', ""

    monkeypatch.setattr(cli_bridge.CliRunner, "_run_codex", staticmethod(run_codex))
    runner = cli_bridge.CliRunner()
    request = {"provider": "codex", "instructions": "x", "input": {}, "schema": SCHEMA}
    threads = [threading.Thread(target=runner.generate, args=(request,)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum == 1


def test_setup_service_checks_safe_edge_states_and_starts_mac_docker(monkeypatch, tmp_path):
    setup = cli_bridge.SetupService()
    monkeypatch.setattr(cli_bridge, "USER_ENV", tmp_path / "env")
    root = tmp_path / "Project"
    root.mkdir()
    config = root / ".dduo-solo-founder"
    config.mkdir()
    (config / "project.toml").write_text("not = [valid")
    assert setup._project_status(root) == {"ready": False}
    assert setup._auth_attempt_state("claude") == "idle"
    setup._auth["claude"] = cli_bridge.AuthAttempt(provider="claude", error="closed")
    assert setup._auth_attempt_state("claude") == "failed"
    setup._auth["codex"] = cli_bridge.AuthAttempt(
        provider="codex", process=SimpleNamespace(poll=lambda: 0)
    )
    assert setup._auth_attempt_state("codex") == "checking"

    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda provider: SimpleNamespace(ready=True, as_dict=lambda: {"ready": True}),
    )
    assert setup.start_auth("codex") == {"status": "connected", "provider": "codex"}
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/usr/local/bin/docker")
    opened = []
    monkeypatch.setattr(cli_bridge.sys, "platform", "darwin")
    monkeypatch.setattr(
        cli_bridge.subprocess, "Popen", lambda command, **kwargs: opened.append(command)
    )
    assert setup.start_docker() == {"status": "starting"}
    assert opened == [["open", "-a", "Docker"]]
    monkeypatch.setattr(
        cli_bridge.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(cli_bridge.httpx.HTTPError("offline")),
    )
    (config / "project.toml").write_text('id = "p"\napi_port = 1234\n')
    assert setup.resume_memory(str(root), "codex") == {"resumed": False, "provider": "codex"}


def test_bridge_handler_rejects_oversized_and_invalid_setup_requests():
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="secret", setup_access=cli_bridge.SetupAccessStore())
    sent = []
    handler._send = lambda status, value: sent.append((status, value))
    handler.path = "/setup?ticket=wrong"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop()[0] == 401
    handler.headers = {"Content-Length": str(cli_bridge.MAX_BODY_BYTES + 1)}
    with pytest.raises(ValueError, match="invalid request size"):
        handler._body()


def test_setup_access_and_service_cover_project_scoped_failure_edges(monkeypatch, tmp_path):
    store = cli_bridge.SetupAccessStore()
    with pytest.raises(ValueError, match="unavailable"):
        store.issue_ticket(tmp_path / "missing")
    assert store.consume_ticket("  ") is None

    setup = cli_bridge.SetupService()
    assert setup._codex_hook_status(None) is None

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _name: "/usr/local/bin/docker")
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("docker unavailable")),
    )
    assert setup._docker_status() == {"installed": True, "running": False, "ready": False}

    saved = []
    monkeypatch.setattr(cli_bridge.SetupService, "_project_id", staticmethod(lambda _root: "p1"))
    monkeypatch.setattr(
        cli_bridge,
        "save_project_secrets",
        lambda project_id, values: saved.append((project_id, values)),
    )
    setup.save_openai_key("project-secret", str(tmp_path))
    assert saved == [("p1", {"OPENAI_API_KEY": "project-secret"})]


def test_registered_project_and_supplement_fail_closed_edges(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="required"):
        cli_bridge._registered_project_root("")
    with pytest.raises(ValueError, match="required"):
        cli_bridge._registered_project_root("x" * 129)

    root = tmp_path / "project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text('id = "actual"\n')
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {"expected": {"root_path": str(root)}},
            }
        )
    )
    monkeypatch.setattr(cli_bridge, "REGISTRY_PATH", registry)
    with pytest.raises(ValueError, match="identity does not match"):
        cli_bridge._registered_project_root("expected")

    missing = tmp_path / "vanished"
    with pytest.raises(ValueError, match="unavailable"):
        cli_bridge._supplement_file("host-state/missing.json", missing)


def test_bridge_get_and_post_failure_matrix_is_typed_and_fail_closed(monkeypatch, tmp_path):
    root = tmp_path / "project"
    config = root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    (config / "project.toml").write_text('id = "p1"\nweb_port = 20001\n')
    access = cli_bridge.SetupAccessStore()
    ticket = access.issue_ticket(root)
    session_id, _ = access.consume_ticket(ticket)
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(
        token="master",
        setup_access=access,
        setup=SimpleNamespace(status=lambda _root: {"ready": False}),
    )
    sent = []
    html = []
    handler._send = lambda status, value: sent.append((status, value))
    handler._send_html = lambda value, **kwargs: html.append((value, kwargs))

    handler.client_address = ("not-an-ip", 1)
    handler.path = "/health"
    handler.headers = {"Authorization": "Bearer master"}
    handler.do_GET()
    assert sent.pop() == (403, {"error": "untrusted_network"})
    handler.client_address = ("127.0.0.1", 1)

    handler.path = "/setup"
    handler.headers = {}
    handler.do_GET()
    assert sent.pop() == (401, {"error": "unauthorized"})

    handler.path = "/v1/backup/supplement?project_id=p1"
    handler.headers = {"Authorization": "Bearer master"}
    monkeypatch.setattr(
        cli_bridge,
        "build_backup_supplement",
        lambda _project_id: (_ for _ in ()).throw(ValueError("bad project")),
    )
    handler.do_GET()
    assert sent.pop() == (422, {"error": "invalid_request", "detail": "bad project"})
    monkeypatch.setattr(
        cli_bridge,
        "build_backup_supplement",
        lambda _project_id: (_ for _ in ()).throw(RuntimeError("private detail")),
    )
    handler.do_GET()
    assert sent.pop()[0] == 503

    invalid_ticket_body = json.dumps({"project_root": str(tmp_path / "missing")}).encode()
    handler.path = "/v1/setup/ticket"
    handler.headers = {
        "Authorization": "Bearer master",
        "Content-Length": str(len(invalid_ticket_body)),
    }
    handler.rfile = io.BytesIO(invalid_ticket_body)
    handler.do_POST()
    assert sent.pop() == (422, {"error": "invalid_request", "detail": "Setup is unavailable."})

    handler.client_address = ("8.8.8.8", 1)
    handler.path = "/v1/generate"
    handler.headers = {}
    handler.do_POST()
    assert sent.pop() == (403, {"error": "untrusted_network"})
    handler.client_address = ("127.0.0.1", 1)

    handler.path = "/shutdown"
    handler.headers = {}
    handler.do_POST()
    assert sent.pop() == (401, {"error": "unauthorized"})
    handler.path = "/unknown"
    handler.do_POST()
    assert sent.pop() == (404, {"error": "not_found"})

    payload = json.dumps({"project_root": str(tmp_path)}).encode()
    handler.path = "/v1/setup/openai"
    handler.headers = {
        "Cookie": f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}",
        "Content-Length": str(len(payload)),
    }
    handler.rfile = io.BytesIO(payload)
    handler.do_POST()
    assert sent.pop() == (403, {"error": "setup_scope_mismatch"})


def test_setup_activation_reports_disappearing_root_command_failure_and_internal_error(
    monkeypatch, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    access = cli_bridge.SetupAccessStore()
    ticket = access.issue_ticket(root)
    session_id, _ = access.consume_ticket(ticket)
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(token="master", setup_access=access, setup=SimpleNamespace())
    sent = []
    handler._send = lambda status, value: sent.append((status, value))

    def activate() -> tuple[int, dict]:
        payload = json.dumps({}).encode()
        handler.path = "/v1/setup/activate"
        handler.headers = {
            "Cookie": f"{cli_bridge.SETUP_SESSION_COOKIE}={session_id}",
            "Content-Length": str(len(payload)),
        }
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        return sent.pop()

    root.rmdir()
    assert activate()[0] == 422
    root.mkdir()
    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=4),
    )
    assert activate()[0] == 422

    monkeypatch.setattr(
        cli_bridge.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private detail")),
    )
    assert activate() == (
        503,
        {"error": "setup_unavailable", "detail": "Setup could not complete. Try again."},
    )


def test_bridge_response_writers_emit_complete_no_store_responses():
    handler = object.__new__(cli_bridge.BridgeHandler)
    status = []
    headers = []
    handler.send_response = status.append
    handler.send_header = lambda name, value: headers.append((name, value))
    handler.end_headers = lambda: headers.append(("ended", "yes"))
    handler.wfile = io.BytesIO()

    handler._send(200, {"message": "caffè"})
    assert status == [200]
    assert ("Cache-Control", "no-store") in headers
    assert json.loads(handler.wfile.getvalue()) == {"message": "caffè"}

    status.clear()
    headers.clear()
    handler.wfile = io.BytesIO()
    handler._send_html("<p>ok</p>")
    assert status == [200]
    assert not any(name == "Set-Cookie" for name, _value in headers)
    assert handler.wfile.getvalue() == b"<p>ok</p>"


def test_setup_cookie_and_scope_parsers_fail_closed(monkeypatch, tmp_path):
    handler = object.__new__(cli_bridge.BridgeHandler)
    handler.server = SimpleNamespace(
        setup_access=SimpleNamespace(session_root=lambda _value: tmp_path)
    )
    handler.headers = {"Cookie": "anything"}

    class BrokenCookie:
        def load(self, _value):
            raise cli_bridge.CookieError("invalid cookie")

    monkeypatch.setattr(cli_bridge, "SimpleCookie", BrokenCookie)
    assert handler._setup_session_root() is None

    class BrokenPath:
        def expanduser(self):
            return self

        def resolve(self):
            raise OSError("unresolvable")

    monkeypatch.setattr(cli_bridge, "Path", lambda _value: BrokenPath())
    assert cli_bridge.BridgeHandler._setup_scope_matches(tmp_path, "broken") is False
