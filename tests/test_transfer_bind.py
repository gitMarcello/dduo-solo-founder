"""Bind a workstation through verified TLS after a first authority transfer."""

from __future__ import annotations

from dataclasses import replace
import json
import ssl

import httpx
from typer.testing import CliRunner

from conftest import assert_private_file
from test_transfer_https import (  # noqa: F401 -- shared real HTTPS application fixture
    AUTHORITY,
    PROOF,
    SECRET,
    _trust_test_ca,
    tls_destination,
)
from dduo_solo_founder import client_binding, launcher, manual_cache
from dduo_solo_founder.authority_receipts import issue_authority_receipt
from dduo_solo_founder.client_http import ProjectHttpClient


def test_remote_bind_after_first_transfer_uses_real_tls_auth_and_private_credentials(
    tls_destination, tmp_path, monkeypatch,  # noqa: F811 -- pytest fixture injection
):
    checkout = tmp_path / "workstation-checkout"
    (checkout / ".git").mkdir(parents=True)
    private = tmp_path / "workstation-private"
    approvals = private / "remote-bindings.json"
    credentials = private / "remote-credentials"
    monkeypatch.setattr(client_binding, "CONFIG_DIR", private)
    monkeypatch.setattr(client_binding, "REMOTE_APPROVALS_PATH", approvals)
    monkeypatch.setattr(client_binding, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(manual_cache, "MANUAL_CACHE_DIR", private / "manual-cache")
    manager_token = "dduo_dev_" + "b" * 43
    runner = CliRunner()

    with tls_destination() as (api_url, certificate):
        _trust_test_ca(monkeypatch, certificate)
        real_request = httpx.request
        trusted = ssl.create_default_context(cafile=str(certificate))
        assert trusted.verify_mode == ssl.CERT_REQUIRED and trusted.check_hostname
        observed = []

        def request(method, url, **kwargs):
            # Only trust the isolated CA. Still use a real TCP/TLS connection,
            # hostname validation, application router, auth and SQLite rows.
            assert kwargs.get("verify", True) is not False
            response = real_request(
                method, url,
                **{**kwargs, "verify": trusted, "trust_env": False, "follow_redirects": False},
            )
            observed.append((method, url, response.status_code))
            return response

        monkeypatch.setattr(httpx, "request", request)
        path = f"{api_url}/projects/{PROOF.project_id}"
        arguments = [
            "remote-bind", "--project-id", PROOF.project_id,
            "--name", "Artificial shared project", "--api-url", api_url,
            "--dashboard-url", api_url.removesuffix("/api"),
            "--project-root", str(checkout),
        ]
        config_path = checkout / ".dduo-solo-founder" / "project.toml"
        # The future infrastructure token is not a member credential yet.
        early = runner.invoke(launcher.app, arguments, input=manager_token + "\n")
        assert early.exit_code != 0
        assert isinstance(early.exception, httpx.HTTPStatusError)
        assert early.exception.response.status_code == 401
        assert not config_path.exists()
        assert not list(credentials.glob("*.token"))
        if approvals.exists():
            assert json.loads(approvals.read_text())["bindings"] == {}

        ready_receipt = issue_authority_receipt(SECRET, PROOF)
        assert launcher._verify_destination_https(
            api_url, PROOF.project_id, AUTHORITY, ready_receipt, SECRET,
        ) == api_url
        bootstrap_payload = {
            "display_name": "Artificial manager", "device_id": "artificial-host",
            "device_token": manager_token,
        }
        authority_headers = {"X-DDUO-Authority": SECRET}
        still_frozen = request(
            "POST", path + "/team/bootstrap", headers=authority_headers, json=bootstrap_payload,
        )
        assert still_frozen.status_code == 409

        # The source-side finalization is covered by the two-database/PG tests.
        # Supply its synthetic signed result here to test the remaining TLS →
        # destination activation → first manager → workstation binding chain.
        finalized = issue_authority_receipt(SECRET, replace(PROOF, kind="source_finalized"))
        completed = request(
            "POST", path + "/authority/complete", headers=authority_headers,
            json={"node_id": PROOF.target_node_id,
                  "expected_generation": PROOF.source_generation,
                  "finalization_receipt": finalized},
        )
        assert completed.status_code == 200
        assert completed.json()["writable"] is True
        assert completed.json()["generation"] == PROOF.source_generation + 1
        bootstrap = request(
            "POST", path + "/team/bootstrap", headers=authority_headers, json=bootstrap_payload,
        )
        assert bootstrap.status_code == 200
        manager = bootstrap.json()["current_member"]
        assert manager["capability"] == "infrastructure_manager"

        bound = runner.invoke(launcher.app, arguments, input=manager_token + "\n")
        assert bound.exit_code == 0, f"{bound.output}\n{bound.exception}"
        assert '"bound": true' in bound.output
        assert manager_token not in bound.output and SECRET not in bound.output
        binding = client_binding.load_binding(checkout)
        assert binding.remote and binding.project_id == PROOF.project_id
        assert binding.bearer_token == manager_token
        assert binding.api_url == api_url
        assert binding.credential_path.parent == credentials
        assert_private_file(binding.credential_path)
        assert_private_file(approvals)
        assert binding.credential_path.read_text().strip() == manager_token
        config_text = config_path.read_text()
        assert 'binding = "remote"' in config_text
        assert manager_token not in config_text and SECRET not in config_text
        assert "OPENAI_API_KEY" not in config_text
        approved = json.loads(approvals.read_text())["bindings"][binding.binding_id]
        assert "provisional_id" not in approved
        assert approved["project_id"] == PROOF.project_id

        # Exercise the actual shared client again from the persisted binding,
        # proving no CLI-only or in-memory authentication shortcut was used.
        team = ProjectHttpClient(binding, component="launcher").json(
            "GET", f"/projects/{PROOF.project_id}/team",
        )
        assert team["current_member"]["id"] == manager["id"]
        assert team["current_member"]["access_token_id"] == manager["access_token_id"]
        assert ("GET", path + "/team", 401) in observed
        assert observed.count(("GET", path + "/team", 200)) >= 2
