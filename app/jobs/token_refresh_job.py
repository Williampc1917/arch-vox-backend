"""
Token Refresh Job for proactive OAuth token management.
Runs as a background job to refresh tokens before they expire.
REFACTORED: Now follows the same patterns as other services for consistency.
"""

import asyncio
import time
from datetime import datetime, timedelta

from app.infrastructure.observability.logging import get_logger
from app.services.gmail_connection_service import GmailConnectionError, gmail_connection_service
from app.services.token_service import TokenServiceError, token_service

logger = get_logger(__name__)

# Job configuration
JOB_INTERVAL_MINUTES = 10  # Run every 10 minutes
TOKEN_REFRESH_BUFFER_MINUTES = 15  # Refresh tokens expiring within 15 minutes
BATCH_SIZE = 50  # Process users in batches
MAX_CONCURRENT_REFRESHES = 10  # Limit concurrent refresh operations
REFRESH_TIMEOUT_SECONDS = 30  # Timeout for individual refresh operations


class TokenRefreshJobError(Exception):
    """Custom exception for token refresh job operations."""

    def __init__(self, message: str, operation: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.operation = operation
        self.recoverable = recoverable


class TokenRefreshMetrics:
    """Metrics tracking for token refresh operations."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all metrics for new job run."""
        self.start_time = datetime.utcnow()
        self.users_processed = 0
        self.tokens_refreshed = 0
        self.refresh_failures = 0
        self.users_disconnected = 0
        self.processing_errors = 0
        self.total_duration_seconds = 0
        self.errors: list[dict] = []

    def record_success(self, user_id: str, duration_ms: float):
        """Record successful token refresh."""
        self.users_processed += 1
        self.tokens_refreshed += 1

        logger.debug(
            "Token refresh successful",
            user_id=user_id,
            duration_ms=duration_ms,
            job_run="token_refresh",
        )

    def record_failure(self, user_id: str, error: str, disconnected: bool = False):
        """Record failed token refresh."""
        self.users_processed += 1
        self.refresh_failures += 1

        if disconnected:
            self.users_disconnected += 1

        error_record = {
            "user_id": user_id,
            "error": error,
            "disconnected": disconnected,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.errors.append(error_record)

        logger.warning(
            "Token refresh failed",
            user_id=user_id,
            error=error,
            disconnected=disconnected,
            job_run="token_refresh",
        )

    def record_processing_error(self, user_id: str, error: str):
        """Record processing error (non-token related)."""
        self.users_processed += 1
        self.processing_errors += 1

        error_record = {
            "user_id": user_id,
            "error": error,
            "error_type": "processing",
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.errors.append(error_record)

        logger.error(
            "Token refresh processing error", user_id=user_id, error=error, job_run="token_refresh"
        )

    def finalize(self):
        """Finalize metrics and calculate totals."""
        self.total_duration_seconds = (datetime.utcnow() - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        """Convert metrics to dictionary for logging."""
        return {
            "job_run": "token_refresh",
            "start_time": self.start_time.isoformat(),
            "total_duration_seconds": round(self.total_duration_seconds, 2),
            "users_processed": self.users_processed,
            "tokens_refreshed": self.tokens_refreshed,
            "refresh_failures": self.refresh_failures,
            "users_disconnected": self.users_disconnected,
            "processing_errors": self.processing_errors,
            "success_rate_percent": round(
                (
                    (self.tokens_refreshed / self.users_processed * 100)
                    if self.users_processed > 0
                    else 0
                ),
                2,
            ),
            "errors_count": len(self.errors),
        }


class TokenRefreshJob:
    """
    Background job for proactive token refresh management.

    Runs periodically to refresh OAuth tokens before they expire,
    preventing user-facing delays and improving user experience.
    """

    def __init__(self):
        self.is_running = False
        self.last_run_time: datetime | None = None
        self.job_metrics = TokenRefreshMetrics()
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate job configuration."""
        # Test database pool availability
        try:
            from app.db.pool import db_pool
            if not db_pool._initialized:
                raise TokenRefreshJobError("Database pool not initialized")
        except Exception as e:
            raise TokenRefreshJobError(f"Database pool validation failed: {e}") from e

        # Validate job configuration parameters
        if JOB_INTERVAL_MINUTES < 5:
            logger.warning(
                "Token refresh job interval is very short", interval_minutes=JOB_INTERVAL_MINUTES
            )

        if TOKEN_REFRESH_BUFFER_MINUTES < 10:
            logger.warning(
                "Token refresh buffer is very short", buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES
            )

        logger.info(
            "Token refresh job configured",
            interval_minutes=JOB_INTERVAL_MINUTES,
            buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES,
            batch_size=BATCH_SIZE,
            max_concurrent=MAX_CONCURRENT_REFRESHES,
        )

    async def run_once(self) -> dict:
        """
        Run a single iteration of the token refresh job.

        Returns:
            Dict: Job execution metrics and results

        Raises:
            TokenRefreshJobError: If job execution fails due to system errors
        """
        if self.is_running:
            logger.warning("Token refresh job already running, skipping this iteration")
            return {"skipped": True, "reason": "already_running"}

        try:
            self.is_running = True
            self.job_metrics.reset()

            logger.info(
                "Starting token refresh job",
                buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES,
                batch_size=BATCH_SIZE,
            )

            # Get users with tokens expiring soon
            expiring_users = await self._get_expiring_users()

            if not expiring_users:
                logger.info("No tokens found requiring refresh")
                self.job_metrics.finalize()
                return self.job_metrics.to_dict()

            logger.info(
                "Found users with expiring tokens",
                user_count=len(expiring_users),
                buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES,
            )

            # Process users in batches with concurrency control
            await self._process_users_in_batches(expiring_users)

            # Finalize and log metrics
            self.job_metrics.finalize()
            self.last_run_time = datetime.utcnow()

            metrics = self.job_metrics.to_dict()

            logger.info("Token refresh job completed", **metrics)

            return metrics

        except TokenRefreshJobError:
            raise  # Re-raise job-specific errors
        except Exception as e:
            logger.error("Token refresh job failed", error=str(e), error_type=type(e).__name__)
            self.job_metrics.finalize()
            metrics = self.job_metrics.to_dict()
            metrics["job_error"] = str(e)
            raise TokenRefreshJobError(f"Token refresh job failed: {e}", operation="run_once") from e

        finally:
            self.is_running = False

    async def _get_expiring_users(self) -> list[str]:
        """
        Get users with tokens expiring soon.
        
        Returns:
            list[str]: List of user IDs with expiring tokens
            
        Raises:
            TokenRefreshJobError: If unable to fetch expiring users
        """
        try:
            # Use the token service to get users with expiring tokens
            expiring_users = await token_service.get_tokens_expiring_soon(
                provider="google", buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES
            )
            
            return expiring_users

        except TokenServiceError as e:
            logger.error("Token service error getting expiring users", error=str(e))
            raise TokenRefreshJobError(f"Failed to get expiring users: {e}", operation="get_expiring_users") from e
        except Exception as e:
            logger.error("Unexpected error getting expiring users", error=str(e))
            raise TokenRefreshJobError(f"Unexpected error getting expiring users: {e}", operation="get_expiring_users") from e

    async def _process_users_in_batches(self, user_ids: list[str]):
        """
        Process users in batches with concurrency control.
        
        Args:
            user_ids: List of user IDs to process
            
        Raises:
            TokenRefreshJobError: If batch processing fails
        """
        try:
            # Split users into batches
            batches = [user_ids[i : i + BATCH_SIZE] for i in range(0, len(user_ids), BATCH_SIZE)]

            logger.info(
                "Processing users in batches",
                total_users=len(user_ids),
                batch_count=len(batches),
                batch_size=BATCH_SIZE,
            )

            for batch_num, batch_users in enumerate(batches, 1):
                logger.debug(
                    "Processing batch",
                    batch_number=batch_num,
                    batch_size=len(batch_users),
                    total_batches=len(batches),
                )

                # Create semaphore to limit concurrent operations
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_REFRESHES)

                # Process batch with concurrency limit
                tasks = [
                    self._refresh_user_token_with_semaphore(semaphore, user_id)
                    for user_id in batch_users
                ]

                # Wait for all tasks in batch to complete
                await asyncio.gather(*tasks, return_exceptions=True)

                # Small delay between batches to avoid overwhelming services
                if batch_num < len(batches):
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error("Error processing users in batches", error=str(e))
            raise TokenRefreshJobError(f"Batch processing failed: {e}", operation="process_batches") from e

    async def _refresh_user_token_with_semaphore(self, semaphore: asyncio.Semaphore, user_id: str):
        """
        Refresh token for a single user with concurrency control.
        
        Args:
            semaphore: Semaphore to control concurrency
            user_id: UUID string of the user
        """
        async with semaphore:
            await self._refresh_user_token(user_id)

    async def _refresh_user_token(self, user_id: str):
        """
        Refresh token for a single user.

        Args:
            user_id: UUID string of the user
        """
        start_time = time.time()

        try:
            # Use asyncio timeout to prevent hanging
            refresh_task = asyncio.create_task(self._perform_token_refresh(user_id))

            await asyncio.wait_for(refresh_task, timeout=REFRESH_TIMEOUT_SECONDS)

            duration_ms = (time.time() - start_time) * 1000
            self.job_metrics.record_success(user_id, duration_ms)

        except TimeoutError:
            error_msg = f"Token refresh timed out after {REFRESH_TIMEOUT_SECONDS}s"
            self.job_metrics.record_processing_error(user_id, error_msg)

        except TokenServiceError as e:
            # Handle token service specific errors
            disconnected = not getattr(e, "recoverable", True)
            if disconnected:
                # Disconnect user if error is not recoverable
                try:
                    await gmail_connection_service.disconnect_gmail(user_id)
                except Exception as disconnect_error:
                    logger.error(
                        "Failed to disconnect user after token refresh failure",
                        user_id=user_id,
                        error=str(disconnect_error),
                    )

            self.job_metrics.record_failure(user_id, str(e), disconnected)

        except GmailConnectionError as e:
            # Handle gmail connection specific errors
            self.job_metrics.record_failure(user_id, str(e), False)

        except Exception as e:
            # Handle unexpected errors
            error_msg = f"Unexpected error: {type(e).__name__}: {e}"
            self.job_metrics.record_processing_error(user_id, error_msg)

    async def _perform_token_refresh(self, user_id: str):
        """
        Perform the actual token refresh operation.

        Args:
            user_id: UUID string of the user
            
        Raises:
            TokenServiceError: If refresh fails
            
        Note:
            This method is separated to make it easier to add timeout handling.
        """
        try:
            # Use the gmail connection service for high-level refresh
            # This handles user status updates and proper error handling
            success = await gmail_connection_service.refresh_connection(user_id)

            if not success:
                raise TokenServiceError(
                    "Token refresh failed through gmail connection service",
                    user_id=user_id,
                    recoverable=False,
                )

        except GmailConnectionError as e:
            # Re-raise as TokenServiceError for consistent error handling
            raise TokenServiceError(
                f"Gmail connection error during refresh: {e}",
                user_id=user_id,
                recoverable=getattr(e, "recoverable", True),
            ) from e

    def get_job_status(self) -> dict:
        """
        Get current job status and metrics.

        Returns:
            Dict: Current job status information
        """
        return {
            "job_name": "token_refresh",
            "is_running": self.is_running,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "interval_minutes": JOB_INTERVAL_MINUTES,
            "buffer_minutes": TOKEN_REFRESH_BUFFER_MINUTES,
            "batch_size": BATCH_SIZE,
            "max_concurrent": MAX_CONCURRENT_REFRESHES,
            "last_run_metrics": self.job_metrics.to_dict() if self.last_run_time else None,
        }

    def health_check(self) -> dict:
        """
        Health check for the token refresh job.

        Returns:
            Dict: Health status and configuration
        """
        try:
            now = datetime.utcnow()

            # Check if job is overdue (hasn't run in 2x the interval)
            overdue_threshold = timedelta(minutes=JOB_INTERVAL_MINUTES * 2)
            is_overdue = (
                self.last_run_time is not None and (now - self.last_run_time) > overdue_threshold
            )

            # Check recent success rate
            recent_success_rate = None
            if hasattr(self.job_metrics, "success_rate_percent"):
                recent_success_rate = self.job_metrics.success_rate_percent

            health_status = {
                "healthy": not is_overdue,
                "service": "token_refresh_job",
                "is_running": self.is_running,
                "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
                "is_overdue": is_overdue,
                "recent_success_rate": recent_success_rate,
                "configuration": {
                    "interval_minutes": JOB_INTERVAL_MINUTES,
                    "buffer_minutes": TOKEN_REFRESH_BUFFER_MINUTES,
                    "batch_size": BATCH_SIZE,
                    "max_concurrent": MAX_CONCURRENT_REFRESHES,
                },
            }

            if is_overdue:
                health_status["warning"] = (
                    f"Job overdue by {(now - self.last_run_time).total_seconds() / 60:.1f} minutes"
                )

            return health_status

        except Exception as e:
            logger.error("Token refresh job health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "token_refresh_job",
                "error": str(e),
            }


# Singleton instance for application use
token_refresh_job = TokenRefreshJob()


# Convenience functions for easy import and background job scheduling
async def run_token_refresh_job() -> dict:
    """Run a single iteration of the token refresh job."""
    return await token_refresh_job.run_once()


def get_token_refresh_job_status() -> dict:
    """Get current token refresh job status."""
    return token_refresh_job.get_job_status()


def token_refresh_job_health() -> dict:
    """Check token refresh job health."""
    return token_refresh_job.health_check()


# Background job scheduler (for use with task queue or cron)
async def start_token_refresh_scheduler():
    """
    Start the token refresh job scheduler.

    This function can be used with a task queue like Celery or
    run in a separate process/container for background processing.
    """
    logger.info("Starting token refresh job scheduler", interval_minutes=JOB_INTERVAL_MINUTES)

    while True:
        try:
            # Run the job
            metrics = await run_token_refresh_job()

            # Log summary metrics
            if not metrics.get("skipped", False):
                logger.info(
                    "Token refresh job cycle completed",
                    **{k: v for k, v in metrics.items() if k != "errors"},
                )

            # Wait for next interval
            await asyncio.sleep(JOB_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            logger.info("Token refresh job scheduler stopped by user")
            break
        except Exception as e:
            logger.error(
                "Error in token refresh job scheduler", error=str(e), error_type=type(e).__name__
            )
            # Wait a bit before retrying to avoid tight error loops
            await asyncio.sleep(60)


# Example usage for standalone execution
if __name__ == "__main__":
    import asyncio

    async def main():
        """Example of running the token refresh job."""
        print("Running token refresh job...")

        # Run once
        metrics = await run_token_refresh_job()
        print(f"Job completed: {metrics}")

        # Check status
        status = get_token_refresh_job_status()
        print(f"Job status: {status}")

        # Check health
        health = token_refresh_job_health()
        print(f"Job health: {health}")

    asyncio.run(main())
