"""
OAuth State Service for secure OAuth flow management.
Handles state parameter generation, storage, and validation for CSRF protection.
"""

import secrets
from urllib.parse import quote

import requests

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

# Constants for OAuth state management
STATE_TTL_SECONDS = 900  # 15 minutes
STATE_KEY_PREFIX = "oauth_state"
STATE_LENGTH = 32  # bytes for cryptographically secure state


class OAuthStateError(Exception):
    """Custom exception for OAuth state-related errors."""

    pass


class OAuthStateService:
    """
    Service for managing OAuth state parameters with Redis backend.

    Provides CSRF protection for OAuth flows by generating, storing, and validating
    cryptographically secure state parameters.
    """

    def __init__(self):
        self.redis_url = settings.UPSTASH_REDIS_REST_URL
        self.redis_token = settings.UPSTASH_REDIS_REST_TOKEN
        self._validate_redis_config()

    def _validate_redis_config(self) -> None:
        """Validate Redis configuration on service initialization."""
        if not self.redis_url or not self.redis_token:
            raise OAuthStateError(
                "Redis configuration missing. Required: UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN"
            )

    def _redis_headers(self) -> dict[str, str]:
        """Get Redis REST API headers."""
        return {"Authorization": f"Bearer {self.redis_token}"}

    def _redis_key(self, state: str) -> str:
        """Generate Redis key for state parameter."""
        return f"{STATE_KEY_PREFIX}:{state}"

    def generate_state(self, user_id: str) -> str:
        """
        Generate cryptographically secure state parameter and store in Redis.

        Args:
            user_id: UUID string of the user initiating OAuth flow

        Returns:
            str: Cryptographically secure state parameter

        Raises:
            OAuthStateError: If state generation or storage fails
        """
        try:
            # Generate cryptographically secure random state
            state = secrets.token_urlsafe(STATE_LENGTH)

            # Store state with user_id in Redis with TTL
            redis_key = self._redis_key(state)
            success = self._store_state(redis_key, user_id)

            if not success:
                raise OAuthStateError("Failed to store state in Redis")

            logger.info(
                "OAuth state generated successfully",
                user_id=user_id,
                state_length=len(state),
                ttl_seconds=STATE_TTL_SECONDS,
            )

            return state

        except Exception as e:
            logger.error(
                "Failed to generate OAuth state",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise OAuthStateError(f"State generation failed: {e}") from e

    def _store_state(self, redis_key: str, user_id: str) -> bool:
        """
        Store state parameter in Redis with TTL.

        Args:
            redis_key: Redis key for the state
            user_id: User ID to associate with state

        Returns:
            bool: True if storage successful, False otherwise
        """
        try:
            url = f"{self.redis_url}/set/{quote(redis_key)}/{quote(user_id)}?EX={STATE_TTL_SECONDS}"
            response = requests.post(url, headers=self._redis_headers(), timeout=5)

            success = response.ok

            if success:
                logger.debug("State stored in Redis", redis_key=redis_key[:20] + "...")
            else:
                logger.warning(
                    "Failed to store state in Redis",
                    redis_key=redis_key[:20] + "...",
                    status_code=response.status_code,
                    response_text=response.text[:100],
                )

            return success

        except requests.exceptions.RequestException as e:
            logger.error(
                "Redis connection error during state storage",
                redis_key=redis_key[:20] + "...",
                error=str(e),
            )
            return False

    def validate_state(self, state: str, expected_user_id: str) -> bool:
        """
        Validate state parameter against stored value and user ID.

        Args:
            state: State parameter from OAuth callback
            expected_user_id: Expected user ID for validation

        Returns:
            bool: True if state is valid and matches user, False otherwise

        Raises:
            OAuthStateError: If validation fails due to system errors
        """
        if not state or not expected_user_id:
            logger.warning(
                "Invalid state validation parameters",
                has_state=bool(state),
                has_user_id=bool(expected_user_id),
            )
            return False

        try:
            redis_key = self._redis_key(state)
            stored_user_id = self._retrieve_state(redis_key)

            if stored_user_id is None:
                logger.warning(
                    "State not found in Redis",
                    state_preview=state[:8] + "...",
                    expected_user_id=expected_user_id,
                )
                return False

            is_valid = stored_user_id == expected_user_id

            if is_valid:
                logger.info(
                    "OAuth state validated successfully",
                    user_id=expected_user_id,
                    state_preview=state[:8] + "...",
                )
                # Clean up state after successful validation
                self._cleanup_state(redis_key)
            else:
                logger.warning(
                    "OAuth state validation failed - user ID mismatch",
                    expected_user_id=expected_user_id,
                    stored_user_id=stored_user_id,
                    state_preview=state[:8] + "...",
                )

            return is_valid

        except Exception as e:
            logger.error(
                "Error during state validation",
                state_preview=state[:8] + "...",
                expected_user_id=expected_user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise OAuthStateError(f"State validation failed: {e}") from e

    def _retrieve_state(self, redis_key: str) -> str | None:
        """
        Retrieve state value from Redis.

        Args:
            redis_key: Redis key for the state

        Returns:
            Optional[str]: User ID if found, None if not found or error
        """
        try:
            url = f"{self.redis_url}/get/{quote(redis_key)}"
            response = requests.post(url, headers=self._redis_headers(), timeout=5)

            if response.ok:
                result = response.json()
                logger.debug("State retrieved from Redis", redis_key=redis_key[:20] + "...")
                return result
            else:
                logger.debug(
                    "State not found in Redis",
                    redis_key=redis_key[:20] + "...",
                    status_code=response.status_code,
                )
                return None

        except requests.exceptions.RequestException as e:
            logger.error(
                "Redis connection error during state retrieval",
                redis_key=redis_key[:20] + "...",
                error=str(e),
            )
            return None
        except Exception as e:
            logger.error(
                "Error parsing Redis response",
                redis_key=redis_key[:20] + "...",
                error=str(e),
            )
            return None

    def _cleanup_state(self, redis_key: str) -> bool:
        """
        Remove state from Redis after successful validation.

        Args:
            redis_key: Redis key to delete

        Returns:
            bool: True if deletion successful, False otherwise
        """
        try:
            url = f"{self.redis_url}/del/{quote(redis_key)}"
            response = requests.post(url, headers=self._redis_headers(), timeout=5)

            success = response.ok

            if success:
                logger.debug("State cleaned up from Redis", redis_key=redis_key[:20] + "...")
            else:
                logger.warning(
                    "Failed to cleanup state from Redis",
                    redis_key=redis_key[:20] + "...",
                    status_code=response.status_code,
                )

            return success

        except requests.exceptions.RequestException as e:
            logger.error(
                "Redis connection error during state cleanup",
                redis_key=redis_key[:20] + "...",
                error=str(e),
            )
            return False

    def cleanup_expired_states(self) -> dict[str, int]:
        """
        Manual cleanup of expired states (backup to Redis TTL).

        Note: This is primarily for monitoring and debugging since Redis TTL
        should handle automatic cleanup.

        Returns:
            dict: Cleanup statistics
        """
        # This would require Redis SCAN operation which is more complex
        # with REST API. For now, rely on TTL for cleanup.
        logger.info("State cleanup relies on Redis TTL auto-expiration")
        return {
            "cleanup_method": "redis_ttl",
            "manual_cleanup_needed": False,
            "ttl_seconds": STATE_TTL_SECONDS,
        }

    def health_check(self) -> dict[str, any]:
        """
        Check OAuth state service health.

        Returns:
            dict: Health status and metrics
        """
        try:
            # Test Redis connectivity with a dummy operation
            test_key = f"{STATE_KEY_PREFIX}:health_check"
            test_value = "health_test"

            # Test set operation
            url = f"{self.redis_url}/set/{quote(test_key)}/{quote(test_value)}?EX=10"
            response = requests.post(url, headers=self._redis_headers(), timeout=3)

            if not response.ok:
                return {
                    "healthy": False,
                    "error": f"Redis set failed: {response.status_code}",
                    "service": "oauth_state",
                }

            # Test get operation
            url = f"{self.redis_url}/get/{quote(test_key)}"
            response = requests.post(url, headers=self._redis_headers(), timeout=3)

            if not response.ok:
                return {
                    "healthy": False,
                    "error": f"Redis get failed: {response.status_code}",
                    "service": "oauth_state",
                }

            # Cleanup test key
            url = f"{self.redis_url}/del/{quote(test_key)}"
            requests.post(url, headers=self._redis_headers(), timeout=3)

            return {
                "healthy": True,
                "service": "oauth_state",
                "redis_connectivity": "ok",
                "state_ttl_seconds": STATE_TTL_SECONDS,
            }

        except Exception as e:
            logger.error("OAuth state service health check failed", error=str(e))
            return {
                "healthy": False,
                "error": str(e),
                "service": "oauth_state",
            }


# Singleton instance for application use
oauth_state_service = OAuthStateService()


# Convenience functions for easy import
def generate_oauth_state(user_id: str) -> str:
    """Generate and store OAuth state parameter."""
    return oauth_state_service.generate_state(user_id)


def validate_oauth_state(state: str, user_id: str) -> bool:
    """Validate OAuth state parameter."""
    return oauth_state_service.validate_state(state, user_id)


def oauth_state_health() -> dict[str, any]:
    """Check OAuth state service health."""
    return oauth_state_service.health_check()
