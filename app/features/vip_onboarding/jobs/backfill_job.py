"""
VIP backfill job runner.

Eventually this module will run inside the worker service, pop enqueue
requests from Redis, and delegate to VipBackfillService to process the
Gmail + Calendar history for each user.
"""

import asyncio

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


async def _idle_worker_loop() -> None:
    """
    Lightweight loop that keeps the worker process alive.

    Replace this with real scheduling logic once the job is implemented.
    """

    logger.info("VIP backfill worker placeholder started.")
    while True:
        await asyncio.sleep(60)


async def start_vip_backfill_scheduler() -> None:
    """
    Entry point for running the VIP backfill worker.

    The actual implementation will grab jobs from Redis and execute them,
    but for now we keep the process alive so deployment plumbing can be
    validated.
    """

    await _idle_worker_loop()


if __name__ == "__main__":
    asyncio.run(start_vip_backfill_scheduler())

