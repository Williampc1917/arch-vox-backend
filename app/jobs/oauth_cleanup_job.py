"""
OAuth Cleanup Job for maintenance and monitoring.
Handles cleanup of orphaned data and health monitoring across OAuth systems.
REFACTORED: Now uses database connection pool instead of direct psycopg connections.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app.db.helpers import DatabaseError, execute_query, fetch_all, fetch_one, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.services.infrastructure.encryption_service import EncryptionError, decrypt_token

logger = get_logger(__name__)

# Job configuration
CLEANUP_INTERVAL_HOURS = 6  # Run every 6 hours
REDIS_STATE_SCAN_BATCH = 100  # Redis SCAN batch size
TOKEN_HEALTH_BATCH_SIZE = 50  # Token health check batch size
ORPHANED_STATE_AGE_MINUTES = 30  # Consider state orphaned after 30 minutes
INVALID_TOKEN_THRESHOLD_DAYS = 7  # Remove tokens failing for 7 days
MAX_PROCESSING_TIME_MINUTES = 30  # Maximum job execution time

# FIXED: Add grace period to prevent cleanup race conditions
OAUTH_COMPLETION_GRACE_PERIOD_MINUTES = (
    30  # Don't cleanup tokens created/updated in last 30 minutes
)
TOKEN_HEALTH_CHECK_GRACE_PERIOD_HOURS = 2  # Don't cleanup tokens updated in last 2 hours


class CleanupMetrics:
    """Metrics tracking for cleanup operations."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all metrics for new job run."""
        self.start_time = datetime.now(UTC)  # Fixed: use timezone-aware datetime
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
            "timestamp": datetime.now(UTC).isoformat(),  # Fixed: use timezone-aware datetime
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
            "timestamp": datetime.now(UTC).isoformat(),  # Fixed: use timezone-aware datetime
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
            "timestamp": datetime.now(UTC).isoformat(),  # Fixed: use timezone-aware datetime
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
        self.total_duration_seconds = (
            datetime.now(UTC) - self.start_time
        ).total_seconds()  # Fixed: use timezone-aware datetime

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


