"""
Rate Limiter - Redis-based request rate limiting.

This module provides sliding window rate limiting using Redis for:
- Per-user rate limits (authenticated requests)
- Per-IP rate limits (all requests)
- Anti-abuse protection
- Gmail API compliance

Design:
- Sliding window algorithm (fair and accurate)
- Redis sorted sets for efficient tracking
- Fail-open behavior (if Redis is down, allow requests)
- Automatic cleanup of old entries

Usage:
    from app.middleware.rate_limiter import rate_limiter

    # Check rate limit
    allowed, info = await rate_limiter.check_rate_limit(
        key="user:user-123",
        limit=100,
        window_seconds=60
    )

    if not allowed:
        raise HTTPException(429, detail="Rate limit exceeded")
"""

import time
from typing import Literal

from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.services.redis_client import fast_redis

logger = get_logger(__name__)


class RateLimiter:
    """
    Redis-based rate limiter using sliding window algorithm.

    The sliding window algorithm tracks exact request timestamps,
    providing fair and accurate rate limiting without burst issues.

    Example:
        If limit is 100 req/min and user made 100 requests at 10:00:00,
        they can make more requests starting at 10:01:01 (not 10:01:00).

    Thread Safety:
        Uses atomic Lua scripts to prevent race conditions under high concurrency.
    """

    # Lua script for atomic rate limit check and increment
    # Returns: {allowed (0 or 1), current_count, oldest_timestamp or 0}
    RATE_LIMIT_LUA_SCRIPT = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local window_seconds = tonumber(ARGV[2])
    local current_time = tonumber(ARGV[3])
    local unique_id = ARGV[4]

    -- Remove entries older than the window
    local window_start = current_time - window_seconds
    redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

    -- Count requests in current window
    local current_count = redis.call('ZCARD', key)

    -- Check if limit exceeded
    if current_count >= limit then
        -- Get oldest entry to calculate retry_after
        local oldest_entries = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local oldest_timestamp = 0
        if #oldest_entries > 0 then
            oldest_timestamp = tonumber(oldest_entries[2])
        end
        return {0, current_count, oldest_timestamp}
    end

    -- Add current request with timestamp as score
    redis.call('ZADD', key, current_time, unique_id)

    -- Set expiry on key (cleanup) - 2x window for safety
    redis.call('EXPIRE', key, window_seconds * 2)

    -- Return allowed, new count, 0 (no oldest timestamp needed)
    return {1, current_count + 1, 0}
    """

    def __init__(
        self,
        default_limit: int = 100,
        window_seconds: int = 60,
        fail_open: bool = True,
    ):
        """
        Initialize rate limiter.

        Args:
            default_limit: Default requests per window
            window_seconds: Time window in seconds
            fail_open: If True, allow requests when Redis fails
        """
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self.fail_open = fail_open
        self._lua_script_sha = None  # Cache for Lua script SHA

    async def check_rate_limit(
        self,
        key: str,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> tuple[bool, dict]:
        """
        Check if rate limit is exceeded for given key.

        Uses atomic Lua script with Redis sorted sets for race-condition-free
        sliding window rate limiting.

        Args:
            key: Rate limit key (e.g., "user:123" or "ip:192.168.1.1")
            limit: Request limit for this window (None = use default)
            window_seconds: Time window (None = use default)

        Returns:
            Tuple of (allowed: bool, info: dict)
            - allowed: True if request should be allowed
            - info: Dict with limit details (limit, remaining, retry_after, etc.)

        Example:
            allowed, info = await check_rate_limit("user:123", limit=60)
            if not allowed:
                # info = {
                #     "allowed": False,
                #     "limit": 60,
                #     "remaining": 0,
                #     "retry_after": 15,
                # }
        """
        limit = limit or self.default_limit
        window_seconds = window_seconds or self.window_seconds

        # Redis key with namespace
        redis_key = f"ratelimit:{key}"
        current_time = int(time.time())

        try:
            # Ensure Redis is initialized
            if not fast_redis.client:
                if self.fail_open:
                    logger.warning("Redis not initialized, failing open (allowing request)")
                    return True, self._create_info_dict(
                        allowed=True,
                        limit=limit,
                        remaining=limit,
                        error="redis_not_initialized",
                    )
                else:
                    return False, self._create_info_dict(
                        allowed=False, limit=limit, remaining=0, error="redis_not_initialized"
                    )

            # Generate unique ID for this request
            unique_id = f"{current_time}:{time.time_ns()}"

            # Execute atomic Lua script
            # Returns: [allowed (0 or 1), current_count, oldest_timestamp or 0]
            result = await fast_redis.client.eval(
                self.RATE_LIMIT_LUA_SCRIPT,
                1,  # Number of keys
                redis_key,  # KEYS[1]
                limit,  # ARGV[1]
                window_seconds,  # ARGV[2]
                current_time,  # ARGV[3]
                unique_id,  # ARGV[4]
            )

            allowed = bool(result[0])
            current_count = int(result[1])
            oldest_timestamp = int(result[2]) if result[2] else 0

            if not allowed:
                # Rate limit exceeded
                if oldest_timestamp > 0:
                    retry_after = max(1, (oldest_timestamp + window_seconds) - current_time)
                else:
                    retry_after = window_seconds

                return False, self._create_info_dict(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after=retry_after,
                    window_seconds=window_seconds,
                )

            # Request allowed
            remaining = max(0, limit - current_count)

            return True, self._create_info_dict(
                allowed=True,
                limit=limit,
                remaining=remaining,
                window_seconds=window_seconds,
            )

        except Exception as e:
            # Log error but don't fail the request (if fail_open=True)
            logger.error(
                "Rate limiter Redis error",
                error=str(e),
                error_type=type(e).__name__,
                key=key,
                limit=limit,
            )

            if self.fail_open:
                # Fail open: Allow request when rate limiter fails
                return True, self._create_info_dict(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    error="rate_limiter_error",
                )
            else:
                # Fail closed: Reject request when rate limiter fails
                return False, self._create_info_dict(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    error="rate_limiter_error",
                )

    async def check_user_rate_limit(
        self,
        user_id: str,
        limit: int | None = None,
    ) -> tuple[bool, dict]:
        """
        Check per-user rate limit.

        Args:
            user_id: User ID
            limit: Request limit (None = use configured default)

        Returns:
            Tuple of (allowed, info)
        """
        if limit is None:
            rate_limits = settings.get_rate_limits()
            limit = rate_limits["user_per_minute"]

        return await self.check_rate_limit(
            key=f"user:{user_id}",
            limit=limit,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

    async def check_ip_rate_limit(
        self,
        ip_address: str,
        limit: int | None = None,
    ) -> tuple[bool, dict]:
        """
        Check per-IP rate limit.

        Args:
            ip_address: Client IP address
            limit: Request limit (None = use configured default)

        Returns:
            Tuple of (allowed, info)
        """
        if limit is None:
            rate_limits = settings.get_rate_limits()
            limit = rate_limits["ip_per_minute"]

        return await self.check_rate_limit(
            key=f"ip:{ip_address}",
            limit=limit,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

    def _create_info_dict(
        self,
        allowed: bool,
        limit: int,
        remaining: int,
        retry_after: int | None = None,
        window_seconds: int | None = None,
        error: str | None = None,
    ) -> dict:
        """
        Create standardized rate limit info dict.

        Args:
            allowed: Whether request is allowed
            limit: Rate limit
            remaining: Remaining requests
            retry_after: Seconds until can retry (if rate limited)
            window_seconds: Time window
            error: Error code (if any)

        Returns:
            Dict with rate limit info
        """
        info = {
            "allowed": allowed,
            "limit": limit,
            "remaining": remaining,
            "retry_after": retry_after,
        }

        if window_seconds is not None:
            info["window_seconds"] = window_seconds

        if error:
            info["error"] = error

        return info


# Global singleton
rate_limiter = RateLimiter(
    default_limit=settings.RATE_LIMIT_USER_PER_MINUTE,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    fail_open=settings.RATE_LIMIT_FAIL_OPEN,
)
