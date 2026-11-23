import hashlib
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select
from dduo_solo_founder import personal_memory as pm
from dduo_solo_founder.models import OutboxEvent, Project
from dduo_solo_founder.schemas import PersonalEventProposal


@pytest.fixture(autouse=True)
def private_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("DDUO_PERSONAL_CLIENT_CONFIG", str(tmp_path / "client.json"))
    monkeypatch.setenv("DDUO_PERSONAL_PUBLISHER_CONFIG", str(tmp_path / "publisher.json"))


def binding(root, project="p"):
    key = hashlib.sha256((str(root.resolve()) + "\n" + project).encode()).hexdigest()
    pm.store_private(
        pm.config_path(),
        {
            "version": 1,
            "bindings": {
                key: dict(
                    enabled=True,
                    url="https://memory.example.org",
                    connection_id="c",
                    access_token="synthetic-access",
                    expires_at=time.time() + 300,
                )
            },
        },
    )


def test_default_off_unsafe_file_and_wrong_project(tmp_path):
    assert pm.recall(tmp_path, "p", "query") == []
    binding(tmp_path)
    assert pm.recall(tmp_path, "other", "query") == []
    pm.config_path().chmod(0o644)
    assert pm.recall(tmp_path, "p", "query") == []


@pytest.mark.parametrize("case", ["context-list", "null-item", "string-score", "nan-score"])
def test_malformed_remote_memory_never_breaks_primary_context(tmp_path, case):
    binding(tmp_path)

    def handler(request):
        if request.url.path == "/api/contexts":
            payload = [] if case == "context-list" else {"context_id": "ctx"}
        elif case == "null-item":
            payload = {"items": [None]}
        else:
            payload = {
                "items": [
                    dict(
                        id="synthetic",
                        text="Synthetic evidence",
                        score="0.99" if case == "string-score" else float("nan"),
                    )
                ]
            }
        return httpx.Response(
            200, content=json.dumps(payload), headers={"Content-Type": "application/json"}
        )

    assert pm.recall(tmp_path, "p", "synthetic query", transport=httpx.MockTransport(handler)) == []


def test_malformed_private_json_disables_optional_recall(tmp_path):
    pm.config_path().write_text("[]")
    pm.config_path().chmod(0o600)
    assert pm.recall(tmp_path, "p", "synthetic query") == []


def test_malformed_publisher_configuration_does_not_break_consolidation():
    pm.store_private(pm.config_path("publisher"), {"version": 1, "projects": []})
    assert not pm.publishing_enabled("p")


def test_personal_quota_reserves_primary_context_and_keeps_whole_fragments():
    from dduo_solo_founder.hooks import compose_founder_context
    from dduo_solo_founder.context_budget import context_units
    import json

    briefing = {
        "project": {"name": "Synthetic", "context": "x" * 1500},
        "personal_memories": [{"id": str(i), "text": "synthetic " * 35} for i in range(10)],
    }
    result = compose_founder_context(briefing, contracts="standing " * 80)
    rows = [json.loads(line) for line in result.content.splitlines()]
    personal = [r for r in rows if r.get("component") == "personal_memories"]
    assert len(personal) < 10 and personal
    assert all(r["payload"]["text"] == "synthetic " * 35 for r in personal)
    assert context_units(result.content) <= 9000


