"""Redis helpers for email style daily usage counters."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.infrastructure.observability.logging import get_logger
from app.services.redis_client import fast_redis

logger = get_logger(__name__)


def _usage_key(user_id: str) -> str:
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    return f"email_style:usage:{user_id}:{date_str}"


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


async def get_usage_count(user_id: str) -> int | None:
    key = _usage_key(user_id)
    value = await fast_redis.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid usage value in Redis", key=key, value=value)
        return None


async def set_usage_count(user_id: str, count: int) -> None:
    key = _usage_key(user_id)
    ttl = _seconds_until_midnight_utc()
    await fast_redis.set_with_ttl(key, str(count), ttl)


async def increment_usage_count(user_id: str) -> int | None:
    key = _usage_key(user_id)
    ttl = _seconds_until_midnight_utc()
    return await fast_redis.incr_with_ttl(key, ttl)


async def decrement_usage_count(user_id: str, amount: int = 1) -> int | None:
    key = _usage_key(user_id)
    return await fast_redis.decr(key, amount)