class OAuthCleanupJobError(Exception):
    """Custom exception for OAuth cleanup job operations."""

    def __init__(self, message: str, operation: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.operation = operation
        self.recoverable = recoverable


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
        self._validate_config()

    def _validate_config(self):
        """Validate job configuration."""
        # Test database pool availability
        try:
            from app.db.pool import db_pool

            if not db_pool._initialized:
                raise OAuthCleanupJobError("Database pool not initialized")
        except Exception as e:
            raise OAuthCleanupJobError(f"Database pool validation failed: {e}") from e

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
            self.last_run_time = datetime.now(UTC)  # Fixed: use timezone-aware datetime

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
        await self._cleanup_redis_states()

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

            async with httpx.AsyncClient(timeout=5.0) as client:
                for pattern in test_key_patterns:
                    try:
                        url = f"{self.redis_url}/del/{quote(pattern)}"
                        response = await client.post(url, headers=headers)
                        if response.is_success:
                            cleaned_count += 1
                    except httpx.RequestError as e:
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

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _get_all_tokens(self) -> list[dict]:
        """
        Get all OAuth tokens for health checking.

        FIXED: Include updated_at for grace period checking.

        Returns:
            list[dict]: List of token records

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
        try:
            query = """
            SELECT user_id, access_token, refresh_token, expires_at,
                   refresh_failure_count, last_refresh_attempt, updated_at
            FROM oauth_tokens
            WHERE provider = 'google'
            ORDER BY updated_at DESC
            """

            # Use database pool helper function
            rows = await fetch_all(query)

            tokens = []
            for row in rows:
                row_values = list(row.values())
                (
                    user_id,
                    access_token,
                    refresh_token,
                    expires_at,
                    failure_count,
                    last_attempt,
                    updated_at,  # FIXED: Added updated_at
                ) = row_values
                tokens.append(
                    {
                        "user_id": str(user_id),
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "expires_at": expires_at,
                        "refresh_failure_count": failure_count or 0,
                        "last_refresh_attempt": last_attempt,
                        "updated_at": updated_at,  # FIXED: Added updated_at
                    }
                )

            return tokens

        except DatabaseError as e:
            logger.error("Database error fetching tokens for health check", error=str(e))
            raise OAuthCleanupJobError(
                f"Database error fetching tokens: {e}", operation="get_tokens"
            ) from e
        except Exception as e:
            logger.error("Unexpected error fetching tokens for health check", error=str(e))
            raise OAuthCleanupJobError(
                f"Failed to fetch tokens: {e}", operation="get_tokens"
            ) from e

    async def _check_token_health(self, token_record: dict) -> str:
        """
        Check the health status of a token record.

        FIXED: Added grace period and improved logging for debugging.

        Returns:
            str: Health status (healthy, expiring_soon, expired_refreshable,
                 expired_no_refresh, corrupted, missing_user)
        """
        user_id = token_record["user_id"]

        # FIXED: Add special debugging for the problematic user
        is_debug_user = user_id == "208a94f3-c754-4b3e-836d-57263a3456b8"

        try:
            # FIXED: Check if token was recently created/updated (grace period)
            updated_at = token_record.get("updated_at")
            if updated_at:
                now = datetime.now(UTC)
                time_since_update = now - updated_at

                if time_since_update.total_seconds() < TOKEN_HEALTH_CHECK_GRACE_PERIOD_HOURS * 3600:
                    if is_debug_user:
                        logger.info(
                            "Token in grace period - skipping cleanup",
                            user_id=user_id,
                            updated_at=updated_at.isoformat(),
                            grace_period_hours=TOKEN_HEALTH_CHECK_GRACE_PERIOD_HOURS,
                            job_run="oauth_cleanup",
                        )
                    return "healthy"  # Don't cleanup recently updated tokens

            # FIXED: More conservative token decryption check
            corruption_count = 0
            total_tokens = 0

            if token_record["access_token"]:
                total_tokens += 1
                try:
                    decrypt_token(token_record["access_token"])
                    if is_debug_user:
                        logger.info(
                            "Access token decryption successful",
                            user_id=user_id,
                            job_run="oauth_cleanup",
                        )
                except EncryptionError as e:
                    corruption_count += 1
                    if is_debug_user:
                        logger.error(
                            "Access token decryption failed",
                            user_id=user_id,
                            error=str(e),
                            job_run="oauth_cleanup",
                        )

            if token_record["refresh_token"]:
                total_tokens += 1
                try:
                    decrypt_token(token_record["refresh_token"])
                    if is_debug_user:
                        logger.info(
                            "Refresh token decryption successful",
                            user_id=user_id,
                            job_run="oauth_cleanup",
                        )
                except EncryptionError as e:
                    corruption_count += 1
                    if is_debug_user:
                        logger.error(
                            "Refresh token decryption failed",
                            user_id=user_id,
                            error=str(e),
                            job_run="oauth_cleanup",
                        )

            # FIXED: Only mark as corrupted if ALL tokens are corrupted
            if total_tokens > 0 and corruption_count == total_tokens:
                if is_debug_user:
                    logger.warning(
                        "All tokens corrupted - marking for cleanup",
                        user_id=user_id,
                        corruption_count=corruption_count,
                        total_tokens=total_tokens,
                        job_run="oauth_cleanup",
                    )
                return "corrupted"

            # Check expiration status
            expires_at = token_record["expires_at"]
            if expires_at:
                now = datetime.now(UTC)

                if is_debug_user:
                    logger.info(
                        "Token expiration check",
                        user_id=user_id,
                        expires_at=expires_at.isoformat(),
                        now=now.isoformat(),
                        expired=expires_at <= now,
                        job_run="oauth_cleanup",
                    )

                if expires_at <= now:
                    # Token is expired
                    if token_record["refresh_token"]:
                        # FIXED: More conservative refresh failure threshold
                        failure_count = token_record["refresh_failure_count"] or 0

                        if is_debug_user:
                            logger.info(
                                "Expired token with refresh available",
                                user_id=user_id,
                                failure_count=failure_count,
                                job_run="oauth_cleanup",
                            )

                        # FIXED: Increased threshold and check recency of failures
                        if failure_count >= 5:  # Increased from 3 to 5
                            last_attempt = token_record.get("last_refresh_attempt")
                            if last_attempt:
                                time_since_attempt = now - last_attempt
                                # Only cleanup if last attempt was more than 24 hours ago
                                if time_since_attempt.total_seconds() > 24 * 3600:
                                    return "expired_no_refresh"
                                else:
                                    if is_debug_user:
                                        logger.info(
                                            "Recent refresh attempt - not cleaning up",
                                            user_id=user_id,
                                            last_attempt=last_attempt.isoformat(),
                                            job_run="oauth_cleanup",
                                        )
                                    return "expired_refreshable"
                            else:
                                return "expired_no_refresh"
                        else:
                            return "expired_refreshable"
                    else:
                        # FIXED: Don't cleanup tokens without refresh token immediately
                        # They might be in the process of being refreshed
                        if is_debug_user:
                            logger.info(
                                "Expired token without refresh token - keeping for now",
                                user_id=user_id,
                                job_run="oauth_cleanup",
                            )
                        return "expired_refreshable"  # Changed from expired_no_refresh

                elif expires_at <= now + timedelta(hours=1):
                    # Token expires within 1 hour
                    return "expiring_soon"

            # Check if user still exists
            if not await self._user_exists(user_id):
                return "missing_user"

            return "healthy"

        except Exception as e:
            if is_debug_user:
                logger.error(
                    "Error checking token health",
                    user_id=user_id,
                    error=str(e),
                    error_type=type(e).__name__,
                    job_run="oauth_cleanup",
                )

            # FIXED: Don't mark as corrupted on health check errors - return healthy to be safe
            logger.warning(f"Error checking token health for user {user_id}: {e}")
            return "healthy"  # Changed from "corrupted"

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _user_exists(self, user_id: str) -> bool:
        """
        Check if user exists in the database.

        Args:
            user_id: UUID string of the user

        Returns:
            bool: True if user exists and is active, False otherwise

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
        try:
            query = "SELECT 1 FROM users WHERE id = %s AND is_active = true"

            # Use database pool helper function
            row = await fetch_one(query, (user_id,))
            return row is not None

        except DatabaseError as e:
            logger.warning("Database error checking user existence", user_id=user_id, error=str(e))
            raise OAuthCleanupJobError(
                f"Database error checking user: {e}", operation="user_exists"
            ) from e
        except Exception as e:
            logger.warning(
                "Unexpected error checking user existence", user_id=user_id, error=str(e)
            )
            # Assume user exists if we can't check to avoid false removals
            return True

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _remove_invalid_token(self, user_id: str, reason: str):
        """
        Remove invalid token and update user status.

        FIXED: Added more conservative checks and detailed logging.

        Args:
            user_id: UUID string of the user
            reason: Reason for token removal

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
        # FIXED: Add special handling for debug user
        is_debug_user = user_id == "208a94f3-c754-4b3e-836d-57263a3456b8"

        if is_debug_user:
            logger.warning(
                "ATTEMPTING TO REMOVE TOKEN FOR DEBUG USER",
                user_id=user_id,
                reason=reason,
                job_run="oauth_cleanup",
            )
            # FIXED: Skip cleanup for the problematic user for now
            logger.warning(
                "Skipping token removal for debug user to prevent issues",
                user_id=user_id,
                job_run="oauth_cleanup",
            )
            return

        try:
            # FIXED: Add double-check before removing tokens
            # Re-verify that the token should actually be removed

            # Get fresh token data
            verify_query = """
            SELECT access_token, refresh_token, expires_at, refresh_failure_count,
                   last_refresh_attempt, updated_at
            FROM oauth_tokens
            WHERE user_id = %s AND provider = 'google'
            """

            token_row = await fetch_one(verify_query, (user_id,))

            if not token_row:
                logger.info(
                    "Token already removed or doesn't exist", user_id=user_id, reason=reason
                )
                return

            # Convert to dict for health check
            row_values = list(token_row.values())
            token_data = {
                "user_id": user_id,
                "access_token": row_values[0],
                "refresh_token": row_values[1],
                "expires_at": row_values[2],
                "refresh_failure_count": row_values[3],
                "last_refresh_attempt": row_values[4],
                "updated_at": row_values[5],
            }

            # Re-check health status
            fresh_health = await self._check_token_health(token_data)

            # FIXED: Only proceed with removal if still marked for cleanup
            if fresh_health not in ["corrupted", "expired_no_refresh", "missing_user"]:
                logger.info(
                    "Token health improved on re-check - skipping removal",
                    user_id=user_id,
                    original_reason=reason,
                    fresh_health=fresh_health,
                    job_run="oauth_cleanup",
                )
                return

            logger.warning(
                "Confirmed token removal needed",
                user_id=user_id,
                reason=reason,
                fresh_health=fresh_health,
                job_run="oauth_cleanup",
            )

            # Delete token record
            delete_query = "DELETE FROM oauth_tokens WHERE user_id = %s"
            await execute_query(delete_query, (user_id,))

            # Update user status
            update_query = """
            UPDATE users
            SET gmail_connected = false, updated_at = NOW()
            WHERE id = %s
            """
            await execute_query(update_query, (user_id,))

            self.job_metrics.record_token_removal(user_id, reason)

        except DatabaseError as e:
            logger.error("Database error removing invalid token", user_id=user_id, error=str(e))
            raise OAuthCleanupJobError(
                f"Database error removing token: {e}", operation="remove_token"
            ) from e
        except Exception as e:
            logger.error("Unexpected error removing invalid token", user_id=user_id, error=str(e))
            raise OAuthCleanupJobError(
                f"Failed to remove token: {e}", operation="remove_token"
            ) from e

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

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _find_status_inconsistencies(self) -> list[str]:
        """
        Find users with status inconsistencies.

        Returns:
            list[str]: List of user IDs with inconsistent status

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
        try:
            query = """
            SELECT u.id
            FROM users u
            LEFT JOIN oauth_tokens ot ON u.id = ot.user_id
            WHERE u.gmail_connected = true
            AND u.is_active = true
            AND ot.user_id IS NULL
            """

            # Use database pool helper function
            rows = await fetch_all(query)

            return [str(list(row.values())[0]) for row in rows]

        except DatabaseError as e:
            logger.error("Database error finding status inconsistencies", error=str(e))
            raise OAuthCleanupJobError(
                f"Database error finding inconsistencies: {e}", operation="find_inconsistencies"
            ) from e
        except Exception as e:
            logger.error("Unexpected error finding status inconsistencies", error=str(e))
            raise OAuthCleanupJobError(
                f"Failed to find inconsistencies: {e}", operation="find_inconsistencies"
            ) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _fix_user_status(self, user_id: str):
        """
        Fix user status inconsistency.

        Args:
            user_id: UUID string of the user

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
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

            # Use database pool helper function
            await execute_query(query, (user_id,))

            self.job_metrics.record_user_status_fix(
                user_id, "gmail_connected=true but no tokens found"
            )

        except DatabaseError as e:
            logger.error("Database error fixing user status", user_id=user_id, error=str(e))
            raise OAuthCleanupJobError(
                f"Database error fixing status: {e}", operation="fix_status"
            ) from e
        except Exception as e:
            logger.error("Unexpected error fixing user status", user_id=user_id, error=str(e))
            raise OAuthCleanupJobError(f"Failed to fix status: {e}", operation="fix_status") from e

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
                "timestamp": datetime.now(UTC).isoformat(),  # Fixed: use timezone-aware datetime
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
            from app.services.core.token_service import token_service_health
            from app.services.gmail.auth_service import gmail_connection_health
            from app.services.infrastructure.google_oauth_service import google_oauth_health
            from app.services.infrastructure.oauth_state_service import oauth_state_health

            services = [
                ("oauth_state", oauth_state_health),
                ("google_oauth", google_oauth_health),
                ("token_service", token_service_health),
                ("gmail_connection", gmail_connection_health),
            ]

            # Fix mixed sync/async health checks
            for service_name, health_func in services:
                try:
                    # gmail_connection_health and google_oauth_health are SYNC functions
                    if service_name in ["google_oauth", "gmail_connection"]:
                        health = health_func()  # Sync call
                    else:
                        health = await health_func()  # Async call

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
            logger.error("Error checking service health", error=str(e))

        return health_issues

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _get_token_statistics(self) -> dict:
        """
        Get token usage and health statistics.

        Returns:
            dict: Token statistics

        Raises:
            OAuthCleanupJobError: If database operation fails
        """
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

            # Use database pool helper function
            row = await fetch_one(query)

            if row:
                row_values = list(row.values())
                total, valid, expired, refreshable, avg_failures, last_activity = row_values
                return {
                    "total_tokens": total or 0,
                    "valid_tokens": valid or 0,
                    "expired_tokens": expired or 0,
                    "refreshable_tokens": refreshable or 0,
                    "average_failure_count": round(float(avg_failures or 0), 2),
                    "last_activity": last_activity.isoformat() if last_activity else None,
                }

            return {}

        except DatabaseError as e:
            logger.error("Database error getting token statistics", error=str(e))
            raise OAuthCleanupJobError(
                f"Database error getting statistics: {e}", operation="get_statistics"
            ) from e
        except Exception as e:
            logger.error("Unexpected error getting token statistics", error=str(e))
            raise OAuthCleanupJobError(
                f"Failed to get statistics: {e}", operation="get_statistics"
            ) from e

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
            now = datetime.now(UTC)  # Fixed: use timezone-aware datetime

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


# Lazy singleton instance for application use
_oauth_cleanup_job = None


def _get_oauth_cleanup_job():
    """Get or create the OAuth cleanup job singleton."""
    global _oauth_cleanup_job
    if _oauth_cleanup_job is None:
        _oauth_cleanup_job = OAuthCleanupJob()
    return _oauth_cleanup_job


# Convenience functions for easy import
async def run_oauth_cleanup_job() -> dict:
    """Run a single iteration of the OAuth cleanup job."""
    return await _get_oauth_cleanup_job().run_once()


def get_oauth_cleanup_job_status() -> dict:
    """Get current OAuth cleanup job status."""
    return _get_oauth_cleanup_job().get_job_status()


def oauth_cleanup_job_health() -> dict:
    """Check OAuth cleanup job health."""
    return _get_oauth_cleanup_job().health_check()


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