def test_recall_threshold_quota_lineage_and_timeout(tmp_path):
    binding(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if request.url.path == "/api/contexts":
            assert json.loads(request.content)["connection_id"] == "c"
            return httpx.Response(200, json={"context_id": "ctx"})
        payload = json.loads(request.content)
        assert payload["character_budget"] == 1080 and payload["threshold"] == 0.75
        return httpx.Response(
            200,
            json={
                "items": [
                    dict(id="weak", score=0.74, text="no"),
                    dict(
                        id="mirrored",
                        score=0.99,
                        text="same project",
                        origin={"source_ref": {"project_id": "p"}},
                    ),
                    dict(id="valid", score=0.9, text="Synthetic processor insight"),
                    dict(id="valid", score=0.99, text="duplicate"),
                    dict(id="oversized", score=0.99, text="x" * 10000),
                ]
            },
        )

    result = pm.recall(tmp_path, "p", "Synthetic query", transport=httpx.MockTransport(handler))
    assert [m["id"] for m in result] == ["valid"] and len(calls) == 2
    assert result[0]["untrusted_evidence"]

    def offline(request):
        raise httpx.ReadTimeout("offline")

    assert pm.recall(tmp_path, "p", "q", transport=httpx.MockTransport(offline)) == []


def test_slow_stream_cannot_exceed_optional_recall_deadline(tmp_path, monkeypatch):
    import asyncio

    binding(tmp_path)
    monkeypatch.setattr(pm, "TIMEOUT", 0.05)
    closed = []

    class Trickle(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            yield b'{"items": []}'
            for _ in range(50):
                time.sleep(0.01)
                yield b" "

        async def __aiter__(self):
            yield b'{"items": []}'
            for _ in range(50):
                await asyncio.sleep(0.01)
                yield b" "

        def close(self):
            closed.append(True)

        async def aclose(self):
            closed.append(True)

    def handler(request):
        return (
            httpx.Response(200, json={"context_id": "ctx"})
            if request.url.path.endswith("contexts")
            else httpx.Response(200, stream=Trickle())
        )

    started = time.monotonic()
    assert pm.recall(tmp_path, "p", "q", transport=httpx.MockTransport(handler)) == []
    assert time.monotonic() - started < 0.25
    assert closed


def test_oversized_response_is_not_injected_into_primary_context(tmp_path):
    binding(tmp_path)

    def handler(request):
        return (
            httpx.Response(200, json={"context_id": "ctx"})
            if request.url.path.endswith("contexts")
            else httpx.Response(
                200,
                json={
                    "padding": "x" * 100000,
                    "items": [dict(id="synthetic", text="Synthetic evidence", score=0.99)],
                },
            )
        )

    assert pm.recall(tmp_path, "p", "q", transport=httpx.MockTransport(handler)) == []


def test_unexpected_compression_is_not_decoded_by_optional_recall(tmp_path):
    binding(tmp_path)
    import gzip

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            content=gzip.compress(b'{"context_id":"ctx"}'),
            headers={"Content-Encoding": "gzip"},
        )

    assert pm.recall(tmp_path, "p", "q", transport=httpx.MockTransport(handler)) == []


async def test_sync_adapters_do_not_block_an_existing_event_loop(tmp_path):
    binding(tmp_path)
    assert pm.recall(tmp_path, "p", "q") == []
    with pytest.raises(ValueError, match="personal_delivery_requires_sync_worker"):
        pm.deliver("p", {})


def test_rotated_refresh_is_kept_when_later_recall_times_out(tmp_path, monkeypatch):
    import asyncio

    binding(tmp_path)
    data = pm.private_config()
    entry = next(iter(data["bindings"].values()))
    entry.update(
        expires_at=0,
        token_endpoint="https://memory.example.org/token",
        refresh_token="old",
        client_id="client",
    )
    pm.store_private(pm.config_path(), data)
    monkeypatch.setattr(pm, "TIMEOUT", 0.05)

    async def handler(request):
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "rotated",
                    "expires_in": 300,
                },
            )
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"context_id": "ctx"})

    started = time.monotonic()
    assert pm.recall(tmp_path, "p", "q", transport=httpx.MockTransport(handler)) == []
    assert time.monotonic() - started < 0.25
    assert next(iter(pm.private_config()["bindings"].values()))["refresh_token"] == "rotated"


