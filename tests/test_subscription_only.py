import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dduo_solo_founder import cli_bridge, client_readiness
from dduo_solo_founder.client_installation import ClientCommand, ClientInstallation


@pytest.fixture(autouse=True)
def isolated_subscription_installation(monkeypatch):
    def installation(provider):
        return ClientInstallation(
            family=provider,
            surface_hint="cli",
            config_dir=Path("/synthetic/profile") / provider,
            management_command=ClientCommand(Path("/synthetic/bin") / provider),
        )

    monkeypatch.setattr(cli_bridge.CliRunner, "_installation", staticmethod(installation))


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

    def status(provider, *, environment, installation):
        assert (
            "CODEX_API_KEY" not in environment and "OPENAI_IDENTITY_TOKEN_FILE" not in environment
        )
        assert environment["CODEX_HOME"] == str(installation.config_dir)
        return SimpleNamespace(ready=True)

    monkeypatch.setattr(cli_bridge, "subscription_auth_status", status)
    cli_bridge.CliRunner._require_unscoped_subscription("codex")


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_legacy_execution_reuses_the_authenticated_installation_and_profile(monkeypatch, provider):
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-unused-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unused-key")
    authenticated = {}

    def status(client, *, environment, installation):
        assert client == provider
        assert "CODEX_API_KEY" not in environment and "ANTHROPIC_API_KEY" not in environment
        authenticated.update(installation=installation, environment=environment)
        return SimpleNamespace(ready=True)

    def run(client, prompt, schema, timeout, project_id, *, installation, environment):
        assert client == provider and not project_id
        assert installation is authenticated["installation"]
        assert environment is authenticated["environment"]
        return json.dumps({"synthetic": True}), "", {}

    monkeypatch.setattr(cli_bridge, "subscription_auth_status", status)
    monkeypatch.setattr(cli_bridge.CliRunner, "_run_provider", staticmethod(run))
    result = cli_bridge.CliRunner().generate(
        {
            "provider": provider,
            "instructions": "Synthetic",
            "input": {},
            "schema": {"type": "object", "properties": {}},
        }
    )
    assert result["output"] == {"synthetic": True}
