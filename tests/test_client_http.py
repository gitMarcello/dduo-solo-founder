from __future__ import annotations

from types import SimpleNamespace

import pytest

from dduo_solo_founder.client_binding import ProjectBinding
from dduo_solo_founder.client_http import ProjectHttpClient, compatibility_directive


def binding(tmp_path, *, kind: str = "local", token: str | None = None) -> ProjectBinding:
    return ProjectBinding(
        project_id="project",
        name="Project",
        root_path=tmp_path,
        kind=kind,
        api_url="https://memory.example.test/api" if kind == "remote" else "http://127.0.0.1:1",
        dashboard_url=None,
        binding_id="binding",
        bearer_token=token,
    )


def test_compatibility_directive_reads_json_envelope_and_rejects_unsafe_release():
    response = SimpleNamespace(
        headers={},
        status_code=200,
        json=lambda: {
            "client_update": {
                "status": "grace",
                "target_release": "0.1.0-alpha.49",
            }
        },
    )
    directive = compatibility_directive(response)
    assert directive is not None
    assert directive.status == "grace"
    assert directive.target_release == "0.1.0-alpha.49"

    response.json = lambda: {
        "client_update": {"status": "blocked", "target_release": "../unsafe"}
    }
    directive = compatibility_directive(response)
    assert directive is not None and directive.target_release is None


def test_project_http_headers_fail_closed_without_remote_token_and_merge_safe_extras(tmp_path):
    with pytest.raises(RuntimeError, match="credential is unavailable"):
        ProjectHttpClient(binding(tmp_path, kind="remote"), component="test").headers()

    headers = ProjectHttpClient(binding(tmp_path), component="test").headers(
        {"X-Request-ID": "request-1"}
    )
    assert headers["X-DDUO-Binding-ID"] == "binding"
    assert headers["X-Request-ID"] == "request-1"
    assert "Authorization" not in headers
