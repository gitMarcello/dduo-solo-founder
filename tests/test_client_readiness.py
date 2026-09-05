from __future__ import annotations

import io
import json
import queue
import subprocess
import time
from types import SimpleNamespace

import pytest

from dduo_solo_founder import client_readiness


def test_claude_subscription_auth_is_checked_without_parent_credentials(monkeypatch):
    monkeypatch.setattr(client_readiness.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "session-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(client_readiness.subprocess, "run", run)
    status = client_readiness.subscription_auth_status("claude")

    assert status.ready and status.reason == "authenticated"
    assert captured["command"][-3:] == ["auth", "status", "--json"]
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured["env"]


@pytest.mark.parametrize(
    ("payload", "returncode"),
    [
        ({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}, 0),
        ({"loggedIn": True, "authMethod": "apiKey", "apiProvider": "firstParty"}, 0),
        ({}, 1),
    ],
)
def test_claude_rejects_missing_or_api_key_auth(monkeypatch, payload, returncode):
    monkeypatch.setattr(client_readiness.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        client_readiness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )
    status = client_readiness.subscription_auth_status("claude")
    assert not status.ready and status.reason == "login_required"
    assert status.login_command == "claude auth login --claudeai"


def test_codex_accepts_chatgpt_and_rejects_api_key(monkeypatch):
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: "/bin/codex",
    )
    outputs = iter(["Logged in using ChatGPT", "Logged in using an API key"])
    monkeypatch.setattr(
        client_readiness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=next(outputs),
            stderr="",
        ),
    )
    assert client_readiness.subscription_auth_status("codex").ready
    rejected = client_readiness.subscription_auth_status("codex")
    assert not rejected.ready and rejected.login_command == "codex login"


def test_subscription_auth_reports_missing_timeout_failure_and_invalid_client(monkeypatch):
    monkeypatch.setattr(client_readiness.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: None,
    )
    assert client_readiness.subscription_auth_status("codex").reason == "cli_missing"
    with pytest.raises(ValueError, match="codex or claude"):
        client_readiness.subscription_auth_status("other")

    monkeypatch.setattr(client_readiness.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        client_readiness.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 1)),
    )
    assert client_readiness.subscription_auth_status("claude").reason == "check_timeout"
    monkeypatch.setattr(
        client_readiness.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("failed")),
    )
    assert client_readiness.subscription_auth_status("claude").reason == "check_failed"


def test_codex_version_parser_and_minimum_compatibility(monkeypatch):
    assert client_readiness.parse_codex_version("codex-cli 0.145.0") == (0, 145, 0)
    assert client_readiness.parse_codex_version("codex-cli 0.150.0-alpha.8") == (0, 150, 0)
    assert client_readiness.parse_codex_version("unknown") is None

    monkeypatch.setattr(
        client_readiness,
        "_codex_candidates",
        lambda: [("/path/codex", False)],
    )
    monkeypatch.setattr(
        client_readiness,
        "_codex_version",
        lambda executable, *, timeout: (0, 149, 0),
    )
    assert client_readiness.resolve_codex_executable() is None
    with pytest.raises(client_readiness.CodexVersionError, match="0.150.0 or newer"):
        client_readiness.resolve_codex_executable(required=True)

    monkeypatch.setattr(
        client_readiness,
        "_codex_version",
        lambda executable, *, timeout: (0, 150, 0),
    )
    assert client_readiness.resolve_codex_executable(required=True) == "/path/codex"


