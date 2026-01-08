"""
VIP backfill job runner.

Consumes job IDs from Redis, loads job context, and delegates to the
backfill service. This module is intended to run inside the worker
container/service.
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.features.vip_onboarding.pipeline.aggregation import contact_aggregation_service
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.features.vip_onboarding.services.backfill_service import VipBackfillService
from app.infrastructure.observability.logging import get_logger
from app.services.infrastructure.redis_client import fast_redis

logger = get_logger(__name__)

vip_backfill_service = VipBackfillService()
_INFLIGHT_SUFFIX = ":inflight"


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

    if not settings.VIP_BACKFILL_ENABLED:
        await VipRepository.mark_job_failed(job.id, "VIP backfill disabled by feature flag")
        logger.info(
            "Skipping VIP backfill job - disabled",
            job_id=job_id,
            user_id=job.user_id,
            flag="VIP_BACKFILL_ENABLED",
        )
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


async def _requeue_stuck_jobs(queue_name: str, inflight_name: str) -> None:
    inflight_ids = await fast_redis.list_range(inflight_name)
    if not inflight_ids:
        return

    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=settings.VIP_BACKFILL_STUCK_TIMEOUT_MINUTES)

    for job_id in inflight_ids:
        job_id = job_id.strip()
        if not job_id:
            continue

        job = await VipRepository.load_job(job_id)
        if not job:
            await fast_redis.ack_from_inflight(inflight_name, job_id)
            continue

        if job.status in {"completed", "failed"}:
            await fast_redis.ack_from_inflight(inflight_name, job_id)
            continue

        stuck = False
        if job.status == "running" and job.started_at and job.started_at <= cutoff:
            stuck = True
        elif job.status == "pending" and job.created_at and job.created_at <= cutoff:
            stuck = True

        if not stuck:
            continue

        retry_key = f"{queue_name}:retries:{job_id}"
        retries = await fast_redis.incr_with_ttl(retry_key, settings.VIP_BACKFILL_RETRY_TTL_SECONDS)

        if retries is not None and retries > settings.VIP_BACKFILL_MAX_AUTO_RETRIES:
            await VipRepository.mark_job_failed(
                job_id, "VIP backfill job stuck; auto-retry limit exceeded"
            )
            await fast_redis.ack_from_inflight(inflight_name, job_id)
            logger.warning(
                "VIP backfill job failed after auto-retry limit",
                job_id=job_id,
                retries=retries,
            )
            continue

        await VipRepository.reset_job_pending(job_id)
        requeued = await fast_redis.requeue_from_inflight(inflight_name, queue_name, job_id)
        if requeued:
            logger.warning(
                "VIP backfill job requeued after stall",
                job_id=job_id,
                retries=retries,
            )
        else:
            logger.error("Failed to requeue stuck VIP backfill job", job_id=job_id)


async def _worker_loop() -> None:
    """Continuously consume VIP backfill jobs from Redis."""
    queue_name = settings.VIP_BACKFILL_QUEUE_NAME
    inflight_name = f"{queue_name}{_INFLIGHT_SUFFIX}"
    metrics = Counter()
    last_watchdog = datetime.now(UTC)

    logger.info("VIP backfill worker started", queue=queue_name)

    while True:
        try:
            payload = await fast_redis.pop_to_inflight(queue_name, inflight_name, timeout=5)

            if not payload:
                await asyncio.sleep(1)
            else:
                metrics["jobs_seen"] += 1
                job_id = payload.strip()
                try:
                    await _process_job(job_id)
                    metrics["jobs_processed"] += 1
                finally:
                    await fast_redis.ack_from_inflight(inflight_name, job_id)

            now = datetime.now(UTC)
            if (
                now - last_watchdog
            ).total_seconds() >= settings.VIP_BACKFILL_WATCHDOG_INTERVAL_SECONDS:
                await _requeue_stuck_jobs(queue_name, inflight_name)
                last_watchdog = now

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
