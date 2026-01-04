"""
VIP backfill job runner.

Consumes job IDs from Redis, loads job context, and delegates to the
backfill service. This module is intended to run inside the worker
container/service.
"""

import asyncio
from collections import Counter

from app.config import settings
from app.features.vip_onboarding.pipeline.aggregation import contact_aggregation_service
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.features.vip_onboarding.services.backfill_service import VipBackfillService
from app.infrastructure.observability.logging import get_logger
from app.services.infrastructure.redis_client import fast_redis

logger = get_logger(__name__)

vip_backfill_service = VipBackfillService()


async def _process_job(job_id: str) -> None:
    """Process a single VIP backfill job by ID."""
    job = await VipRepository.load_job(job_id)

    if not job:
        logger.warning("VIP backfill job missing", job_id=job_id)
        return

    if job.status in {"completed", "failed"}:
        logger.info(
            "Skipping VIP backfill job - already finished", job_id=job_id, status=job.status
        )
        return

    if job.status == "running":
        logger.info("Skipping VIP backfill job - already running", job_id=job_id)
        return

    try:
        await VipRepository.mark_job_running(job.id)
        await vip_backfill_service.run(job)
        await contact_aggregation_service.aggregate_contacts_for_user(job.user_id)
        await VipRepository.mark_job_completed(job.id)
        logger.info(
            "VIP backfill job processed successfully",
            job_id=job_id,
            user_id=job.user_id,
        )
    except Exception as exc:
        await VipRepository.mark_job_failed(job.id, str(exc))
        logger.error(
            "VIP backfill job failed",
            job_id=job_id,
            user_id=job.user_id,
            error=str(exc),
        )


async def _worker_loop() -> None:
    """Continuously consume VIP backfill jobs from Redis."""
    queue_name = settings.VIP_BACKFILL_QUEUE_NAME
    metrics = Counter()

    logger.info("VIP backfill worker started", queue=queue_name)

    while True:
        try:
            payload = await fast_redis.pop_from_list(queue_name, timeout=5)

            if not payload:
                await asyncio.sleep(1)
                continue

            metrics["jobs_seen"] += 1
            job_id = payload.strip()
            await _process_job(job_id)
            metrics["jobs_processed"] += 1

        except asyncio.CancelledError:
            logger.info("VIP backfill worker cancelled, shutting down")
            raise
        except Exception as exc:
            metrics["worker_errors"] += 1
            logger.error("Error in VIP backfill worker loop", error=str(exc))
            await asyncio.sleep(5)


async def start_vip_backfill_scheduler() -> None:
    """Public entry point for launching the VIP backfill worker."""
    await fast_redis.initialize()
    await _worker_loop()


if __name__ == "__main__":
    asyncio.run(start_vip_backfill_scheduler())