@pytest.mark.parametrize(
    ("path_version", "desktop_version", "expected"),
    [
        ((0, 149, 9), (0, 150, 0), "/desktop/codex"),
        ((0, 151, 0), (0, 150, 0), "/path/codex"),
        ((0, 150, 0), (0, 150, 0), "/desktop/codex"),
    ],
)
def test_codex_resolver_selects_best_compatible_binary_and_desktop_on_tie(
    monkeypatch, path_version, desktop_version, expected
):
    monkeypatch.setattr(
        client_readiness,
        "_codex_candidates",
        lambda: [("/path/codex", False), ("/desktop/codex", True)],
    )
    versions = {
        "/path/codex": path_version,
        "/desktop/codex": desktop_version,
    }
    monkeypatch.setattr(
        client_readiness,
        "_codex_version",
        lambda executable, *, timeout: versions[executable],
    )
    assert client_readiness.resolve_codex_executable(required=True) == expected


class FakeInput(io.StringIO):
    def close(self):
        self.was_closed = True


class FakeProcess:
    def __init__(self, stdout: str):
        self.stdin = FakeInput()
        self.stdout = io.StringIO(stdout)
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_codex_hook_protocol_reads_official_trust_state(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    output = "\n".join(
        [
            json.dumps({"id": 1, "result": {}}),
            json.dumps(
                {
                    "id": 2,
                    "result": {
                        "data": [
                            {
                                "cwd": str(root),
                                "hooks": [
                                    {
                                        "pluginId": "dduo-solo-founder@personal",
                                        "trustStatus": "trusted",
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
        ]
    )
    process = FakeProcess(output)
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: "/bin/codex",
    )
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: process)

    hooks = client_readiness._list_codex_hooks(root)

    assert hooks[0]["trustStatus"] == "trusted"
    sent = process.stdin.getvalue()
    assert '"method":"initialize"' in sent
    assert '"method":"hooks/list"' in sent
    assert process.terminated


@pytest.mark.parametrize(
    ("statuses", "ready", "reason"),
    [
        (["trusted"] * 3, True, "authorized"),
        (["managed", "trusted", "managed"], True, "authorized"),
        (["untrusted"] * 3, False, "authorization_required"),
        (["trusted", "modified", "trusted"], False, "reauthorization_required"),
    ],
)
def test_codex_hook_status_classifies_trust(monkeypatch, tmp_path, statuses, ready, reason):
    events = ["sessionStart", "userPromptSubmit", "stop"]
    monkeypatch.setattr(
        client_readiness,
        "_list_codex_hooks",
        lambda *args, **kwargs: [
            {
                "pluginId": "dduo-solo-founder@personal",
                "trustStatus": status,
                "eventName": event,
                "enabled": True,
            }
            for event, status in zip(events, statuses, strict=True)
        ],
    )
    result = client_readiness.codex_hook_status(tmp_path)
    assert result.ready is ready
    assert result.reason == reason
    assert result.hook_count == len(statuses)
    assert result.events == sorted(events)


def test_codex_hook_status_handles_missing_and_failed_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(client_readiness, "_list_codex_hooks", lambda *args, **kwargs: [])
    assert client_readiness.codex_hook_status(tmp_path).reason == "hooks_missing"
    monkeypatch.setattr(
        client_readiness,
        "_list_codex_hooks",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )
    assert client_readiness.codex_hook_status(tmp_path).reason == "check_failed"


def test_codex_hook_status_ignores_a_similarly_named_foreign_source_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client_readiness,
        "_list_codex_hooks",
        lambda *args, **kwargs: [
            {
                "pluginId": "another-plugin@personal",
                "sourcePath": "/tmp/dduo-solo-founder/hooks/hooks.json",
                "eventName": event,
                "enabled": True,
                "trustStatus": "trusted",
            }
            for event in ["sessionStart", "userPromptSubmit", "stop"]
        ],
    )

    status = client_readiness.codex_hook_status(tmp_path)

    assert status.ready is False
    assert status.reason == "hooks_missing"


@pytest.mark.parametrize(
    "events",
    [
        ["sessionStart", "userPromptSubmit"],
        ["sessionStart", "userPromptSubmit", "userPromptSubmit"],
        ["sessionStart", "userPromptSubmit", "unknown"],
        ["sessionStart", "userPromptSubmit", "stop", "stop"],
    ],
)
def test_codex_hook_status_rejects_partial_duplicate_or_unknown_lifecycle(monkeypatch, tmp_path, events):
    monkeypatch.setattr(
        client_readiness,
        "_list_codex_hooks",
        lambda *args, **kwargs: [
            {
                "pluginId": "dduo-solo-founder@personal",
                "trustStatus": "trusted",
                "eventName": event,
                "enabled": True,
            }
            for event in events
        ],
    )

    status = client_readiness.codex_hook_status(tmp_path)

    assert status.ready is False
    assert status.reason == "hooks_incomplete"
    assert status.hook_count == len(events)


def test_codex_hook_status_rejects_a_disabled_lifecycle_hook(monkeypatch, tmp_path):
    events = ["sessionStart", "userPromptSubmit", "stop"]
    monkeypatch.setattr(
        client_readiness,
        "_list_codex_hooks",
        lambda *args, **kwargs: [
            {
                "pluginId": "dduo-solo-founder@personal",
                "trustStatus": "trusted",
                "eventName": event,
                "enabled": event != "userPromptSubmit",
            }
            for event in events
        ],
    )

    status = client_readiness.codex_hook_status(tmp_path)

    assert status.ready is False
    assert status.reason == "hooks_disabled"
    assert status.hook_count == 3
    assert status.events == sorted(events)


def test_wait_for_response_handles_errors_closed_stream_and_timeout(monkeypatch):
    messages: queue.Queue[dict] = queue.Queue()
    messages.put({"id": 1, "error": {"message": "bad"}})
    with pytest.raises(RuntimeError, match="rejected"):
        client_readiness._wait_for_response(messages, 1, time.monotonic() + 1)
    messages.put({"_stream_closed": True})
    with pytest.raises(RuntimeError, match="closed"):
        client_readiness._wait_for_response(messages, 1, time.monotonic() + 1)
    monkeypatch.setattr(client_readiness.time, "monotonic", lambda: 10)
    with pytest.raises(TimeoutError):
        client_readiness._wait_for_response(queue.Queue(), 1, 9)

    class EmptyQueue:
        def get(self, *, timeout):
            raise queue.Empty

    with pytest.raises(TimeoutError, match="timed out"):
        client_readiness._wait_for_response(EmptyQueue(), 1, 11)


def test_client_readiness_returns_protected_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client_readiness,
        "subscription_auth_status",
        lambda client: client_readiness.SubscriptionAuthStatus(
            client, False, "login_required", "codex login"
        ),
    )
    monkeypatch.setattr(
        client_readiness,
        "codex_hook_status",
        lambda root: client_readiness.CodexHookStatus(
            False,
            "authorization_required",
            3,
            ["untrusted"],
            events=["sessionStart", "stop", "userPromptSubmit"],
        ),
    )
    result = client_readiness.client_readiness("codex", tmp_path)
    assert not result["ready"]
    assert [action["type"] for action in result["actions"]] == [
        "subscription_login",
        "codex_hook_review",
    ]
    hook_review = result["actions"][-1]
    assert hook_review["surface"] == "native_codex_hook_review"
    assert hook_review["path"] == ["Settings", "Hooks"]
    assert hook_review["review_action"] == "Review"
    assert hook_review["trust_action"] == "Trust all"
    assert "/hooks" not in str(hook_review)
    assert "chat command" in hook_review["detail"]


def test_client_readiness_requires_repair_when_codex_lifecycle_is_not_discovered(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        client_readiness,
        "subscription_auth_status",
        lambda client: client_readiness.SubscriptionAuthStatus(
            client, True, "authenticated", "codex login"
        ),
    )
    monkeypatch.setattr(
        client_readiness,
        "codex_hook_status",
        lambda root: client_readiness.CodexHookStatus(
            False, "hooks_incomplete", 3, ["trusted"]
        ),
    )

    result = client_readiness.client_readiness("codex", tmp_path)

    assert result["ready"] is False
    assert [action["type"] for action in result["actions"]] == ["codex_plugin_repair"]
    assert "restart Codex" in result["actions"][0]["detail"]


def test_client_readiness_covers_invalid_json_stream_and_ready_states(monkeypatch, tmp_path):
    monkeypatch.setattr(client_readiness.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        client_readiness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )
    assert client_readiness.subscription_auth_status("claude").reason == "login_required"

    messages: queue.Queue[dict] = queue.Queue()
    client_readiness._stream_json(io.StringIO("not-json\n[]\n{\"id\": 3}\n"), messages)
    assert messages.get_nowait() == {"id": 3}
    assert messages.get_nowait() == {"_stream_closed": True}

    monkeypatch.setattr(
        client_readiness,
        "subscription_auth_status",
        lambda client: client_readiness.SubscriptionAuthStatus(client, True, "authenticated", f"{client} login"),
    )
    monkeypatch.setattr(
        client_readiness,
        "codex_hook_status",
        lambda root: client_readiness.CodexHookStatus(
            True,
            "authorized",
            3,
            ["trusted"],
            events=["sessionStart", "stop", "userPromptSubmit"],
        ),
    )
    assert client_readiness.client_readiness("codex", tmp_path)["actions"] == []
    assert client_readiness.client_readiness("claude", tmp_path)["ready"] is True


def test_codex_hook_discovery_handles_stdio_missing_empty_result_and_forced_kill(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: "/bin/codex",
    )

    broken = FakeProcess("")
    broken.stdin = None
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: broken)
    with pytest.raises(RuntimeError, match="stdio"):
        client_readiness._list_codex_hooks(root)
    assert broken.terminated

    no_matching_root = FakeProcess(
        "\n".join(
            [
                json.dumps({"id": 1, "result": {}}),
                json.dumps({"id": 2, "result": {"data": [{"cwd": "/other", "hooks": []}]}}),
            ]
        )
    )
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: no_matching_root)
    assert client_readiness._list_codex_hooks(root) == []

    class KillProcess(FakeProcess):
        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("codex", timeout)
            return super().wait(timeout)

    kill = KillProcess(
        "\n".join(
            [
                json.dumps({"id": 1, "result": {}}),
                json.dumps({"id": 2, "result": {"data": []}}),
            ]
        )
    )
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: kill)
    assert client_readiness._list_codex_hooks(root) == []
    assert kill.killed is True


def test_codex_hook_discovery_handles_missing_cli_and_stdin_close_error(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError("not found")),
    )
    with pytest.raises(FileNotFoundError, match="not found"):
        client_readiness._list_codex_hooks(root)

    class ClosingErrorInput:
        def __init__(self):
            self.buffer = io.StringIO()

        def write(self, value):
            return self.buffer.write(value)

        def flush(self):
            return None

        def close(self):
            raise OSError("already closed")

    process = FakeProcess(
        "\n".join(
            [
                json.dumps({"id": 1, "result": {}}),
                json.dumps(
                    {
                        "id": 2,
                        "result": {
                            "data": [
                                {
                                    "cwd": str(root),
                                    "hooks": [
                                        {
                                            "sourcePath": "/plugins/dduo-solo-founder/hooks/hooks.json",
                                            "trustStatus": "managed",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ),
            ]
        )
    )
    process.stdin = ClosingErrorInput()
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: "/bin/codex",
    )
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: process)

    hooks = client_readiness._list_codex_hooks(root)

    assert hooks[0]["trustStatus"] == "managed"
    assert process.terminated is True


def test_codex_candidate_discovery_deduplicates_the_desktop_binary(
    monkeypatch, tmp_path
):
    executable = tmp_path / "Codex Desktop" / "codex"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    monkeypatch.setattr(client_readiness.shutil, "which", lambda _: str(executable))
    monkeypatch.setenv(client_readiness.CODEX_DESKTOP_EXECUTABLE_ENV, str(executable))

    assert client_readiness._codex_candidates() == [(str(executable.resolve()), True)]

    monkeypatch.delenv(client_readiness.CODEX_DESKTOP_EXECUTABLE_ENV)
    monkeypatch.setattr(client_readiness.sys, "platform", "linux")
    monkeypatch.setattr(client_readiness.shutil, "which", lambda _: None)
    assert client_readiness._codex_candidates() == []


def test_codex_version_probe_handles_process_failures_and_stderr(monkeypatch):
    outcomes = iter(
        (
            subprocess.TimeoutExpired("codex", 1),
            SimpleNamespace(returncode=1, stdout="", stderr="failed"),
            SimpleNamespace(returncode=0, stdout="", stderr="codex-cli 0.151.2"),
        )
    )

    def run(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(client_readiness.subprocess, "run", run)
    assert client_readiness._codex_version("/bin/codex", timeout=1) is None
    assert client_readiness._codex_version("/bin/codex", timeout=1) is None
    assert client_readiness._codex_version("/bin/codex", timeout=1) == (0, 151, 2)


def test_required_codex_resolution_distinguishes_missing_from_incompatible(monkeypatch):
    monkeypatch.setattr(client_readiness, "_codex_candidates", lambda: [])
    with pytest.raises(FileNotFoundError, match="was not found"):
        client_readiness.resolve_codex_executable(required=True)

    monkeypatch.setattr(
        client_readiness,
        "_codex_candidates",
        lambda: [("/old/codex", False), ("/broken/codex", True)],
    )
    monkeypatch.setattr(
        client_readiness,
        "_codex_version",
        lambda executable, *, timeout: (0, 149, 0) if "old" in executable else None,
    )
    with pytest.raises(client_readiness.CodexVersionError) as error:
        client_readiness.resolve_codex_executable(required=True)
    assert "0.149.0 at /old/codex" in str(error.value)
    assert "unknown at /broken/codex" in str(error.value)


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("missing"),
        client_readiness.CodexVersionError("too old"),
    ],
)
def test_codex_subscription_auth_preserves_resolver_diagnostics(monkeypatch, failure):
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: (_ for _ in ()).throw(failure),
    )

    status = client_readiness.subscription_auth_status("codex")

    assert status.reason == "cli_missing"
    assert status.detail == str(failure)
    assert status.login_command == "codex login"


def test_app_server_ignores_unrelated_messages_and_rejects_invalid_result(
    monkeypatch, tmp_path
):
    messages: queue.Queue[dict] = queue.Queue()
    messages.put({"method": "server/notice", "params": {}})
    messages.put({"id": 7, "result": {"ok": True}})
    assert client_readiness._wait_for_response(
        messages, 7, time.monotonic() + 1
    ) == {"id": 7, "result": {"ok": True}}

    process = FakeProcess(
        "\n".join(
            [
                json.dumps({"id": 1, "result": {}}),
                json.dumps({"id": 2, "result": []}),
            ]
        )
    )
    monkeypatch.setattr(
        client_readiness,
        "resolve_codex_executable",
        lambda **kwargs: "/bin/codex",
    )
    monkeypatch.setattr(client_readiness.subprocess, "Popen", lambda *args, **kwargs: process)
    with pytest.raises(RuntimeError, match="invalid response"):
        client_readiness._codex_app_server_request(
            "account/rateLimits/read", project_root=tmp_path
        )


def test_readiness_uses_diagnostic_action_without_exposing_a_command(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client_readiness,
        "subscription_auth_status",
        lambda client: client_readiness.SubscriptionAuthStatus(
            client, False, "check_failed", "claude auth login --claudeai", "failed"
        ),
    )

    result = client_readiness.client_readiness("claude", tmp_path)

    assert result["ready"] is False
    assert result["hooks"] is None
    assert result["actions"] == [
        {
            "type": "diagnose_cli",
            "requires_user_approval": True,
            "command": None,
        }
    ]
