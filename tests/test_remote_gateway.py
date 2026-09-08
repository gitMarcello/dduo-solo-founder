from __future__ import annotations

import json

import pytest

from dduo_solo_founder import remote_gateway


def test_remote_gateway_registers_isolated_ports_and_renders_short_lived_ip_tls(tmp_path):
    registry = tmp_path / "projects.json"
    first = remote_gateway.register_gateway_project(
        "project-one", "One", 20003, "8.8.8.8", email="owner@example.test", registry_path=registry
    )
    second = remote_gateway.register_gateway_project(
        "project-two", "Two", 20021, "8.8.8.8", registry_path=registry
    )
    assert first == {
        "project_id": "project-one",
        "https_port": 443,
        "api_url": "https://8.8.8.8:443/api",
        "dashboard_url": "https://8.8.8.8:443",
    }
    assert second["https_port"] != first["https_port"]
    value = remote_gateway.load_gateway_registry(registry)
    rendered = remote_gateway.render_caddyfile(value)
    assert "profile shortlived" in rendered
    assert "reverse_proxy 127.0.0.1:20003" in rendered
    assert "reverse_proxy 127.0.0.1:20021" in rendered
    assert "owner@example.test" in rendered
    assert registry.stat().st_mode & 0o777 == 0o600

    caddyfile = remote_gateway.write_caddyfile(registry, tmp_path / "Caddyfile")
    assert caddyfile.read_text() == rendered
    assert caddyfile.stat().st_mode & 0o777 == 0o600


def test_remote_gateway_updates_one_project_without_reallocating_and_can_remove(tmp_path):
    registry = tmp_path / "projects.json"
    initial = remote_gateway.register_gateway_project(
        "project", "Initial", 20003, "2001:4860:4860::8888", registry_path=registry
    )
    updated = remote_gateway.register_gateway_project(
        "project", "Updated", 20099, "2001:4860:4860::8888", registry_path=registry
    )
    assert initial["https_port"] == updated["https_port"] == 443
    assert updated["api_url"] == "https://[2001:4860:4860::8888]:443/api"
    assert remote_gateway.unregister_gateway_project("project", registry) is True
    assert remote_gateway.unregister_gateway_project("project", registry) is False


def test_remote_gateway_keeps_port_443_for_acme_when_only_additional_projects_remain(
    tmp_path,
):
    registry = tmp_path / "projects.json"
    remote_gateway.register_gateway_project(
        "first", "First", 20003, "8.8.8.8", registry_path=registry
    )
    remaining = remote_gateway.register_gateway_project(
        "remaining", "Remaining", 20021, "8.8.8.8", registry_path=registry
    )
    assert remaining["https_port"] != remote_gateway.FIRST_HTTPS_PORT

    assert remote_gateway.unregister_gateway_project("first", registry) is True
    rendered = remote_gateway.render_caddyfile(
        remote_gateway.load_gateway_registry(registry)
    )

    assert "https://8.8.8.8:443 {" in rendered
    assert "\trespond 404" in rendered
    assert rendered.count("profile shortlived") == 2
    assert "reverse_proxy 127.0.0.1:20003" not in rendered
    assert "reverse_proxy 127.0.0.1:20021" in rendered


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.1", "example.com", "", "[broken"])
def test_remote_gateway_rejects_non_public_ip(host):
    with pytest.raises(remote_gateway.RemoteGatewayError, match="public IP"):
        remote_gateway.public_ip(host)


def test_remote_gateway_rejects_cross_ip_and_invalid_registry(tmp_path):
    registry = tmp_path / "projects.json"
    remote_gateway.register_gateway_project(
        "project", "Project", 20003, "8.8.8.8", registry_path=registry
    )
    with pytest.raises(remote_gateway.RemoteGatewayError, match="another public IP"):
        remote_gateway.register_gateway_project(
            "other", "Other", 20021, "1.1.1.1", registry_path=registry
        )
    registry.write_text("not-json")
    with pytest.raises(remote_gateway.RemoteGatewayError, match="unreadable"):
        remote_gateway.load_gateway_registry(registry)
    registry.write_text(json.dumps({"version": 99, "projects": {}}))
    with pytest.raises(remote_gateway.RemoteGatewayError, match="unsupported"):
        remote_gateway.load_gateway_registry(registry)


@pytest.mark.parametrize(
    ("project_id", "email"),
    [
        ("../another-project", None),
        ("project", "owner@example.test\nadmin off"),
        ("project", "not-an-email"),
    ],
)
def test_remote_gateway_rejects_configuration_injection(tmp_path, project_id, email):
    with pytest.raises(remote_gateway.RemoteGatewayError):
        remote_gateway.register_gateway_project(
            project_id,
            "Project",
            20003,
            "8.8.8.8",
            email=email,
            registry_path=tmp_path / "projects.json",
        )


def test_remote_gateway_requires_projects_before_rendering(tmp_path):
    registry = tmp_path / "projects.json"
    with pytest.raises(remote_gateway.RemoteGatewayError, match="no registered projects"):
        remote_gateway.write_caddyfile(registry, tmp_path / "Caddyfile")

    malformed = remote_gateway._empty_registry()
    malformed["public_ip"] = "127.0.0.1"
    malformed["projects"]["bad"] = {"https_port": 443, "web_port": 20003}
    with pytest.raises(remote_gateway.RemoteGatewayError, match="public IP"):
        remote_gateway.render_caddyfile(malformed)


@pytest.mark.parametrize(
    "projects",
    [
        [],
        {"../bad": {"https_port": 443, "web_port": 20003}},
        {"project": "not-an-object"},
        {"project": {"https_port": 443}},
        {"project": {"https_port": "invalid", "web_port": 20003}},
        {"project": {"https_port": 0, "web_port": 20003}},
        {
            "one": {"https_port": 443, "web_port": 20003},
            "two": {"https_port": 443, "web_port": 20021},
        },
        {
            "one": {"https_port": 443, "web_port": 20003},
            "two": {"https_port": 24443, "web_port": 20003},
        },
    ],
)
def test_remote_gateway_rejects_malformed_project_registries(projects):
    with pytest.raises(remote_gateway.RemoteGatewayError):
        remote_gateway._validate_projects(projects)


def test_remote_gateway_rejects_duplicate_loopback_port(tmp_path):
    registry = tmp_path / "projects.json"
    remote_gateway.register_gateway_project(
        "one", "One", 20003, "8.8.8.8", registry_path=registry
    )
    with pytest.raises(remote_gateway.RemoteGatewayError, match="loopback dashboard port"):
        remote_gateway.register_gateway_project(
            "two", "Two", 20003, "8.8.8.8", registry_path=registry
        )


def test_remote_gateway_reports_exhausted_https_port_pool():
    projects = {
        "first": {"https_port": remote_gateway.FIRST_HTTPS_PORT, "web_port": 10000}
    }
    projects.update(
        {
            f"project-{offset}": {
                "https_port": remote_gateway.ADDITIONAL_HTTPS_PORT_BASE + offset,
                "web_port": 11000 + offset,
            }
            for offset in range(remote_gateway.ADDITIONAL_HTTPS_PORTS)
        }
    )
    with pytest.raises(remote_gateway.RemoteGatewayError, match="no free HTTPS"):
        remote_gateway._allocated_port("new-project", projects)
