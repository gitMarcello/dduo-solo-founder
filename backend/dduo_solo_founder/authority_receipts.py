"""Small HMAC receipts for a two-phase authority handoff.

The Beta threat model is accidental split brain between hosts controlled by
one trusted infrastructure manager. The shared authority secret is present on
the restored destination by design, so these receipts bind the handoff state
and detect operator/copy errors; they are not Byzantine proof against a
malicious destination that deliberately forges with that shared secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass


RECEIPT_VERSION = 1
RECEIPT_PREFIX = "dduo_authority_v1"
RECEIPT_KINDS = frozenset({"destination_ready", "source_finalized"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SAFE_NONCE = re.compile(r"^[0-9a-f]{64}$")


class AuthorityReceiptError(ValueError):
    """The supplied authority handoff proof is malformed or unauthenticated."""


@dataclass(frozen=True, slots=True)
class AuthorityReceipt:
    kind: str
    project_id: str
    source_node_id: str
    target_node_id: str
    source_generation: int
    nonce: str

    def payload(self) -> dict[str, str | int]:
        return {
            "version": RECEIPT_VERSION,
            "kind": self.kind,
            "project_id": self.project_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "source_generation": self.source_generation,
            "nonce": self.nonce,
        }


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decoded(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise AuthorityReceiptError("authority receipt encoding is invalid")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise AuthorityReceiptError("authority receipt encoding is invalid") from exc


def _validate(receipt: AuthorityReceipt) -> None:
    if receipt.kind not in RECEIPT_KINDS:
        raise AuthorityReceiptError("authority receipt kind is invalid")
    if not _SAFE_ID.fullmatch(receipt.project_id):
        raise AuthorityReceiptError("authority receipt project is invalid")
    if not _SAFE_ID.fullmatch(receipt.source_node_id):
        raise AuthorityReceiptError("authority receipt source node is invalid")
    if not _SAFE_ID.fullmatch(receipt.target_node_id):
        raise AuthorityReceiptError("authority receipt target node is invalid")
    if receipt.source_generation < 1:
        raise AuthorityReceiptError("authority receipt generation is invalid")
    if not _SAFE_NONCE.fullmatch(receipt.nonce):
        raise AuthorityReceiptError("authority receipt nonce is invalid")


def issue_authority_receipt(secret: str, receipt: AuthorityReceipt) -> str:
    """Return a compact proof that contains no authority secret."""
    if not secret:
        raise AuthorityReceiptError("node authority secret is unavailable")
    _validate(receipt)
    payload = json.dumps(
        receipt.payload(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded_payload = _encoded(payload)
    signing_input = f"{RECEIPT_PREFIX}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{RECEIPT_PREFIX}.{encoded_payload}.{_encoded(signature)}"


def verify_authority_receipt(
    token: str,
    secret: str,
    *,
    expected_kind: str,
) -> AuthorityReceipt:
    """Authenticate and strictly parse one handoff proof."""
    if not secret or len(token) > 2_048:
        raise AuthorityReceiptError("authority receipt is invalid")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != RECEIPT_PREFIX:
        raise AuthorityReceiptError("authority receipt is invalid")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    supplied_signature = _decoded(parts[2])
    expected_signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise AuthorityReceiptError("authority receipt signature is invalid")
    try:
        payload = json.loads(_decoded(parts[1]))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityReceiptError("authority receipt payload is invalid") from exc
    expected_keys = {
        "version",
        "kind",
        "project_id",
        "source_node_id",
        "target_node_id",
        "source_generation",
        "nonce",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise AuthorityReceiptError("authority receipt payload is invalid")
    if payload.get("version") != RECEIPT_VERSION:
        raise AuthorityReceiptError("authority receipt version is unsupported")
    try:
        receipt = AuthorityReceipt(
            kind=str(payload["kind"]),
            project_id=str(payload["project_id"]),
            source_node_id=str(payload["source_node_id"]),
            target_node_id=str(payload["target_node_id"]),
            source_generation=int(payload["source_generation"]),
            nonce=str(payload["nonce"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityReceiptError("authority receipt payload is invalid") from exc
    if isinstance(payload.get("source_generation"), bool):
        raise AuthorityReceiptError("authority receipt generation is invalid")
    _validate(receipt)
    if receipt.kind != expected_kind:
        raise AuthorityReceiptError("authority receipt phase is invalid")
    return receipt
