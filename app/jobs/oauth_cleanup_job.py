"""
OAuth Cleanup Job for maintenance and monitoring.
Handles cleanup of orphaned data and health monitoring across OAuth systems.
"""

import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote

import psycopg
import requests

from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.services.encryption_service import EncryptionError, decrypt_token

logger = get_logger(__name__)

# Job configuration
CLEANUP_INTERVAL_HOURS = 6  # Run every 6 hours
REDIS_STATE_SCAN_BATCH = 100  # Redis SCAN batch size
TOKEN_HEALTH_BATCH_SIZE = 50  # Token health check batch size
ORPHANED_STATE_AGE_MINUTES = 30  # Consider state orphaned after 30 minutes
INVALID_TOKEN_THRESHOLD_DAYS = 7  # Remove tokens failing for 7 days
MAX_PROCESSING_TIME_MINUTES = 30  # Maximum job execution time


class CleanupMetrics:
    """Metrics tracking for cleanup operations."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all metrics for new job run."""
        self.start_time = datetime.utcnow()
        self.redis_states_checked = 0
        self.redis_states_cleaned = 0
        self.tokens_checked = 0
        self.invalid_tokens_removed = 0
        self.corrupted_tokens_found = 0
        self.user_status_fixes = 0
        self.health_issues_found = 0
        self.processing_errors = 0
        self.total_duration_seconds = 0
        self.errors: list[dict] = []

        # Health monitoring metrics
        self.token_health_summary = {
            "healthy": 0,
            "expiring_soon": 0,
            "expired_refreshable": 0,
            "expired_no_refresh": 0,
            "corrupted": 0,
            "missing_user": 0,
        }

    def record_redis_cleanup(self, states_checked: int, states_cleaned: int):
        """Record Redis state cleanup results."""
        self.redis_states_checked += states_checked
        self.redis_states_cleaned += states_cleaned

    def record_token_removal(self, user_id: str, reason: str):
        """Record token removal."""
        self.invalid_tokens_removed += 1

        logger.info(
            "Invalid token removed", user_id=user_id, reason=reason, job_run="oauth_cleanup"
        )

    def record_corruption(self, user_id: str, error: str):
        """Record corrupted token found."""
        self.corrupted_tokens_found += 1

        error_record = {
            "user_id": user_id,
            "error": error,
            "error_type": "token_corruption",
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.errors.append(error_record)

        logger.warning(
            "Corrupted token found", user_id=user_id, error=error, job_run="oauth_cleanup"
        )

    def record_user_status_fix(self, user_id: str, issue: str):
        """Record user status inconsistency fix."""
        self.user_status_fixes += 1

        logger.info(
            "User status inconsistency fixed", user_id=user_id, issue=issue, job_run="oauth_cleanup"
        )

    def record_health_issue(self, issue_type: str, details: str):
        """Record health monitoring issue."""
        self.health_issues_found += 1

        error_record = {
            "issue_type": issue_type,
            "details": details,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.errors.append(error_record)

        logger.warning(
            "OAuth health issue found",
            issue_type=issue_type,
            details=details,
            job_run="oauth_cleanup",
        )

    def record_processing_error(self, operation: str, error: str):
        """Record processing error."""
        self.processing_errors += 1

        error_record = {
            "operation": operation,
            "error": error,
            "error_type": "processing",
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.errors.append(error_record)

        logger.error(
            "OAuth cleanup processing error",
            operation=operation,
            error=error,
            job_run="oauth_cleanup",
        )

    def update_token_health(self, health_status: str):
        """Update token health summary."""
        if health_status in self.token_health_summary:
            self.token_health_summary[health_status] += 1

    def finalize(self):
        """Finalize metrics and calculate totals."""
        self.total_duration_seconds = (datetime.utcnow() - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        """Convert metrics to dictionary for logging."""
        return {
            "job_run": "oauth_cleanup",
            "start_time": self.start_time.isoformat(),
            "total_duration_seconds": round(self.total_duration_seconds, 2),
            "redis_states_checked": self.redis_states_checked,
            "redis_states_cleaned": self.redis_states_cleaned,
            "tokens_checked": self.tokens_checked,
            "invalid_tokens_removed": self.invalid_tokens_removed,
            "corrupted_tokens_found": self.corrupted_tokens_found,
            "user_status_fixes": self.user_status_fixes,
            "health_issues_found": self.health_issues_found,
            "processing_errors": self.processing_errors,
            "token_health_summary": self.token_health_summary,
            "errors_count": len(self.errors),
        }


class OAuthCleanupJob:
    """
    Background job for OAuth system maintenance and monitoring.

    Performs cleanup of orphaned data, monitors token health,
    and fixes inconsistent states across the OAuth system.
    """

    def __init__(self):
        self.is_running = False
        self.last_run_time: datetime | None = None
        self.job_metrics = CleanupMetrics()
        self.db_url = settings.SUPABASE_DB_URL
        self.redis_url = settings.UPSTASH_REDIS_REST_URL
        self.redis_token = settings.UPSTASH_REDIS_REST_TOKEN
        self._validate_config()

    def _validate_config(self):
        """Validate job configuration."""
        if not self.db_url:
            raise ValueError("Database URL not configured for cleanup job")

        if not self.redis_url or not self.redis_token:
            logger.warning("Redis not configured - Redis cleanup will be skipped")

        logger.info(
            "OAuth cleanup job configured",
            interval_hours=CLEANUP_INTERVAL_HOURS,
            token_batch_size=TOKEN_HEALTH_BATCH_SIZE,
            max_processing_minutes=MAX_PROCESSING_TIME_MINUTES,
        )

    async def run_once(self) -> dict:
        """
        Run a single iteration of the cleanup job.

        Returns:
            Dict: Job execution metrics and results
        """
        if self.is_running:
            logger.warning("OAuth cleanup job already running, skipping this iteration")
            return {"skipped": True, "reason": "already_running"}

        try:
            self.is_running = True
            self.job_metrics.reset()

            logger.info("Starting OAuth cleanup job")

            # Create task timeout to prevent job from running too long
            cleanup_task = asyncio.create_task(self._run_cleanup_tasks())

            timeout_seconds = MAX_PROCESSING_TIME_MINUTES * 60
            await asyncio.wait_for(cleanup_task, timeout=timeout_seconds)

            # Finalize and log metrics
            self.job_metrics.finalize()
            self.last_run_time = datetime.utcnow()

            metrics = self.job_metrics.to_dict()

            logger.info(
                "OAuth cleanup job completed", **{k: v for k, v in metrics.items() if k != "errors"}
            )

            # Log errors separately if any
            if self.job_metrics.errors:
                logger.warning(
                    "OAuth cleanup job found issues",
                    error_count=len(self.job_metrics.errors),
                    errors=self.job_metrics.errors[:10],  # Log first 10 errors
                )

            return metrics

        except TimeoutError:
            logger.error("OAuth cleanup job timed out", timeout_minutes=MAX_PROCESSING_TIME_MINUTES)
            self.job_metrics.finalize()
            metrics = self.job_metrics.to_dict()
            metrics["job_error"] = f"Timed out after {MAX_PROCESSING_TIME_MINUTES} minutes"
            return metrics

        except Exception as e:
            logger.error("OAuth cleanup job failed", error=str(e), error_type=type(e).__name__)
            self.job_metrics.finalize()
            metrics = self.job_metrics.to_dict()
            metrics["job_error"] = str(e)
            return metrics

        finally:
            self.is_running = False

    async def _run_cleanup_tasks(self):
        """Run all cleanup tasks."""

        # Task 1: Clean up orphaned Redis state entries
        if self.redis_url and self.redis_token:
            await self._cleanup_redis_states()
        else:
            logger.info("Skipping Redis cleanup - Redis not configured")

        # Task 2: Check token health and remove invalid tokens
        await self._cleanup_invalid_tokens()

        # Task 3: Fix user status inconsistencies
        await self._fix_user_status_inconsistencies()

        # Task 4: Generate health monitoring report
        await self._monitor_oauth_health()

    async def _cleanup_redis_states(self):
        """Clean up orphaned Redis state entries."""
        try:
            logger.info("Starting Redis state cleanup")

            # Note: Redis REST API doesn't support SCAN, so we'll implement
            # a simplified cleanup approach by checking if Redis TTL is working
            # and cleaning up any manual test keys

            cleanup_count = await self._cleanup_redis_test_keys()

            self.job_metrics.record_redis_cleanup(
                states_checked=cleanup_count, states_cleaned=cleanup_count
            )

            logger.info("Redis state cleanup completed", states_cleaned=cleanup_count)

        except Exception as e:
            self.job_metrics.record_processing_error("redis_cleanup", str(e))

    async def _cleanup_redis_test_keys(self) -> int:
        """Clean up any test or debug keys in Redis."""
        try:
            # Clean up any health check keys or test keys that might be lingering
            test_key_patterns = [
                "oauth_state:health_check",
                "oauth_state:test_",
                "oauth_state:debug_",
            ]

            cleaned_count = 0
            headers = {"Authorization": f"Bearer {self.redis_token}"}

            for pattern in test_key_patterns:
                try:
                    url = f"{self.redis_url}/del/{quote(pattern)}"
                    response = requests.post(url, headers=headers, timeout=5)
                    if response.ok:
                        cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Error cleaning Redis key {pattern}: {e}")

            return cleaned_count

        except Exception as e:
            logger.warning(f"Error during Redis test key cleanup: {e}")
            return 0

    async def _cleanup_invalid_tokens(self):
        """Clean up invalid and corrupted token records."""
        try:
            logger.info("Starting invalid token cleanup")

            # Get all tokens for health checking
            tokens = await self._get_all_tokens()

            for token_record in tokens:
                user_id = token_record["user_id"]
                self.job_metrics.tokens_checked += 1

                try:
                    # Check if token can be decrypted
                    health_status = await self._check_token_health(token_record)
                    self.job_metrics.update_token_health(health_status)

                    # Remove tokens that should be cleaned up
                    if health_status in ["corrupted", "expired_no_refresh"]:
                        await self._remove_invalid_token(user_id, health_status)

                except Exception as e:
                    self.job_metrics.record_corruption(user_id, str(e))
                    await self._remove_invalid_token(user_id, "processing_error")

            logger.info(
                "Invalid token cleanup completed",
                tokens_checked=self.job_metrics.tokens_checked,
                tokens_removed=self.job_metrics.invalid_tokens_removed,
            )

        except Exception as e:
            self.job_metrics.record_processing_error("token_cleanup", str(e))

    async def _get_all_tokens(self) -> list[dict]:
        """Get all OAuth tokens for health checking."""
        try:
            query = """
            SELECT user_id, access_token, refresh_token, expires_at,
                   refresh_failure_count, last_refresh_attempt
            FROM oauth_tokens
            WHERE provider = 'google'
            ORDER BY updated_at DESC
            """

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    rows = cur.fetchall()

                    tokens = []
                    for row in rows:
                        (
                            user_id,
                            access_token,
                            refresh_token,
                            expires_at,
                            failure_count,
                            last_attempt,
                        ) = row
                        tokens.append(
                            {
                                "user_id": str(user_id),
                                "access_token": access_token,
                                "refresh_token": refresh_token,
                                "expires_at": expires_at,
                                "refresh_failure_count": failure_count or 0,
                                "last_refresh_attempt": last_attempt,
                            }
                        )

                    return tokens

        except Exception as e:
            logger.error(f"Error fetching tokens for health check: {e}")
            return []

    async def _check_token_health(self, token_record: dict) -> str:
        """
        Check the health status of a token record.

        Returns:
            str: Health status (healthy, expiring_soon, expired_refreshable,
                 expired_no_refresh, corrupted, missing_user)
        """
        user_id = token_record["user_id"]

        try:
            # Check if tokens can be decrypted
            if token_record["access_token"]:
                try:
                    decrypt_token(token_record["access_token"])
                except EncryptionError:
                    return "corrupted"

            if token_record["refresh_token"]:
                try:
                    decrypt_token(token_record["refresh_token"])
                except EncryptionError:
                    return "corrupted"

            # Check expiration status
            expires_at = token_record["expires_at"]
            if expires_at:
                now = datetime.utcnow()

                if expires_at <= now:
                    # Token is expired
                    if token_record["refresh_token"]:
                        # Check if refresh has been failing
                        failure_count = token_record["refresh_failure_count"]
                        if failure_count >= 3:
                            return "expired_no_refresh"
                        else:
                            return "expired_refreshable"
                    else:
                        return "expired_no_refresh"

                elif expires_at <= now + timedelta(hours=1):
                    # Token expires within 1 hour
                    return "expiring_soon"

            # Check if user still exists
            if not await self._user_exists(user_id):
                return "missing_user"

            return "healthy"

        except Exception as e:
            logger.warning(f"Error checking token health for user {user_id}: {e}")
            return "corrupted"

    async def _user_exists(self, user_id: str) -> bool:
        """Check if user exists in the database."""
        try:
            query = "SELECT 1 FROM users WHERE id = %s AND is_active = true"

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (user_id,))
                    return cur.fetchone() is not None

        except Exception as e:
            logger.warning(f"Error checking user existence for {user_id}: {e}")
            return True  # Assume user exists if we can't check

    async def _remove_invalid_token(self, user_id: str, reason: str):
        """Remove invalid token and update user status."""
        try:
            # Delete token record
            delete_query = "DELETE FROM oauth_tokens WHERE user_id = %s"

            # Update user status
            update_query = """
            UPDATE users
            SET gmail_connected = false, updated_at = NOW()
            WHERE id = %s
            """

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(delete_query, (user_id,))
                    cur.execute(update_query, (user_id,))

            self.job_metrics.record_token_removal(user_id, reason)

        except Exception as e:
            logger.error(f"Error removing invalid token for user {user_id}: {e}")

    async def _fix_user_status_inconsistencies(self):
        """Fix inconsistencies between user status and token existence."""
        try:
            logger.info("Starting user status consistency check")

            # Find users marked as connected but with no tokens
            inconsistent_users = await self._find_status_inconsistencies()

            for user_id in inconsistent_users:
                await self._fix_user_status(user_id)

            logger.info(
                "User status consistency check completed", fixes_applied=len(inconsistent_users)
            )

        except Exception as e:
            self.job_metrics.record_processing_error("status_consistency", str(e))

    async def _find_status_inconsistencies(self) -> list[str]:
        """Find users with status inconsistencies."""
        try:
            query = """
            SELECT u.id
            FROM users u
            LEFT JOIN oauth_tokens ot ON u.id = ot.user_id
            WHERE u.gmail_connected = true
            AND u.is_active = true
            AND ot.user_id IS NULL
            """

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    rows = cur.fetchall()
                    return [str(row[0]) for row in rows]

        except Exception as e:
            logger.error(f"Error finding status inconsistencies: {e}")
            return []

    async def _fix_user_status(self, user_id: str):
        """Fix user status inconsistency."""
        try:
            query = """
            UPDATE users
            SET gmail_connected = false,
                onboarding_step = CASE
                    WHEN onboarding_step = 'completed' THEN 'gmail'
                    WHEN onboarding_step = 'gmail' THEN 'profile'
                    ELSE onboarding_step
                END,
                onboarding_completed = CASE
                    WHEN onboarding_step = 'completed' THEN false
                    ELSE onboarding_completed
                END,
                updated_at = NOW()
            WHERE id = %s
            """

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (user_id,))

            self.job_metrics.record_user_status_fix(
                user_id, "gmail_connected=true but no tokens found"
            )

        except Exception as e:
            logger.error(f"Error fixing user status for {user_id}: {e}")

    async def _monitor_oauth_health(self):
        """Monitor overall OAuth system health and generate reports."""
        try:
            logger.info("Starting OAuth health monitoring")

            # Check service health
            health_issues = await self._check_service_health()

            # Check token distribution
            token_stats = await self._get_token_statistics()

            # Check recent error rates
            error_rates = await self._get_error_rates()

            # Generate health report
            health_report = {
                "timestamp": datetime.utcnow().isoformat(),
                "service_health": health_issues,
                "token_statistics": token_stats,
                "error_rates": error_rates,
                "token_health_summary": self.job_metrics.token_health_summary,
            }

            # Log health report
            logger.info("OAuth health monitoring completed", health_report=health_report)

            # Record any critical issues
            for issue in health_issues:
                if issue["severity"] == "critical":
                    self.job_metrics.record_health_issue(issue["service"], issue["description"])

        except Exception as e:
            self.job_metrics.record_processing_error("health_monitoring", str(e))

    async def _check_service_health(self) -> list[dict]:
        """Check health of OAuth-related services."""
        health_issues = []

        try:
            # Check dependent services
            from app.services.gmail_connection_service import gmail_connection_health
            from app.services.google_oauth_service import google_oauth_health
            from app.services.oauth_state_service import oauth_state_health
            from app.services.token_service import token_service_health

            services = [
                ("oauth_state", oauth_state_health),
                ("google_oauth", google_oauth_health),
                ("token_service", token_service_health),
                ("gmail_connection", gmail_connection_health),
            ]

            for service_name, health_func in services:
                try:
                    health = health_func()
                    if not health.get("healthy", False):
                        health_issues.append(
                            {
                                "service": service_name,
                                "severity": "critical",
                                "description": health.get("error", "Service unhealthy"),
                                "details": health,
                            }
                        )
                except Exception as e:
                    health_issues.append(
                        {
                            "service": service_name,
                            "severity": "critical",
                            "description": f"Health check failed: {e}",
                            "details": {},
                        }
                    )

        except Exception as e:
            logger.error(f"Error checking service health: {e}")

        return health_issues

    async def _get_token_statistics(self) -> dict:
        """Get token usage and health statistics."""
        try:
            query = """
            SELECT
                COUNT(*) as total_tokens,
                COUNT(CASE WHEN expires_at > NOW() THEN 1 END) as valid_tokens,
                COUNT(CASE WHEN expires_at <= NOW() THEN 1 END) as expired_tokens,
                COUNT(CASE WHEN refresh_token IS NOT NULL THEN 1 END) as refreshable_tokens,
                AVG(CASE WHEN refresh_failure_count IS NOT NULL THEN refresh_failure_count ELSE 0 END) as avg_failure_count,
                MAX(CASE WHEN last_refresh_attempt IS NOT NULL THEN last_refresh_attempt ELSE updated_at END) as last_activity
            FROM oauth_tokens
            WHERE provider = 'google'
            """

            with psycopg.connect(self.db_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    row = cur.fetchone()

                    if row:
                        total, valid, expired, refreshable, avg_failures, last_activity = row
                        return {
                            "total_tokens": total or 0,
                            "valid_tokens": valid or 0,
                            "expired_tokens": expired or 0,
                            "refreshable_tokens": refreshable or 0,
                            "average_failure_count": round(float(avg_failures or 0), 2),
                            "last_activity": last_activity.isoformat() if last_activity else None,
                        }

            return {}

        except Exception as e:
            logger.error(f"Error getting token statistics: {e}")
            return {}

    async def _get_error_rates(self) -> dict:
        """Get recent error rates from logs (simplified version)."""
        # This is a simplified implementation
        # In production, you might query a logging system or metrics database

        return {
            "oauth_failures_24h": "N/A - implement with logging system",
            "token_refresh_failures_24h": "N/A - implement with logging system",
            "connection_errors_24h": "N/A - implement with logging system",
        }

    def get_job_status(self) -> dict:
        """Get current job status and metrics."""
        return {
            "job_name": "oauth_cleanup",
            "is_running": self.is_running,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "interval_hours": CLEANUP_INTERVAL_HOURS,
            "max_processing_minutes": MAX_PROCESSING_TIME_MINUTES,
            "last_run_metrics": self.job_metrics.to_dict() if self.last_run_time else None,
        }

    def health_check(self) -> dict:
        """Health check for the cleanup job."""
        try:
            now = datetime.utcnow()

            # Check if job is overdue
            overdue_threshold = timedelta(hours=CLEANUP_INTERVAL_HOURS * 2)
            is_overdue = (
                self.last_run_time is not None and (now - self.last_run_time) > overdue_threshold
            )

            return {
                "healthy": not is_overdue,
                "service": "oauth_cleanup_job",
                "is_running": self.is_running,
                "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
                "is_overdue": is_overdue,
                "configuration": {
                    "interval_hours": CLEANUP_INTERVAL_HOURS,
                    "max_processing_minutes": MAX_PROCESSING_TIME_MINUTES,
                    "token_batch_size": TOKEN_HEALTH_BATCH_SIZE,
                },
            }

        except Exception as e:
            logger.error("OAuth cleanup job health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "oauth_cleanup_job",
                "error": str(e),
            }


# Singleton instance for application use
oauth_cleanup_job = OAuthCleanupJob()


# Convenience functions for easy import
async def run_oauth_cleanup_job() -> dict:
    """Run a single iteration of the OAuth cleanup job."""
    return await oauth_cleanup_job.run_once()


def get_oauth_cleanup_job_status() -> dict:
    """Get current OAuth cleanup job status."""
    return oauth_cleanup_job.get_job_status()


def oauth_cleanup_job_health() -> dict:
    """Check OAuth cleanup job health."""
    return oauth_cleanup_job.health_check()


# Background job scheduler
async def start_oauth_cleanup_scheduler():
    """
    Start the OAuth cleanup job scheduler.

    This function can be used with a task queue or run in a
    separate process/container for background processing.
    """
    logger.info("Starting OAuth cleanup job scheduler", interval_hours=CLEANUP_INTERVAL_HOURS)

    while True:
        try:
            # Run the job
            metrics = await run_oauth_cleanup_job()

            # Log summary metrics
            if not metrics.get("skipped", False):
                logger.info(
                    "OAuth cleanup job cycle completed",
                    **{
                        k: v
                        for k, v in metrics.items()
                        if k not in ["errors", "token_health_summary"]
                    },
                )

            # Wait for next interval
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)

        except KeyboardInterrupt:
            logger.info("OAuth cleanup job scheduler stopped by user")
            break
        except Exception as e:
            logger.error(
                "Error in OAuth cleanup job scheduler", error=str(e), error_type=type(e).__name__
            )
            # Wait a bit before retrying
            await asyncio.sleep(1800)  # 30 minutes


# Example usage for standalone execution
if __name__ == "__main__":
    import asyncio

    async def main():
        """Example of running the OAuth cleanup job."""
        print("Running OAuth cleanup job...")

        # Run once
        metrics = await run_oauth_cleanup_job()
        print(f"Job completed: {metrics}")

        # Check status
        status = get_oauth_cleanup_job_status()
        print(f"Job status: {status}")

        # Check health
        health = oauth_cleanup_job_health()
        print(f"Job health: {health}")

    asyncio.run(main())
