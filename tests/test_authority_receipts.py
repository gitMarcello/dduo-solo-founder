from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from dduo_solo_founder.authority_receipts import (
    AuthorityReceipt,
    AuthorityReceiptError,
    RECEIPT_PREFIX,
    issue_authority_receipt,
    verify_authority_receipt,
)


def _segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_payload(payload: object, *, secret: str = "node-secret") -> str:
    encoded = _segment(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{RECEIPT_PREFIX}.{encoded}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{RECEIPT_PREFIX}.{encoded}.{_segment(signature)}"


def receipt(kind: str = "destination_ready") -> AuthorityReceipt:
    return AuthorityReceipt(
        kind=kind,
        project_id="project-1",
        source_node_id="node-old",
        target_node_id="node-new",
        source_generation=7,
        nonce="a" * 64,
    )


def test_authority_receipt_round_trip_and_phase_binding():
    token = issue_authority_receipt("node-secret", receipt())
    assert verify_authority_receipt(
        token, "node-secret", expected_kind="destination_ready"
    ) == receipt()
    with pytest.raises(AuthorityReceiptError, match="phase"):
        verify_authority_receipt(token, "node-secret", expected_kind="source_finalized")


def test_authority_receipt_rejects_tampering_and_wrong_secret():
    token = issue_authority_receipt("node-secret", receipt())
    prefix, payload, signature = token.split(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(AuthorityReceiptError, match="signature"):
        verify_authority_receipt(
            f"{prefix}.{payload}.{changed}",
            "node-secret",
            expected_kind="destination_ready",
        )
    with pytest.raises(AuthorityReceiptError, match="signature"):
        verify_authority_receipt(token, "another-secret", expected_kind="destination_ready")


def test_authority_receipt_rejects_unsafe_claims():
    with pytest.raises(AuthorityReceiptError, match="nonce"):
        issue_authority_receipt(
            "node-secret",
            AuthorityReceipt(
                kind="destination_ready",
                project_id="project-1",
                source_node_id="node-old",
                target_node_id="node-new",
                source_generation=7,
                nonce="short",
            ),
        )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"kind": "unknown"}, "kind"),
        ({"project_id": "unsafe/project"}, "project"),
        ({"source_node_id": "unsafe source"}, "source node"),
        ({"target_node_id": "unsafe target"}, "target node"),
        ({"source_generation": 0}, "generation"),
    ],
)
def test_authority_receipt_rejects_each_invalid_claim(replacement, message):
    values = {
        "kind": "destination_ready",
        "project_id": "project-1",
        "source_node_id": "node-old",
        "target_node_id": "node-new",
        "source_generation": 7,
        "nonce": "a" * 64,
        **replacement,
    }
    with pytest.raises(AuthorityReceiptError, match=message):
        issue_authority_receipt("node-secret", AuthorityReceipt(**values))


def test_authority_receipt_requires_signing_secret():
    with pytest.raises(AuthorityReceiptError, match="secret"):
        issue_authority_receipt("", receipt())


@pytest.mark.parametrize(
    "token",
    [
        "",
        "wrong.payload.signature",
        f"{RECEIPT_PREFIX}.only-two-parts",
        "x" * 2_049,
    ],
)
def test_authority_receipt_rejects_invalid_envelope(token):
    with pytest.raises(AuthorityReceiptError, match="invalid"):
        verify_authority_receipt(token, "node-secret", expected_kind="destination_ready")


def test_authority_receipt_rejects_empty_secret_and_invalid_base64():
    token = issue_authority_receipt("node-secret", receipt())
    with pytest.raises(AuthorityReceiptError, match="invalid"):
        verify_authority_receipt(token, "", expected_kind="destination_ready")
    with pytest.raises(AuthorityReceiptError, match="encoding"):
        verify_authority_receipt(
            f"{RECEIPT_PREFIX}.payload.A",
            "node-secret",
            expected_kind="destination_ready",
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (["not", "an", "object"], "payload"),
        ({"version": 1}, "payload"),
        ({**receipt().payload(), "version": 2}, "version"),
        ({**receipt().payload(), "source_generation": {}}, "payload"),
        ({**receipt().payload(), "source_generation": True}, "generation"),
    ],
)
def test_authority_receipt_rejects_signed_malformed_payloads(payload, message):
    with pytest.raises(AuthorityReceiptError, match=message):
        verify_authority_receipt(
            _signed_payload(payload),
            "node-secret",
            expected_kind="destination_ready",
        )


def test_authority_receipt_rejects_signed_non_json_payload():
    encoded = _segment(b"{")
    signing_input = f"{RECEIPT_PREFIX}.{encoded}".encode("ascii")
    signature = hmac.new(b"node-secret", signing_input, hashlib.sha256).digest()
    token = f"{RECEIPT_PREFIX}.{encoded}.{_segment(signature)}"
    with pytest.raises(AuthorityReceiptError, match="payload"):
        verify_authority_receipt(token, "node-secret", expected_kind="destination_ready")
