# app/services/email_style_rate_limiter.py
"""
Email Style Rate Limiter Service
Handles rate limiting for custom email style extractions using plan-based limits.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import settings
from app.db.helpers import (
    get_user_extraction_limit_status,
    get_user_plan_limits,
    increment_extraction_counter,
)
from app.infrastructure.observability.logging import get_logger
from app.services.email_style.usage_cache import (
    decrement_usage_count,
    get_usage_count,
    increment_usage_count,
    set_usage_count,
)

logger = get_logger(__name__)


class RateLimitExceeded(Exception):
    """Raised when user exceeds daily email extraction limit."""

    def __init__(self, message: str, used: int, limit: int, reset_time: datetime):
        super().__init__(message)
        self.used = used
        self.limit = limit
        self.reset_time = reset_time
        self.is_recoverable = True  # User can try again tomorrow


class EmailStyleRateLimiterError(Exception):
    """Base exception for email style rate limiter errors."""

    def __init__(self, message: str, user_id: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.user_id = user_id
        self.recoverable = recoverable


class EmailStyleRateLimiter:
    """
    Rate limiter for custom email style extractions.

    Uses plan-based limits stored in database to control OpenAI API usage.
    """

    def __init__(self):
        logger.info("Email style rate limiter initialized")

    async def _get_plan_limits(self, user_id: str) -> dict[str, Any]:
        plan_info = await get_user_plan_limits(user_id)
        if not plan_info:
            raise EmailStyleRateLimiterError("User plan not found", user_id=user_id)

        return {
            "plan_name": plan_info["plan_name"],
            "daily_limit": plan_info.get("daily_email_extractions", 0) or 0,
        }

    async def check_extraction_limit(self, user_id: str) -> dict[str, Any]:
        """
        Check if user can perform custom email extraction.

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Rate limit status with remaining attempts

        Raises:
            RateLimitExceeded: If user has exceeded daily limit
            EmailStyleRateLimiterError: If unable to check limits
        """
        try:
            cache_enabled = settings.EMAIL_STYLE_REDIS_CACHE_ENABLED
            cached_usage = await get_usage_count(user_id) if cache_enabled else None

            if cached_usage is not None:
                plan_limits = await self._get_plan_limits(user_id)
                daily_limit = plan_limits["daily_limit"]

                if daily_limit <= 0:
                    raise EmailStyleRateLimiterError(
                        "Daily email extraction limit not configured", user_id=user_id
                    )

                remaining = max(0, daily_limit - cached_usage)

                if remaining <= 0:
                    reset_time = datetime.now(UTC).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                    raise RateLimitExceeded(
                        f"Daily limit exceeded: {cached_usage}/{daily_limit} extractions used",
                        used=cached_usage,
                        limit=daily_limit,
                        reset_time=reset_time,
                    )

                logger.info(
                    "Rate limit check passed (cache)",
                    user_id=user_id,
                    remaining=remaining,
                    used=cached_usage,
                    limit=daily_limit,
                    plan=plan_limits["plan_name"],
                )

                reset_time = datetime.now(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)

                return {
                    "allowed": True,
                    "remaining": remaining,
                    "used_today": cached_usage,
                    "daily_limit": daily_limit,
                    "plan_name": plan_limits["plan_name"],
                    "reset_time": reset_time,
                    "last_extraction_at": None,
                }

            # Fallback to complete rate limit status from database
            status = await get_user_extraction_limit_status(user_id)

            # Check for database errors
            if "error" in status:
                logger.error(
                    "Error checking extraction limit", user_id=user_id, error=status["error"]
                )
                raise EmailStyleRateLimiterError(
                    f"Unable to check rate limit: {status['error']}", user_id=user_id
                )

            # Check if user can extract
            if not status["can_extract"]:
                logger.warning(
                    "Rate limit exceeded",
                    user_id=user_id,
                    used=status["used_today"],
                    limit=status["daily_limit"],
                    plan=status["plan_name"],
                )

                raise RateLimitExceeded(
                    f"Daily limit exceeded: {status['used_today']}/{status['daily_limit']} extractions used",
                    used=status["used_today"],
                    limit=status["daily_limit"],
                    reset_time=status.get("reset_time", datetime.now(UTC) + timedelta(days=1)),
                )

            # Cache usage count for faster subsequent checks
            if cache_enabled:
                await set_usage_count(user_id, status["used_today"])

            # Log successful check
            logger.info(
                "Rate limit check passed",
                user_id=user_id,
                remaining=status["remaining"],
                used=status["used_today"],
                limit=status["daily_limit"],
                plan=status["plan_name"],
            )

            return {
                "allowed": True,
                "remaining": status["remaining"],
                "used_today": status["used_today"],
                "daily_limit": status["daily_limit"],
                "plan_name": status["plan_name"],
                "reset_time": status.get("reset_time"),
                "last_extraction_at": status.get("last_extraction_at"),
            }

        except RateLimitExceeded:
            raise  # Re-raise rate limit exceptions
        except Exception as e:
            logger.error(
                "Unexpected error checking extraction limit",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmailStyleRateLimiterError(
                f"Rate limit check failed: {e}", user_id=user_id
            ) from e

    async def record_extraction_attempt(
        self, user_id: str, success: bool = True, metadata: dict | None = None
    ) -> dict[str, Any]:
        """
        Record an email extraction attempt and increment counter.
        Should be called after EVERY OpenAI API call (success or failure).

        Args:
            user_id: UUID string of the user
            success: Whether the extraction was successful
            metadata: Optional metadata about the extraction

        Returns:
            dict: Updated usage status

        Raises:
            EmailStyleRateLimiterError: If unable to record attempt
        """
        try:
            metadata = metadata or {}
            cache_enabled = settings.EMAIL_STYLE_REDIS_CACHE_ENABLED
            redis_count = None

            if cache_enabled:
                redis_count = await increment_usage_count(user_id)
                if redis_count is None:
                    cache_enabled = False

            # Increment the counter in database
            increment_success = await increment_extraction_counter(user_id)

            if not increment_success:
                if cache_enabled and redis_count is not None:
                    await decrement_usage_count(user_id)
                logger.error(
                    "Failed to increment extraction counter",
                    user_id=user_id,
                    success=success,
                    metadata=metadata,
                )
                raise EmailStyleRateLimiterError(
                    "Failed to record extraction attempt", user_id=user_id
                )

            # Log the attempt for monitoring
            logger.info(
                "Email extraction attempt recorded",
                user_id=user_id,
                success=success,
                metadata=metadata,
                timestamp=datetime.now(UTC).isoformat(),
            )

            if cache_enabled and redis_count is not None:
                plan_limits = await self._get_plan_limits(user_id)
                daily_limit = plan_limits["daily_limit"]
                remaining = max(0, daily_limit - redis_count)
                return {
                    "recorded": True,
                    "success": success,
                    "updated_usage": {
                        "used_today": redis_count,
                        "remaining": remaining,
                        "daily_limit": daily_limit,
                    },
                }

            # Fallback to database status
            updated_status = await get_user_extraction_limit_status(user_id)

            return {
                "recorded": True,
                "success": success,
                "updated_usage": {
                    "used_today": updated_status.get("used_today", 0),
                    "remaining": updated_status.get("remaining", 0),
                    "daily_limit": updated_status.get("daily_limit", 0),
                },
            }

        except Exception as e:
            logger.error(
                "Unexpected error recording extraction attempt",
                user_id=user_id,
                success=success,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmailStyleRateLimiterError(
                f"Failed to record extraction attempt: {e}", user_id=user_id
            ) from e

    async def get_rate_limit_status(self, user_id: str) -> dict[str, Any]:
        """
        Get current rate limit status without checking limits.
        Useful for displaying remaining attempts to users.

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Current rate limit status
        """
        try:
            cache_enabled = settings.EMAIL_STYLE_REDIS_CACHE_ENABLED
            cached_usage = await get_usage_count(user_id) if cache_enabled else None

            now = datetime.now(UTC)
            reset_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            hours_until_reset = (reset_time - now).total_seconds() / 3600

            if cached_usage is not None:
                plan_limits = await self._get_plan_limits(user_id)
                daily_limit = plan_limits["daily_limit"]
                remaining = max(0, daily_limit - cached_usage)
                can_extract = remaining > 0

                return {
                    "available": True,
                    "can_extract": can_extract,
                    "daily_limit": daily_limit,
                    "used_today": cached_usage,
                    "remaining": remaining,
                    "plan_name": plan_limits["plan_name"],
                    "reset_time": reset_time.isoformat(),
                    "hours_until_reset": round(hours_until_reset, 1),
                    "last_extraction_at": None,
                }

            status = await get_user_extraction_limit_status(user_id)

            if "error" in status:
                logger.warning(
                    "Error getting rate limit status", user_id=user_id, error=status["error"]
                )
                return {"available": False, "error": status["error"]}

            if cache_enabled:
                await set_usage_count(user_id, status["used_today"])

            return {
                "available": True,
                "can_extract": status["can_extract"],
                "daily_limit": status["daily_limit"],
                "used_today": status["used_today"],
                "remaining": status["remaining"],
                "plan_name": status["plan_name"],
                "reset_time": reset_time.isoformat(),
                "hours_until_reset": round(hours_until_reset, 1),
                "last_extraction_at": status.get("last_extraction_at"),
            }

        except Exception as e:
            logger.error(
                "Unexpected error getting rate limit status",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {"available": False, "error": f"Failed to get rate limit status: {e}"}

    def get_rate_limit_error_message(self, used: int, limit: int, reset_time: datetime) -> str:
        """
        Generate user-friendly rate limit error message.

        Args:
            used: Number of extractions used today
            limit: Daily limit
            reset_time: When limits reset

        Returns:
            str: User-friendly error message
        """
        hours_until_reset = (reset_time - datetime.now(UTC)).total_seconds() / 3600

        if hours_until_reset < 1:
            time_msg = "in less than an hour"
        elif hours_until_reset < 24:
            time_msg = f"in {int(hours_until_reset)} hours"
        else:
            time_msg = "tomorrow"

        return (
            f"You've used all {limit} custom email extractions for today ({used}/{limit}). "
            f"Your limit will reset {time_msg}. You can still select Casual or Professional styles anytime!"
        )

    async def health_check(self) -> dict[str, Any]:
        """
        Health check for rate limiter service.

        Returns:
            dict: Health status
        """
        try:
            # Test database connectivity with a simple query
            from app.db.helpers import fetch_one

            test_query = "SELECT COUNT(*) FROM plans WHERE daily_email_extractions > 0"
            result = await fetch_one(test_query)

            if result:
                plan_count = list(result.values())[0]
                return {
                    "healthy": True,
                    "service": "email_style_rate_limiter",
                    "database_connectivity": "ok",
                    "plans_with_limits": plan_count,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            else:
                return {
                    "healthy": False,
                    "service": "email_style_rate_limiter",
                    "database_connectivity": "error",
                    "error": "No result from test query",
                }

        except Exception as e:
            logger.error("Rate limiter health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "email_style_rate_limiter",
                "database_connectivity": "error",
                "error": str(e),
            }


# Singleton instance for application use
email_style_rate_limiter = EmailStyleRateLimiter()


# Convenience functions for easy import
async def check_email_extraction_limit(user_id: str) -> dict[str, Any]:
    """Check if user can perform email extraction."""
    return await email_style_rate_limiter.check_extraction_limit(user_id)


async def record_email_extraction_attempt(
    user_id: str, success: bool = True, metadata: dict | None = None
) -> dict[str, Any]:
    """Record email extraction attempt and increment counter."""
    return await email_style_rate_limiter.record_extraction_attempt(user_id, success, metadata)


async def get_email_extraction_status(user_id: str) -> dict[str, Any]:
    """Get current email extraction rate limit status."""
    return await email_style_rate_limiter.get_rate_limit_status(user_id)


def get_rate_limit_error_message(used: int, limit: int, reset_time: datetime) -> str:
    """Generate user-friendly rate limit error message."""
    return email_style_rate_limiter.get_rate_limit_error_message(used, limit, reset_time)


async def email_style_rate_limiter_health() -> dict[str, Any]:
    """Check email style rate limiter health."""
    return await email_style_rate_limiter.health_check()
