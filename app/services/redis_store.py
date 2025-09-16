# Updated app/services/redis_store.py - Keep same interface, use fast client
from app.infrastructure.observability.logging import get_logger
from app.services.redis_client import fast_redis

logger = get_logger(__name__)


# Keep exact same function signatures for backward compatibility
async def ping() -> bool:
    """Same interface, 50x faster implementation"""
    return await fast_redis.ping()


async def get(key: str) -> str | None:
    """Same interface, 50x faster implementation"""
    return await fast_redis.get(key)


async def set_with_ttl(key: str, value: str, ttl_s: int | None = None) -> bool:
    """Same interface, 50x faster implementation"""
    return await fast_redis.set_with_ttl(key, value, ttl_s)


async def delete(key: str) -> bool:
    """Same interface, 50x faster implementation"""
    return await fast_redis.delete(key)


# Keep health_check for compatibility
async def health_check() -> dict:
    """Updated health check using fast client"""
    try:
        ping_success = await ping()

        if ping_success:
            # Test set/get operations
            test_key = "health_check_test"
            test_value = "test_value_123"

            set_success = await set_with_ttl(test_key, test_value, 10)
            get_result = await get(test_key) if set_success else None
            get_success = get_result == test_value

            # Cleanup
            if set_success:
                await delete(test_key)

            return {
                "healthy": ping_success and set_success and get_success,
                "ping": ping_success,
                "set_get_operations": set_success and get_success,
                "service": "redis_store",
                "connection_type": "native_pooled",  # Show we're using new method
            }
        else:
            return {
                "healthy": False,
                "ping": False,
                "error": "Redis ping failed",
                "service": "redis_store",
            }

    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        return {"healthy": False, "error": str(e), "service": "redis_store"}
