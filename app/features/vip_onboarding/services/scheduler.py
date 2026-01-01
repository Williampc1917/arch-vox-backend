"""
VIP backfill scheduler helpers.

Creates backfill jobs, enforces simple dedupe rules, and pushes job IDs
onto the Redis queue for workers to process.
"""

from datetime import UTC, datetime, timedelta

from app.config import settings
from app.features.vip_onboarding.domain import VipBackfillJob
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.infrastructure.observability.logging import get_logger
from app.services.redis_client import fast_redis

logger = get_logger(__name__)

_RECENT_COMPLETION_WINDOW = timedelta(hours=24)
_ACTIVE_STATUSES = {"pending", "running"}


class VipSchedulerError(Exception):
    """Raised when enqueueing a VIP job fails."""


async def enqueue_vip_backfill_job(
    user_id: str, trigger_reason: str = "gmail_connect", force: bool = False
) -> VipBackfillJob | None:
    """
    Create and enqueue a VIP backfill job if one is not already in progress.

    Returns the created job (or the existing active job if skipped).
    """

    now = datetime.now(UTC)
    latest_job = await VipRepository.load_latest_job_for_user(user_id)

    logger.info(
        "VIP backfill enqueue requested",
        user_id=user_id,
        trigger_reason=trigger_reason,
        force=force,
    )

    if not force and latest_job:
        if latest_job.status in _ACTIVE_STATUSES:
            logger.info(
                "Skipping VIP backfill enqueue - job already active",
                user_id=user_id,
                job_id=latest_job.id,
                status=latest_job.status,
                trigger_reason=trigger_reason,
                skip_reason="active_job",
            )
            return latest_job

        if (
            latest_job.status == "completed"
            and latest_job.completed_at
            and (now - latest_job.completed_at) < _RECENT_COMPLETION_WINDOW
        ):
            logger.info(
                "Skipping VIP backfill enqueue - job recently completed",
                user_id=user_id,
                job_id=latest_job.id,
                completed_at=latest_job.completed_at.isoformat(),
                trigger_reason=trigger_reason,
                skip_reason="recent_completion",
            )
            return latest_job

    try:
        job = await VipRepository.create_job(user_id, trigger_reason)
    except Exception as exc:
        logger.error(
            "Failed to create VIP backfill job",
            user_id=user_id,
            trigger_reason=trigger_reason,
            error=str(exc),
        )
        raise VipSchedulerError("Unable to create VIP backfill job") from exc

    # Ensure Redis is available (initialize() is idempotent)
    await fast_redis.initialize()

    queue_name = settings.VIP_BACKFILL_QUEUE_NAME
    queued = await fast_redis.push_to_list(queue_name, job.id, left=True)

    if not queued:
        await VipRepository.mark_job_failed(job.id, "Failed to queue job in Redis")
        logger.error(
            "Failed to enqueue VIP backfill job - Redis push failed",
            user_id=user_id,
            job_id=job.id,
            queue=queue_name,
        )
        raise VipSchedulerError("VIP backfill job could not be enqueued")

    logger.info(
        "VIP backfill job enqueued",
        user_id=user_id,
        job_id=job.id,
        queue=queue_name,
        trigger_reason=trigger_reason,
    )
    return job
