"""
Production-ready Redis service for Upstash Redis REST API.
Handles Upstash response format consistently across all operations.
"""

from urllib.parse import quote

import requests

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

REQUEST_TIMEOUT = 5  # seconds


def _headers():
    """Get Redis REST API headers."""
    return {"Authorization": f"Bearer {settings.UPSTASH_REDIS_REST_TOKEN}"}


def _handle_response(response: requests.Response, operation: str):
    """
    Handle Upstash Redis response format consistently.

    Upstash returns: {"result": actual_data}
    We extract: actual_data
    """
    if not response.ok:
        logger.warning(f"Redis {operation} failed", status_code=response.status_code)
        return None

    try:
        data = response.json()

        # Handle Upstash response format
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        else:
            return data

    except Exception as e:
        logger.error(f"Failed to parse Redis {operation} response", error=str(e))
        return None


def ping() -> bool:
    """Test Redis connection."""
    try:
        response = requests.post(
            f"{settings.UPSTASH_REDIS_REST_URL}/ping", headers=_headers(), timeout=REQUEST_TIMEOUT
        )

        result = _handle_response(response, "ping")
        return result == "PONG"

    except requests.exceptions.RequestException as e:
        logger.error("Redis ping failed", error=str(e))
        return False


def set_with_ttl(key: str, value: str, ttl_s: int | None = None) -> bool:
    """
    Set key-value pair in Redis with optional TTL.

    Args:
        key: Redis key
        value: Value to store
        ttl_s: Time to live in seconds (optional)

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Properly URL encode both key and value
        encoded_key = quote(key, safe="")
        encoded_value = quote(value, safe="")

        url = f"{settings.UPSTASH_REDIS_REST_URL}/set/{encoded_key}/{encoded_value}"
        if ttl_s:
            url += f"?EX={int(ttl_s)}"

        response = requests.post(url, headers=_headers(), timeout=REQUEST_TIMEOUT)

        result = _handle_response(response, "set")
        success = result == "OK"

        if success:
            logger.debug("Redis SET successful", key=key[:30] + "...")
        else:
            logger.warning("Redis SET failed", key=key[:30] + "...", result=result)

        return success

    except requests.exceptions.RequestException as e:
        logger.error("Redis SET network error", key=key[:30] + "...", error=str(e))
        return False
    except Exception as e:
        logger.error("Redis SET unexpected error", key=key[:30] + "...", error=str(e))
        return False


def get(key: str) -> str | None:
    """
    Get value from Redis by key.

    Args:
        key: Redis key

    Returns:
        str | None: Value if found, None if not found or error
    """
    try:
        encoded_key = quote(key, safe="")
        url = f"{settings.UPSTASH_REDIS_REST_URL}/get/{encoded_key}"

        response = requests.post(url, headers=_headers(), timeout=REQUEST_TIMEOUT)

        result = _handle_response(response, "get")

        if result is not None:
            logger.debug("Redis GET successful", key=key[:30] + "...")
        else:
            logger.debug("Redis GET - key not found", key=key[:30] + "...")

        return result

    except requests.exceptions.RequestException as e:
        logger.error("Redis GET network error", key=key[:30] + "...", error=str(e))
        return None
    except Exception as e:
        logger.error("Redis GET unexpected error", key=key[:30] + "...", error=str(e))
        return None


def delete(key: str) -> bool:
    """
    Delete key from Redis.

    Args:
        key: Redis key to delete

    Returns:
        bool: True if deleted, False otherwise
    """
    try:
        encoded_key = quote(key, safe="")
        url = f"{settings.UPSTASH_REDIS_REST_URL}/del/{encoded_key}"

        response = requests.post(url, headers=_headers(), timeout=REQUEST_TIMEOUT)

        result = _handle_response(response, "delete")
        # DEL returns number of keys deleted (0 or 1)
        success = result == 1

        if success:
            logger.debug("Redis DELETE successful", key=key[:30] + "...")
        else:
            logger.debug("Redis DELETE - key not found", key=key[:30] + "...")

        return success

    except requests.exceptions.RequestException as e:
        logger.error("Redis DELETE network error", key=key[:30] + "...", error=str(e))
        return False
    except Exception as e:
        logger.error("Redis DELETE unexpected error", key=key[:30] + "...", error=str(e))
        return False


def health_check() -> dict:
    """
    Check Redis service health.

    Returns:
        dict: Health status and metrics
    """
    try:
        ping_success = ping()

        if ping_success:
            # Test set/get operations
            test_key = "health_check_test"
            test_value = "test_value_123"

            set_success = set_with_ttl(test_key, test_value, 10)
            get_result = get(test_key) if set_success else None
            get_success = get_result == test_value

            # Cleanup
            if set_success:
                delete(test_key)

            return {
                "healthy": ping_success and set_success and get_success,
                "ping": ping_success,
                "set_get_operations": set_success and get_success,
                "service": "redis_store",
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
