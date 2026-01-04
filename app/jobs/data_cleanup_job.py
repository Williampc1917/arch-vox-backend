"""
Data Cleanup Background Job - Automatic data retention enforcement.

This job runs daily (configured time) to:
1. Delete data past grace period (soft delete → hard delete)
2. Delete old cached data (data retention policy)
3. Clean up old audit logs (1 year retention)

Schedule:
- Production: Daily at 2 AM (low traffic time)
- Development: Manual trigger only (don't auto-delete during dev)

Design:
- Never fails (resilient)
- Logs all deletions
- Runs as async task
- Configurable schedule

Usage:
    # Start the cleanup scheduler (in main.py)
    import asyncio
    from app.jobs.data_cleanup_job import start_data_cleanup_scheduler

    asyncio.create_task(start_data_cleanup_scheduler())
"""

import asyncio
from datetime import datetime, timedelta

from app.config import settings
from app.db.pool import db_pool
from app.infrastructure.observability.logging import get_logger
from app.services.data_management.deletion_service import data_deletion_service

logger = get_logger(__name__)


class DataCleanupJob:
    """
    Background job for automatic data retention enforcement.

    Runs daily to clean up expired data.
    """

    def __init__(self):
        self.is_running = False

    async def run_cleanup(self) -> dict:
        """
        Run data cleanup job.

        Returns:
            dict: {
                "success": bool,
                "deleted_expired_grace_periods": int,
                "deleted_old_cached_data": int,
                "deleted_old_audit_logs": int,
                "errors": list,
            }
        """
        if self.is_running:
            logger.warning("Cleanup job already running, skipping")
            return {"success": False, "error": "Already running"}

        self.is_running = True
        start_time = datetime.utcnow()

        logger.info("Starting data cleanup job", timestamp=start_time.isoformat())

        result = {
            "success": True,
            "deleted_expired_grace_periods": 0,
            "deleted_old_cached_data": 0,
            "deleted_old_audit_logs": 0,
            "errors": [],
        }

        try:
            # ================================================================
            # 1. Delete data past grace period (soft delete → hard delete)
            # ================================================================
            try:
                expired_count = await self._delete_expired_grace_periods()
                result["deleted_expired_grace_periods"] = expired_count
                logger.info("Expired grace periods cleaned up", count=expired_count)
            except Exception as e:
                error_msg = f"Failed to delete expired grace periods: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)

            # ================================================================
            # 2. Delete old cached data (data retention policy)
            # ================================================================
            try:
                old_data_count = await self._delete_old_cached_data()
                result["deleted_old_cached_data"] = old_data_count
                logger.info("Old cached data cleaned up", count=old_data_count)
            except Exception as e:
                error_msg = f"Failed to delete old cached data: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)

            # ================================================================
            # 3. Delete old audit logs (1 year retention)
            # ================================================================
            try:
                audit_log_count = await self._delete_old_audit_logs()
                result["deleted_old_audit_logs"] = audit_log_count
                logger.info("Old audit logs cleaned up", count=audit_log_count)
            except Exception as e:
                error_msg = f"Failed to delete old audit logs: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)

        except Exception as e:
            logger.error("Unexpected error in cleanup job", error=str(e))
            result["success"] = False
            result["errors"].append(f"Unexpected error: {e}")

        finally:
            self.is_running = False

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        logger.info(
            "Data cleanup job completed",
            duration_seconds=duration,
            result=result,
        )

        return result

    # =======================================================================
    # PRIVATE METHODS
    # =======================================================================

    async def _delete_expired_grace_periods(self) -> int:
        """
        Delete data past grace period (soft delete → hard delete).

        Finds all records where grace_period_until < NOW() and hard deletes them.

        Returns:
            int: Number of users with data deleted
        """
        # Find users with expired grace periods (check all user data tables)
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT DISTINCT user_id
                    FROM oauth_tokens
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    UNION
                    SELECT DISTINCT user_id
                    FROM vip_list
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    UNION
                    SELECT DISTINCT user_id
                    FROM contacts
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    UNION
                    SELECT DISTINCT user_id
                    FROM email_metadata
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    UNION
                    SELECT DISTINCT user_id
                    FROM events_metadata
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    UNION
                    SELECT DISTINCT user_id
                    FROM user_settings
                    WHERE deleted_at IS NOT NULL
                      AND grace_period_until IS NOT NULL
                      AND grace_period_until <= NOW()
                    """
                )
                rows = await cursor.fetchall()

        user_ids = [row[0] for row in rows]

        if not user_ids:
            logger.info("No expired grace periods found")
            return 0

        logger.info("Found users with expired grace periods", count=len(user_ids))

        # Hard delete each user's data
        deleted_count = 0
        for user_id in user_ids:
            try:
                await data_deletion_service.hard_delete_user_data(user_id)
                deleted_count += 1
                logger.info("User data hard deleted (grace period expired)", user_id=user_id)
            except Exception as e:
                logger.error(
                    "Failed to hard delete user data",
                    user_id=user_id,
                    error=str(e),
                )

        return deleted_count

    async def _delete_old_cached_data(self) -> int:
        """
        Delete old cached data based on retention policy.

        Deletes data older than DATA_RETENTION_CACHED_DATA_DAYS (default: 90 days).

        This is for data that hasn't been explicitly deleted by user, but is
        old enough that we don't need to keep it anymore.

        Returns:
            int: Number of records deleted
        """
        retention_days = settings.DATA_RETENTION_CACHED_DATA_DAYS
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        total_deleted = 0

        # Note: For now, we don't auto-delete OAuth tokens or VIP selections
        # based on age alone (only when user requests deletion).
        # This is because these are user preferences, not cached data.
        #
        # If you add features with cached data (email bodies, calendar events,
        # etc.), add deletion logic here.
        #
        # Example:
        # async with db_pool.connection() as conn:
        #     async with conn.cursor() as cursor:
        #         await cursor.execute(
        #             """
        #             DELETE FROM email_cache
        #             WHERE created_at < %s
        #             """,
        #             (cutoff_date,),
        #         )
        #         total_deleted += cursor.rowcount

        logger.info(
            "Old cached data cleanup",
            retention_days=retention_days,
            cutoff_date=cutoff_date.isoformat(),
            deleted_count=total_deleted,
        )

        return total_deleted

    async def _delete_old_audit_logs(self) -> int:
        """
        Delete old audit logs (1 year retention).

        Audit logs are kept for 1 year for compliance, then deleted.

        Returns:
            int: Number of audit logs deleted
        """
        retention_days = settings.DATA_RETENTION_AUDIT_LOGS_DAYS
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    DELETE FROM audit_logs
                    WHERE created_at < %s
                    """,
                    (cutoff_date,),
                )
                deleted_count = cursor.rowcount

        logger.info(
            "Old audit logs deleted",
            retention_days=retention_days,
            cutoff_date=cutoff_date.isoformat(),
            deleted_count=deleted_count,
        )

        return deleted_count


# ==========================================================================
# SCHEDULER
# ==========================================================================


async def start_data_cleanup_scheduler():
    """
    Start the data cleanup scheduler.

    Runs cleanup job daily at configured hour (default: 2 AM).

    This should be called in main.py lifespan:
        asyncio.create_task(start_data_cleanup_scheduler())
    """
    cleanup_job = DataCleanupJob()
    retention_config = settings.get_data_retention_config()

    if not retention_config["cleanup_enabled"]:
        logger.info(
            "Data cleanup scheduler DISABLED",
            environment=settings.environment,
        )
        return

    schedule_hour = retention_config["cleanup_schedule_hour"]

    logger.info(
        "Data cleanup scheduler STARTED",
        schedule_hour=schedule_hour,
        environment=settings.environment,
    )

    while True:
        try:
            # Calculate time until next run (next occurrence of schedule_hour)
            now = datetime.utcnow()
            next_run = now.replace(hour=schedule_hour, minute=0, second=0, microsecond=0)

            # If we've passed today's scheduled time, schedule for tomorrow
            if now >= next_run:
                next_run += timedelta(days=1)

            sleep_seconds = (next_run - now).total_seconds()

            logger.info(
                "Data cleanup job scheduled",
                next_run=next_run.isoformat(),
                sleep_seconds=sleep_seconds,
            )

            # Sleep until next run
            await asyncio.sleep(sleep_seconds)

            # Run cleanup
            logger.info("Running scheduled data cleanup job")
            result = await cleanup_job.run_cleanup()

            logger.info(
                "Scheduled cleanup job completed",
                result=result,
            )

        except asyncio.CancelledError:
            logger.info("Data cleanup scheduler cancelled")
            break
        except Exception as e:
            logger.error(
                "Error in cleanup scheduler, will retry",
                error=str(e),
            )
            # Sleep 1 hour before retrying on error
            await asyncio.sleep(3600)


# Singleton instance for manual triggers
data_cleanup_job = DataCleanupJob()
