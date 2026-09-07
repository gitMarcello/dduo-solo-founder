from __future__ import annotations

import base64
import json
import re

import pytest

from dduo_solo_founder.invitations import (
    INVITE_PAYLOAD,
    REMOTE_RELEASE_TAG,
    InvitationPayloadError,
    create_invitation_bundle,
    decode_invite_payload,
    encode_invite_payload,
    invitation_setup_prompt,
    invitation_urls,
)


INVITATION_CODE = "dduo_inv_" + "a" * 48


def raw_descriptor(document: object) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(document, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def invite_document(**overrides: object) -> dict:
    document = {
        "version": 1,
        "project_id": "project-one",
        "name": "Shared project",
        "api_url": "https://memory.example/api",
        "dashboard_url": "https://memory.example",
        "invitation_code": INVITATION_CODE,
    }
    document.update(overrides)
    return document


def test_bundle_is_canonical_opaque_and_self_contained():
    payload, prompt = create_invitation_bundle(
        project_id="project-one",
        name='TeamApp $(touch /tmp/nope) `whoami` "quoted"\nnext',
        api_url="https://MEMORY.example:443/api/",
        dashboard_url="https://memory.example:443/",
        invitation_code=INVITATION_CODE,
    )

    assert INVITE_PAYLOAD.fullmatch(payload)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload)
    assert decode_invite_payload(payload) == {
        "project_id": "project-one",
        "name": 'TeamApp $(touch /tmp/nope) `whoami` "quoted"\nnext',
        "api_url": "https://memory.example/api",
        "dashboard_url": "https://memory.example",
        "invitation_code": INVITATION_CODE,
    }
    assert prompt == invitation_setup_prompt(payload)
    assert f"--invite-payload {payload}" in prompt
    assert "--project-id" not in prompt
    assert "--api-url" not in prompt
    assert "--dashboard-url" not in prompt
    assert "--invitation-code" not in prompt
    assert INVITATION_CODE not in prompt
    assert "$(touch" not in prompt
    assert "`whoami`" not in prompt
    assert "dashboard --tab tasks --project-root ." in prompt
    assert REMOTE_RELEASE_TAG in prompt
    assert "chiudere e riaprire completamente Codex" in prompt
    assert "Con Claude è sufficiente una nuova sessione" in prompt
    assert "checkout temporaneo esterno al progetto" in prompt
    assert "Dalla root del progetto" in prompt
    assert prompt.index("ripristinare la connessione remota") < prompt.index("alternativa esplicita")
    assert "Attendi la scelta dell'utente" in prompt
    assert "non ripetere lo stesso avviso" in prompt
    assert "senza contattarlo" in prompt
    assert "non aprire Setup locale" in prompt


def test_prompt_uses_the_requested_language_and_server_release():
    payload = encode_invite_payload(
        project_id="project-one",
        name="Shared project",
        api_url="https://memory.example/api",
        dashboard_url="https://memory.example",
        invitation_code=INVITATION_CODE,
    )

    prompt = invitation_setup_prompt(
        payload,
        language="en",
        release_version="9.8.7-beta.6",
    )

    assert "You received access to the shared memory" in prompt
    assert "release v9.8.7-beta.6" in prompt
    assert "fully quit and reopen Codex" in prompt
    assert "With Claude, a new session is sufficient" in prompt
    assert "temporary checkout outside the project" in prompt
    assert prompt.index("restoring the remote connection") < prompt.index("explicit alternative")
    assert "Wait for the user's choice unless already given" in prompt
    assert "do not repeat an unchanged warning" in prompt
    assert "without contacting them unless" in prompt
    assert "open local Setup" in prompt


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"language": "fr"}, "language is unsupported"),
        ({"release_version": "main"}, "release version is malformed"),
    ],
)
def test_prompt_rejects_unsupported_language_or_unsafe_release(kwargs, message):
    payload = encode_invite_payload(
        project_id="project-one",
        name="Shared project",
        api_url="https://memory.example/api",
        dashboard_url="https://memory.example",
        invitation_code=INVITATION_CODE,
    )

    with pytest.raises(InvitationPayloadError, match=message):
        invitation_setup_prompt(payload, **kwargs)


