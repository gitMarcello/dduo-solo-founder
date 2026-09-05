from __future__ import annotations

from types import SimpleNamespace

import pytest

from dduo_solo_founder import db as database
from dduo_solo_founder import worker


async def test_database_health_and_session(monkeypatch):
    executed = []

    class Connection:
        async def exec_driver_sql(self, statement):
            executed.append(statement)

    class Context:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(database, "engine", SimpleNamespace(connect=lambda: Context()))
    await database.init_db()
    assert executed == ["SELECT 1"]


async def test_get_session_yields_session(monkeypatch):
    expected = object()

    class Context:
        async def __aenter__(self):
            return expected

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(database, "SessionLocal", lambda: Context())
    values = [item async for item in database.get_session()]
    assert values == [expected]


async def test_worker_processes_then_sleeps(monkeypatch):
    calls = []

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    class Processor:
        async def process_one(self, session):
            calls.append(session)
            return len(calls) == 1

    async def stop_sleep(_):
        raise RuntimeError("stop")

    async def initialized():
        return None

    async def no_jobs(*args):
        return 0

    async def no_sleep(*args):
        return False

    monkeypatch.setattr(worker, "init_db", initialized)
    monkeypatch.setattr(worker, "embedding_service", lambda: object())
    monkeypatch.setattr(worker, "OutboxProcessor", lambda _: Processor())
    monkeypatch.setattr(worker, "CliSleepProvider", lambda: object())
    monkeypatch.setattr(worker, "recover_interrupted_jobs", no_jobs)
    monkeypatch.setattr(worker, "schedule_due_sleep", no_jobs)
    monkeypatch.setattr(worker, "process_one_sleep_job", no_sleep)
    monkeypatch.setattr(worker, "SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(worker.asyncio, "sleep", stop_sleep)
    with pytest.raises(RuntimeError, match="stop"):
        await worker.run()
    assert len(calls) == 2


def test_worker_main(monkeypatch):
    seen = []
    monkeypatch.setattr(worker.asyncio, "run", lambda value: seen.append(value))
    coroutine = worker.run()
    monkeypatch.setattr(worker, "run", lambda: coroutine)
    worker.main()
    assert seen == [coroutine]
    coroutine.close()
