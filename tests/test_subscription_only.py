from types import SimpleNamespace

import pytest

from dduo_solo_founder import cli_bridge, client_readiness


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_legacy_generation_requires_subscription_even_without_project_identity(
    monkeypatch, provider
):
    monkeypatch.setattr(
        cli_bridge,
        "subscription_auth_status",
        lambda *_a, **_k: SimpleNamespace(ready=False, reason="login_required"),
    )
    monkeypatch.setattr(
        cli_bridge.CliRunner,
        "_run_provider",
        lambda *_a, **_k: pytest.fail("No generation allowed"),
    )
    with pytest.raises(cli_bridge.CliExecutionError) as error:
        cli_bridge.CliRunner().generate(
            {
                "provider": provider,
                "instructions": "Synthetic",
                "input": {},
                "schema": {"type": "object", "properties": {}},
            }
        )
    assert error.value.kind == "auth_required"


@pytest.mark.parametrize(
    "name",
    [
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
        "AWS_ACCESS_KEY_ID",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ],
)
def test_detached_subscription_cannot_inherit_paid_authentication(monkeypatch, name):
    monkeypatch.setenv(name, "synthetic-unused-value")
    assert name not in client_readiness.detached_cli_environment()


def test_legacy_subscription_check_receives_the_same_clean_environment(monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-unused-key")
    monkeypatch.setenv("OPENAI_IDENTITY_TOKEN_FILE", "/synthetic/identity")

    def status(provider, *, environment):
        assert (
            "CODEX_API_KEY" not in environment and "OPENAI_IDENTITY_TOKEN_FILE" not in environment
        )
        return SimpleNamespace(ready=True)

    monkeypatch.setattr(cli_bridge, "subscription_auth_status", status)
    cli_bridge.CliRunner._require_unscoped_subscription("codex")