@pytest.mark.parametrize("status", [200, 201])
async def test_publisher_transactional_outbox_and_idempotence(db_factory, status):
    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": dict(
                    enabled=True,
                    url="https://memory.example.org",
                    connection_id="conn",
                    publisher_key="synthetic-secret",
                )
            },
        },
    )
    proposal = PersonalEventProposal(
        target_node_type="episode",
        target_node_key="release",
        text="Synthetic release published",
        epistemic_status="confirmed",
    )
    memory = SimpleNamespace(id="memory", node_type="episode", node_key="release", revision=1)
    async with db_factory() as db:
        db.add(Project(id="p", name="Synthetic", root_path="/synthetic"))
        await db.commit()
        await pm.enqueue_personal_event(db, "p", "job", memory, proposal)
        await db.commit()
        await pm.enqueue_personal_event(db, "p", "job", memory, proposal)
        await db.commit()
        events = list((await db.scalars(select(OutboxEvent))).all())
        assert len(events) == 1
        assert "synthetic-secret" not in json.dumps(events[0].payload)
        assert events[0].payload["claim_version"] == 1
        payload = events[0].payload
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/bridge/v1/events"
        assert request.headers["authorization"] == "Bearer conn.synthetic-secret"
        return httpx.Response(status, json={"source_id": "source"})

    import asyncio

    await asyncio.to_thread(pm.deliver, "p", payload, transport=httpx.MockTransport(handler))
    assert len(calls) == 1


def test_rotating_refresh_and_private_write(tmp_path):
    binding(tmp_path)
    data = pm.private_config()
    entry = next(iter(data["bindings"].values()))
    entry.update(
        expires_at=0,
        token_endpoint="https://memory.example.org/token",
        refresh_token="old-refresh",
        client_id="client",
    )
    pm.store_private(pm.config_path(), data)

    def handler(request):
        if request.url.path == "/token":
            assert b"old-refresh" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 300,
                },
            )
        assert request.headers["authorization"] == "Bearer fresh-access"
        return httpx.Response(
            200,
            json={"context_id": "ctx"} if request.url.path.endswith("contexts") else {"items": []},
        )

    assert pm.recall(tmp_path, "p", "query", transport=httpx.MockTransport(handler)) == []
    stored = next(iter(pm.private_config()["bindings"].values()))
    assert stored["refresh_token"] == "rotated-refresh"
    assert pm.config_path().stat().st_mode & 0o077 == 0
    assert pm.recall(tmp_path, "p", "query", fraction=0) == []
    assert pm.recall(tmp_path, "p", "  ") == []
    assert pm._recall_locked(tmp_path, "p", "query", deadline=time.monotonic() - 1) == []


async def test_sleep_queues_personal_event_and_outbox_retries_after_commit(db_factory, monkeypatch):
    from datetime import timedelta
    from test_sleep_engine import SuccessfulProvider, NoHits, seed_pending
    from test_outbox import FakeEmbeddings
    from dduo_solo_founder.sleep_engine import schedule_sleep, process_one_sleep_job
    from dduo_solo_founder.outbox import OutboxProcessor
    from dduo_solo_founder.service import utcnow

    class Provider(SuccessfulProvider):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            result["topics"][0]["personal_events"] = [
                dict(
                    target_node_type="episode",
                    target_node_key="android-release",
                    text="Synthetic release testing is underway.",
                    epistemic_status="reported",
                )
            ]
            assert "authorized personal-memory bridge" in kwargs["instructions"]
            return result

    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        pm.store_private(
            pm.config_path("publisher"),
            {
                "version": 1,
                "projects": {
                    project.id: dict(
                        enabled=True,
                        url="https://memory.example",
                        connection_id="connection",
                        publisher_key="s" * 40,
                    )
                },
            },
        )
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), Provider(turn.id, db))
        await db.refresh(job)
        assert job.status == "completed"
        personal = await db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "personal.publish")
        )
        assert personal and personal.payload["source_ref"]["sleep_job_id"] == job.id
        captured = []

        def failing_delivery(project, payload):
            captured.append(payload)
            raise RuntimeError("sensitive provider detail must not reach the log")

        monkeypatch.setattr(pm, "deliver", failing_delivery)
        processor = OutboxProcessor(FakeEmbeddings())
        assert await processor.process_one(db)  # existing Qdrant projection has priority
        assert await processor.process_one(db)
        await db.refresh(personal)
        assert (
            personal.status == "failed" and personal.last_error == "personal_delivery_unavailable"
        )
        stable = personal.payload.copy()
        personal.next_attempt_at = utcnow() - timedelta(seconds=1)
        await db.commit()
        monkeypatch.setattr(pm, "deliver", lambda project, payload: captured.append(payload))
        assert await processor.process_one(db)
        await db.refresh(personal)
        assert personal.status == "processed" and personal.attempts == 2
        assert captured == [stable, stable]


