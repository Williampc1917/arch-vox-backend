# app/services/redis_client.py
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class FastRedisClient:
    """Drop-in replacement for current Redis operations with connection pooling"""

    def __init__(self):
        self.pool = None
        self.client = None
        self._initialized = False

    async def initialize(self):
        """Initialize connection pool on startup"""
        if self._initialized:
            return

        try:
            # For Upstash, we need to use TLS and the correct format
            redis_url = self._build_upstash_redis_url()

            logger.info("Attempting Redis connection", url_preview=redis_url[:30] + "...")

            # Create connection pool with Upstash-specific settings
            self.pool = ConnectionPool.from_url(
                redis_url,
                max_connections=20,
                retry_on_timeout=True,
                retry_on_error=[redis.ConnectionError, redis.TimeoutError],
                socket_connect_timeout=10,  # Increased timeout for Upstash
                socket_timeout=10,
                health_check_interval=30,
                # Upstash-specific settings
                ssl_check_hostname=True,  # Upstash uses TLS
                decode_responses=True,  # Auto-decode strings
            )

            self.client = redis.Redis(connection_pool=self.pool)

            # Test connection
            result = await self.client.ping()
            logger.info("Redis ping successful", result=result)

            self._initialized = True
            logger.info("Fast Redis client initialized successfully", max_connections=20)

        except Exception as e:
            logger.error("Failed to initialize fast Redis client", error=str(e))
            self._initialized = False
            raise RuntimeError("Redis initialization failed") from e

    def _build_upstash_redis_url(self) -> str:
        """Build proper Redis URL for Upstash native protocol"""
        try:
            # Get the base host from REST URL
            rest_url = settings.UPSTASH_REDIS_REST_URL
            token = settings.UPSTASH_REDIS_REST_TOKEN

            # Extract host from REST URL: https://redis-12345.upstash.io
            if rest_url.startswith("https://"):
                host = rest_url.replace("https://", "").strip("/")
            else:
                host = rest_url.replace("http://", "").strip("/")

            # For Upstash native Redis protocol, use port 6379 or 6380
            # Format: rediss://default:password@host:6379
            redis_url = f"rediss://default:{token}@{host}:6379"

            logger.debug("Built Redis URL", host=host, url_length=len(redis_url))
            return redis_url

        except Exception as e:
            logger.error("Failed to build Redis URL", error=str(e))
            raise

    async def close(self):
        """Clean shutdown"""
        try:
            if self.client:
                await self.client.close()
            if self.pool:
                await self.pool.disconnect()
            self._initialized = False
            logger.info("Fast Redis client closed")
        except Exception as e:
            logger.error("Error closing Redis client", error=str(e))

    async def _ensure_initialized(self):
        """Ensure Redis is initialized, fallback if not"""
        if not self._initialized:
            logger.warning("Redis not initialized, attempting to initialize")
            await self.initialize()
            if not self._initialized:
                raise ConnectionError("Redis client not available")

    async def ping(self) -> bool:
        """Test Redis connection"""
        try:
            await self._ensure_initialized()
            result = await self.client.ping()
            return bool(result)
        except Exception as e:
            logger.error("Redis ping failed", error=str(e))
            return False

    async def get(self, key: str) -> str | None:
        """Get value - with fallback handling"""
        try:
            await self._ensure_initialized()
            result = await self.client.get(key)
            return result if result else None  # decode_responses=True handles string conversion
        except Exception as e:
            logger.error("Redis GET failed", key=key[:30], error=str(e))
            return None

    async def set_with_ttl(self, key: str, value: str, ttl_s: int | None = None) -> bool:
        """Set value with TTL - with fallback handling"""
        try:
            await self._ensure_initialized()

            if ttl_s:
                result = await self.client.setex(key, ttl_s, value)
            else:
                result = await self.client.set(key, value)
            return bool(result)
        except Exception as e:
            logger.error("Redis SET failed", key=key[:30], error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        """Delete key - with fallback handling"""
        try:
            await self._ensure_initialized()
            result = await self.client.delete(key)
            return result > 0
        except Exception as e:
            logger.error("Redis DELETE failed", key=key[:30], error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        try:
            await self._ensure_initialized()
            result = await self.client.exists(key)
            return result > 0
        except Exception as e:
            logger.error("Redis EXISTS failed", key=key[:30], error=str(e))
            return False

    async def incr_with_ttl(self, key: str, ttl_s: int | None = None) -> int | None:
        """Increment a key and optionally refresh TTL atomically."""
        try:
            await self._ensure_initialized()
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                if ttl_s:
                    pipe.expire(key, ttl_s)
                results = await pipe.execute()
            return int(results[0]) if results else None
        except Exception as e:
            logger.error("Redis INCR failed", key=key[:30], error=str(e))
            return None

    async def decr(self, key: str, amount: int = 1) -> int | None:
        """Decrement a key and return the new value."""
        try:
            await self._ensure_initialized()
            new_value = await self.client.decr(key, amount)
            return int(new_value)
        except Exception as e:
            logger.error("Redis DECR failed", key=key[:30], error=str(e))
            return None


# Global instance
fast_redis = FastRedisClient()
