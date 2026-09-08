from __future__ import annotations

import asyncio

from dduo_solo_founder.config import get_settings
from dduo_solo_founder.db import SessionLocal, init_db
from dduo_solo_founder.embeddings import embedding_service
from dduo_solo_founder.outbox import OutboxProcessor
from dduo_solo_founder.sleep_engine import (
    CliSleepProvider,
    process_one_sleep_job,
    recover_interrupted_jobs,
    schedule_due_sleep,
)


async def run() -> None:
    await init_db()
    embeddings = embedding_service()
    processor = OutboxProcessor(embeddings)
    sleep_provider = CliSleepProvider()
    poll_seconds = get_settings().sleep_poll_seconds
    async with SessionLocal() as session:
        await recover_interrupted_jobs(session)
    while True:
        async with SessionLocal() as session:
            await schedule_due_sleep(session)
        async with SessionLocal() as session:
            slept = await process_one_sleep_job(session, embeddings, sleep_provider)
        async with SessionLocal() as session:
            projected = await processor.process_one(session)
        processed = slept or projected
        if not processed:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
