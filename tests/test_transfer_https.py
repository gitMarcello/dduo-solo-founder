"""Real TLS + application auth, with only artificial project data and a test CA."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import ipaddress
import json
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dduo_solo_founder import launcher
from dduo_solo_founder.client_binding import BindingError
from dduo_solo_founder.authority_receipts import AuthorityReceipt, issue_authority_receipt
from dduo_solo_founder.db import get_session
from dduo_solo_founder.main import app
from dduo_solo_founder.models import Base, Project


SECRET = "only-artificial-transfer-test-secret"
PROOF = AuthorityReceipt(
    kind="destination_ready", project_id=str(uuid.UUID(int=471)),
    source_node_id="source", target_node_id="destination", source_generation=3,
    nonce="ab" * 32,
)
AUTHORITY = {
    "project_id": PROOF.project_id, "node_id": "source", "target_node_id": "destination",
    "generation": 3, "state": "transfer_pending", "writable": False,
}


def _certificate(tmp_path, *, expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "transfer-test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(hours=-1 if expired else 1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False).sign(key, hashes.SHA256())
    )
    certificate = tmp_path / "test-ca.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key = tmp_path / "test-key.pem"
    private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return certificate, private_key


@pytest.fixture
def tls_destination(tmp_path, monkeypatch):
    """Serve the real application through a HTTPS /api gateway, without lifespan jobs."""
    @contextmanager
    def start(*, expired=False):
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'destination.db'}", poolclass=NullPool,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def seed():
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add(Project(
                    id=PROOF.project_id, name="TLS artificial transfer", root_path="/fixture",
                    authority_node_id="source", authority_target_node_id="destination",
                    authority_generation=3, authority_state="transfer_pending",
                    authority_transfer_nonce=PROOF.nonce,
                ))
                await db.commit()

        asyncio.run(seed())

        async def session():
            async with factory() as db:
                yield db

        monkeypatch.setitem(app.dependency_overrides, get_session, session)
        monkeypatch.setenv("DDUO_AUTH_REQUIRED", "true")
        monkeypatch.setenv("BACKUP_PROJECT_ID", PROOF.project_id)
        monkeypatch.setenv("DDUO_NODE_ID", "destination")
        monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", SECRET)
        gateway = FastAPI()
        gateway.mount("/api", app)
        certificate, key = _certificate(tmp_path, expired=expired)
        config = uvicorn.Config(
            gateway, host="127.0.0.1", lifespan="off", log_level="critical",
            ssl_certfile=str(certificate), ssl_keyfile=str(key),
        )
        server = uvicorn.Server(config)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            yield f"https://127.0.0.1:{port}/api", certificate
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            asyncio.run(engine.dispose())
            assert not thread.is_alive()

    return start


def _trust_test_ca(monkeypatch, certificate):
    real_post = httpx.post
    trusted = ssl.create_default_context(cafile=str(certificate))
    assert trusted.verify_mode == ssl.CERT_REQUIRED and trusted.check_hostname

    def post(url, **kwargs):
        # Production always requests verified TLS. Trust only this synthetic CA
        # in the test; retain certificate validity and hostname validation.
        assert kwargs["verify"] is True
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real_post(url, **{**kwargs, "verify": trusted})

    monkeypatch.setattr(launcher.httpx, "post", post)


def probe(url, *, authority=None, token=None, secret=SECRET):
    return launcher._verify_destination_https(
        url, PROOF.project_id, authority or AUTHORITY,
        token or issue_authority_receipt(secret, PROOF), secret,
    )


@pytest.mark.parametrize("source_state", ["transfer_pending", "transferred"])
def test_verified_tls_reaches_real_authenticated_read_only_destination(
    tls_destination, monkeypatch, source_state,
):
    with tls_destination() as (url, certificate):
        _trust_test_ca(monkeypatch, certificate)
        assert probe(url, authority={**AUTHORITY, "state": source_state}) == url


@pytest.mark.parametrize("failure", ["untrusted", "expired", "hostname", "auth", "route"])
def test_real_tls_or_api_failure_fences_retirement(tls_destination, monkeypatch, failure):
    with tls_destination(expired=failure == "expired") as (url, certificate):
        if failure != "untrusted":
            _trust_test_ca(monkeypatch, certificate)
        if failure == "hostname":
            url = url.replace("127.0.0.1", "localhost")
        elif failure == "route":
            url += "/wrong-route"
        secret = "not-the-node-credential" if failure == "auth" else SECRET
        with pytest.raises(RuntimeError, match="HTTPS verification failed"):
            probe(url, secret=secret)


@pytest.mark.parametrize("field,value", [
    ("project_id", str(uuid.UUID(int=472))), ("node_id", "other-source"),
    ("target_node_id", "other-destination"), ("generation", 4),
    ("generation", True), ("writable", True), ("state", "active"),
])
def test_wrong_source_identity_rejected_before_network(monkeypatch, field, value):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not send credential"))
    with pytest.raises(RuntimeError, match="does not match"):
        probe("https://destination.example/api", authority={**AUTHORITY, field: value})


@pytest.mark.parametrize("failure", [
    "redirect", "non-json", "list", "missing-proof", "null-proof", "altered-proof",
    "wrong-nonce", "wrong-project", "wrong-node", "wrong-generation", "writable", "phase",
])
def test_https_response_must_authenticate_exact_transfer(monkeypatch, failure):
    result = {
        **AUTHORITY, "phase": "destination_ready",
        "activation_receipt": issue_authority_receipt(SECRET, PROOF),
    }
    status, headers = 200, {}
    if failure == "redirect":
        status, headers = 307, {"Location": "https://other.example/api"}
    elif failure == "missing-proof":
        result.pop("activation_receipt")
    elif failure == "null-proof":
        result["activation_receipt"] = None
    elif failure == "altered-proof":
        result["activation_receipt"] += "tampered"
    elif failure == "wrong-nonce":
        result["activation_receipt"] = issue_authority_receipt(SECRET, replace(PROOF, nonce="cd" * 32))
    elif failure == "wrong-project":
        result["project_id"] = str(uuid.UUID(int=472))
    elif failure == "wrong-node":
        result["target_node_id"] = "other-node"
    elif failure == "wrong-generation":
        result["generation"] = 4
    elif failure == "writable":
        result["writable"] = True
    elif failure == "phase":
        result["phase"] = "active"
    content = "not-json" if failure == "non-json" else json.dumps([] if failure == "list" else result)

    def post(url, **kwargs):
        assert kwargs["verify"] is True and kwargs["follow_redirects"] is False
        return httpx.Response(status, headers=headers, content=content, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match="HTTPS verification failed"):
        probe("https://destination.example/api")


def test_plain_http_and_forged_receipt_never_send_credentials(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not send credential"))
    with pytest.raises(BindingError, match="HTTPS"):
        probe("http://destination.example/api")
    with pytest.raises(RuntimeError, match="unauthenticated"):
        probe("https://destination.example/api", token="not-a-receipt")