@pytest.mark.parametrize("failure", ["none", "state", "issuer", "connection"])
def test_native_pkce_login_validates_identity_and_stores_tokens_privately(
    tmp_path, monkeypatch, failure
):
    import http.server
    import io
    import webbrowser
    from urllib.parse import parse_qs, urlparse

    observed = {}

    class Server:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 18741)
            self.handler = handler

        def handle_request(self):
            params = parse_qs(urlparse(observed["url"]).query)
            h = self.handler.__new__(self.handler)
            h.path = "/callback?code=fixture-code&state=" + (
                "wrong" if failure == "state" else params["state"][0]
            )
            h.send_error = lambda status: observed.update(error=status)
            h.send_response = lambda status: observed.update(status=status)
            h.end_headers = lambda: None
            h.wfile = io.BytesIO()
            h.do_GET()
            h.log_message("silenced")

        def server_close(self):
            observed["closed"] = True

    monkeypatch.setattr(http.server, "HTTPServer", Server)
    monkeypatch.setattr(webbrowser, "open", lambda url: observed.update(url=url))

    def get(url, **kwargs):
        if "oauth-authorization-server" in url:
            return httpx.Response(
                200,
                json=dict(
                    issuer="https://wrong.example/"
                    if failure == "issuer"
                    else "https://memory.example",
                    authorization_endpoint="https://memory.example/authorize",
                    token_endpoint="https://memory.example/token",
                ),
            )
        assert kwargs["headers"]["Authorization"] == "Bearer synthetic-login-token"
        return httpx.Response(
            200,
            json={"project_connection": "wrong" if failure == "connection" else "conn"},
            request=httpx.Request("GET", url),
        )

    def post(url, **kwargs):
        data = kwargs["data"]
        params = parse_qs(urlparse(observed["url"]).query)
        expected = (
            pm.base64.urlsafe_b64encode(pm.hashlib.sha256(data["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert params["code_challenge"] == [expected]
        assert data["code"] == "fixture-code" and "client_secret" not in data
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-login-token",
                "refresh_token": "synthetic-refresh",
                "expires_in": 300,
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(pm.httpx, "get", get)
    monkeypatch.setattr(pm.httpx, "post", post)
    args = SimpleNamespace(
        url="https://memory.example",
        issuer="https://memory.example",
        audience="https://memory.example/mcp",
        client_id="client",
        connection="conn",
        workspace_root=str(tmp_path),
        project="p",
    )
    if failure != "none":
        with pytest.raises(ValueError):
            pm.login(args)
        assert not pm.config_path().exists()
    else:
        pm.login(args)
        data = pm.private_config()
        record = next(iter(data["bindings"].values()))
        assert record["access_token"] == "synthetic-login-token" and observed["closed"]
    args.url = "http://insecure.example"
    with pytest.raises(ValueError):
        pm.login(args)


def test_publisher_cli_configuration_validation_and_delivery_failure(tmp_path, monkeypatch):
    import getpass
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "connector",
            "configure-publisher",
            "--project",
            "p",
            "--url",
            "https://memory.example",
            "--connection",
            "conn",
        ],
    )
    monkeypatch.setattr(getpass, "getpass", lambda *a: "conn." + "s" * 40)
    pm.main()
    assert pm.publishing_enabled("p")
    with pytest.raises(ValueError, match="personal_delivery_failed"):
        pm.deliver(
            "p",
            {"connection_id": "conn"},
            transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        )
    with pytest.raises(ValueError, match="personal_connection_disabled"):
        pm.deliver("missing", {})
    data = pm.private_config("publisher")
    data["projects"]["p"]["url"] = "http://insecure"
    pm.store_private(pm.config_path("publisher"), data)
    assert not pm.publishing_enabled("p")
    monkeypatch.setattr(getpass, "getpass", lambda *a: "other." + "s" * 40)
    with pytest.raises(ValueError, match="connection_mismatch"):
        pm.main()
    monkeypatch.setattr(getpass, "getpass", lambda *a: "short")
    with pytest.raises(ValueError, match="invalid_publisher_credential"):
        pm.main()
    pm.store_private(pm.config_path(), {"version": 2})
    with pytest.raises(ValueError, match="unsupported_personal_configuration"):
        pm.private_config()


@pytest.mark.parametrize("case", ["stale", "other-conflict", "bad-json", "oversized", "encoded"])
def test_superseded_delivery_is_terminal_but_other_conflicts_remain_failures(case):
    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": {
                    "enabled": True,
                    "url": "https://memory.example",
                    "connection_id": "c",
                    "publisher_key": "synthetic-key",
                }
            },
        },
    )

    def handler(request):
        if case == "encoded":
            import gzip

            return httpx.Response(
                409,
                stream=httpx.ByteStream(gzip.compress(b'{"error":"stale_claim_version"}')),
                headers={"Content-Encoding": "gzip"},
            )
        if case == "bad-json":
            return httpx.Response(409, content=b"stale_claim_version")
        return httpx.Response(
            409,
            json={
                "error": "stale_claim_version"
                if case != "other-conflict"
                else "idempotency_key_reused",
                **({"padding": "x" * 10000} if case == "oversized" else {}),
            },
        )

    if case == "stale":
        pm.deliver("p", {"connection_id": "c"}, transport=httpx.MockTransport(handler))
    else:
        with pytest.raises(ValueError):
            pm.deliver("p", {"connection_id": "c"}, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("error", ["stale_claim_version", "idempotency_key_reused"])
async def test_outbox_closes_only_a_proven_superseded_event(db_factory, monkeypatch, error):
    from dduo_solo_founder.outbox import OutboxProcessor
    from test_outbox import FakeEmbeddings

    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": {
                    "enabled": True,
                    "url": "https://memory.example",
                    "connection_id": "c",
                    "publisher_key": "synthetic-key",
                }
            },
        },
    )
    actual = pm.deliver
    transport = httpx.MockTransport(lambda request: httpx.Response(409, json={"error": error}))
    monkeypatch.setattr(
        pm, "deliver", lambda project, payload: actual(project, payload, transport=transport)
    )
    async with db_factory() as db:
        db.add(Project(id="p", name="Synthetic", root_path="/synthetic"))
        await db.commit()
        event = OutboxEvent(
            project_id="p",
            event_type="personal.publish",
            aggregate_id="synthetic-memory",
            payload={"connection_id": "c"},
        )
        db.add(event)
        await db.commit()
        processor = OutboxProcessor(FakeEmbeddings())
        assert await processor.process_one(db)
        await db.refresh(event)
        assert event.status == ("processed" if error == "stale_claim_version" else "failed")
        assert event.attempts == 1
        assert not await processor.process_one(db)