@pytest.mark.parametrize(
    ("api_url", "dashboard_url", "message"),
    [
        ("http://memory.example/api", "https://memory.example", "must use HTTPS"),
        ("https:///api", "https://memory.example", "must use HTTPS"),
        (
            "https://user:password@memory.example/api",
            "https://memory.example",
            "must not contain credentials",
        ),
        (
            "https://memory.example:bad/api",
            "https://memory.example",
            "not a valid HTTPS URL",
        ),
        (
            "https://memory.example/api?token=x",
            "https://memory.example",
            "query or fragment",
        ),
        (
            "https://memory.example/api",
            "https://memory.example/#team",
            "query or fragment",
        ),
        (
            "https://other.example/api",
            "https://memory.example",
            "same HTTPS origin",
        ),
        (
            "https://memory.example/v1",
            "https://memory.example",
            "followed by /api",
        ),
        (
            'https://memory.example/"/api',
            "https://memory.example",
            "unsafe characters",
        ),
        (
            "https://memory.example/\n/api",
            "https://memory.example",
            "unsafe characters",
        ),
    ],
)
def test_invitation_urls_reject_unsafe_or_mismatched_endpoints(
    api_url: str, dashboard_url: str, message: str
):
    with pytest.raises(InvitationPayloadError, match=message):
        invitation_urls(api_url, dashboard_url)


def test_invitation_urls_support_same_origin_dashboard_subpath_and_ipv6():
    assert invitation_urls(
        "https://[2001:db8::10]:24443/project/api/",
        "https://[2001:DB8::10]:24443/project/",
    ) == (
        "https://[2001:db8::10]:24443/project/api",
        "https://[2001:db8::10]:24443/project",
    )


def test_encoder_rejects_empty_oversized_and_aggregate_oversized_fields():
    values = {
        "project_id": "project-one",
        "name": "Shared",
        "api_url": "https://memory.example/api",
        "dashboard_url": "https://memory.example",
        "invitation_code": INVITATION_CODE,
    }
    with pytest.raises(InvitationPayloadError, match="must not be empty"):
        encode_invite_payload(**{**values, "name": " "})
    with pytest.raises(InvitationPayloadError, match="too large"):
        encode_invite_payload(**{**values, "name": "x" * 2_001})
    with pytest.raises(InvitationPayloadError, match="too large"):
        long_dashboard = f"https://{'a' * 1_900}.example"
        encode_invite_payload(
            **{
                **values,
                "project_id": "p" * 2_000,
                "name": "n" * 2_000,
                "invitation_code": "i" * 2_000,
                "api_url": f"{long_dashboard}/api",
                "dashboard_url": long_dashboard,
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        "not+base64url",
        "A",
        base64.urlsafe_b64encode(b"not json").decode("ascii").rstrip("="),
        raw_descriptor([]),
        raw_descriptor({"version": 1}),
        raw_descriptor(invite_document(extra="field")),
        raw_descriptor(invite_document(version=2)),
        raw_descriptor(invite_document(name="")),
        raw_descriptor(invite_document(name=7)),
        raw_descriptor(invite_document(name="x" * 2_001)),
        raw_descriptor(invite_document(api_url="http://memory.example/api")),
        "A" * 8_193,
    ],
)
def test_decoder_rejects_malformed_descriptors(payload: str):
    with pytest.raises(InvitationPayloadError, match="invitation payload"):
        decode_invite_payload(payload)


def test_decoder_accepts_and_canonicalizes_a_legacy_default_port_descriptor():
    payload = raw_descriptor(
        invite_document(
            api_url="https://MEMORY.example:443/api/",
            dashboard_url="https://memory.example:443/",
        )
    )

    decoded = decode_invite_payload(payload)

    assert decoded["api_url"] == "https://memory.example/api"
    assert decoded["dashboard_url"] == "https://memory.example"


def test_prompt_rejects_any_non_descriptor_text():
    with pytest.raises(InvitationPayloadError, match="malformed"):
        invitation_setup_prompt("descriptor with spaces")
