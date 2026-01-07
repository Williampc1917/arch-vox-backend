"""
One-time identity backfill job.

Queues VIP backfill jobs for existing users who have Gmail connected but
no contact identities yet.
"""

from app.config import settings
from app.db.helpers import fetch_all
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.features.vip_onboarding.services.scheduler import (
    VipSchedulerError,
    enqueue_vip_backfill_job,
)
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

_ACTIVE_STATUSES = {"pending", "running"}


async def _fetch_users_for_identity_backfill(limit: int | None = None) -> list[str]:
    query = """
        SELECT u.id AS user_id
        FROM users u
        JOIN oauth_tokens ot ON ot.user_id = u.id AND ot.provider = 'google'
        WHERE u.is_active = true
          AND u.gmail_connected = true
          AND ot.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM contact_identities ci
              WHERE ci.user_id = u.id
          )
        ORDER BY u.created_at ASC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT %s"
        params = (limit,)

    rows = await fetch_all(query, params)
    return [str(row["user_id"]) for row in rows]


async def run_vip_identity_backfill() -> None:
    if not settings.VIP_IDENTITY_BACKFILL_ENABLED:
        logger.warning(
            "VIP identity backfill disabled",
            flag="VIP_IDENTITY_BACKFILL_ENABLED",
        )
        return
    if not settings.VIP_IDENTITY_ENABLED:
        logger.warning(
            "VIP identity storage disabled",
            flag="VIP_IDENTITY_ENABLED",
        )
        return
    if not settings.VIP_BACKFILL_ENABLED:
        logger.warning(
            "VIP backfill disabled",
            flag="VIP_BACKFILL_ENABLED",
        )
        return

    user_ids = await _fetch_users_for_identity_backfill(settings.VIP_IDENTITY_BACKFILL_LIMIT)
    if not user_ids:
        logger.info("VIP identity backfill skipped - no eligible users found")
        return

    queued = 0
    skipped_active = 0
    failures = 0

    for user_id in user_ids:
        latest_job = await VipRepository.load_latest_job_for_user(user_id)
        if latest_job and latest_job.status in _ACTIVE_STATUSES:
            skipped_active += 1
            continue

        try:
            await enqueue_vip_backfill_job(
                user_id, trigger_reason="identity_backfill", force=True
            )
            queued += 1
        except VipSchedulerError as exc:
            failures += 1
            logger.warning(
                "Failed to enqueue identity backfill job",
                user_id=user_id,
                error=str(exc),
            )

    logger.info(
        "VIP identity backfill enqueue complete",
        requested=len(user_ids),
        queued=queued,
        skipped_active=skipped_active,
        failures=failures,
    )