@pytest.mark.parametrize("status", [200, 409])
def test_slow_receipt_is_cancelled_before_the_outbox_can_stall(monkeypatch, status):
    import asyncio

    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": {
                    "enabled": True,
                    "url": "https://memory.example",
                    "connection_id": "c",
                    "publisher_key": "synthetic-key",
                }
            },
        },
    )
    monkeypatch.setattr(pm, "TIMEOUT", 0.05)
    closed = []

    class SlowError(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"source_id":"source"}' if status == 200 else b'{"error":"stale_claim_version"}'
            for _ in range(50):
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        pm.deliver(
            "p",
            {"connection_id": "c"},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, stream=SlowError())
            ),
        )
    assert time.monotonic() - started < 0.25
    assert closed


@pytest.mark.parametrize("case", ["html", "empty", "missing-id", "wrong-id-type"])
def test_http_success_without_an_ingestion_receipt_is_not_acknowledged(case):
    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": {
                    "enabled": True,
                    "url": "https://memory.example",
                    "connection_id": "c",
                    "publisher_key": "synthetic-key",
                }
            },
        },
    )

    def handler(request):
        if case == "html":
            return httpx.Response(200, content=b"<html>Synthetic wrong proxy route</html>")
        if case == "empty":
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json={} if case == "missing-id" else {"source_id": []})

    with pytest.raises(ValueError, match="personal_delivery_failed"):
        pm.deliver("p", {"connection_id": "c"}, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("node_key", ["x" * 300, "γ" * 500], ids=["300-ascii", "500-unicode"])
async def test_long_memory_identity_produces_a_stable_supported_bridge_claim(db_factory, node_key):
    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": dict(
                    enabled=True,
                    url="https://memory.example",
                    connection_id="c",
                    publisher_key="synthetic",
                )
            },
        },
    )
    memory = SimpleNamespace(id="memory", node_type="reusable_fact", node_key=node_key, revision=1)
    proposal = PersonalEventProposal(
        target_node_type="reusable_fact", target_node_key=node_key, text="Synthetic event"
    )
    async with db_factory() as db:
        db.add(Project(id="p", name="Synthetic", root_path="/synthetic"))
        await db.commit()
        await pm.enqueue_personal_event(db, "p", "job-1", memory, proposal)
        memory.revision = 2
        await pm.enqueue_personal_event(db, "p", "job-2", memory, proposal)
        events = list(
            (await db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at))).all()
        )
        assert len(events) == 2
        assert len(events[0].payload["claim_key"]) <= 300
        assert events[0].payload["claim_key"] == events[1].payload["claim_key"]
        assert [row.payload["claim_version"] for row in events] == [1, 2]


@pytest.mark.parametrize(
    "field,value", [("text", "Different synthetic event"), ("epistemic_status", "hypothesis")]
)
async def test_same_event_identity_never_silently_discards_a_changed_proposal(
    db_factory, field, value
):
    pm.store_private(
        pm.config_path("publisher"),
        {
            "version": 1,
            "projects": {
                "p": dict(
                    enabled=True,
                    url="https://memory.example",
                    connection_id="c",
                    publisher_key="synthetic",
                )
            },
        },
    )
    memory = SimpleNamespace(id="memory", node_type="episode", node_key="release", revision=1)
    proposal = PersonalEventProposal(
        target_node_type="episode", target_node_key="release", text="Synthetic event"
    )
    async with db_factory() as db:
        db.add(Project(id="p", name="Synthetic", root_path="/synthetic"))
        await db.commit()
        await pm.enqueue_personal_event(db, "p", "job", memory, proposal)
        await db.commit()
        with pytest.raises(ValueError, match="personal_event_idempotency_conflict"):
            await pm.enqueue_personal_event(
                db, "p", "job", memory, proposal.model_copy(update={field: value})
            )


@pytest.mark.parametrize("case", ["same-project-secondary", "shared-secondary", "preserve-all"])
def test_recall_handles_every_origin_not_only_the_primary_origin(tmp_path, case):
    binding(tmp_path)
    primary = dict(claim_identity="foreign:first", source_ref={"project_id": "foreign"})
    secondary = dict(
        claim_identity="shared:second",
        source_ref={"project_id": "p" if case == "same-project-secondary" else "foreign-2"},
    )
    first = dict(
        id="one",
        text="Synthetic combined evidence",
        score=0.99,
        origin=primary,
        origins=[primary, secondary],
    )
    second_primary = dict(claim_identity="foreign:third", source_ref={"project_id": "foreign-3"})
    second = dict(
        id="two",
        text="Synthetic overlapping evidence",
        score=0.98,
        origin=second_primary,
        origins=[second_primary, secondary],
    )

    def handler(request):
        return httpx.Response(
            200,
            json={"context_id": "ctx"}
            if request.url.path.endswith("contexts")
            else {"items": [first, second] if case == "shared-secondary" else [first]},
        )

    result = pm.recall(tmp_path, "p", "synthetic", transport=httpx.MockTransport(handler))
    if case == "same-project-secondary":
        assert result == []
    else:
        assert len(result) == 1
        assert result[0]["origins"] == [primary, secondary]
