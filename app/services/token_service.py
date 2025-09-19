"""
Token Service for OAuth token lifecycle management.
Handles encryption, storage, expiration, refresh, and cleanup of OAuth tokens.
REFACTORED: Now uses database connection pool instead of direct psycopg connections.
"""

from datetime import UTC, datetime, timedelta
import asyncio

from app.db.helpers import DatabaseError, execute_query, fetch_all, fetch_one, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.models.domain.oauth_domain import OAuthToken
from app.services.encryption_service import (
    EncryptionError,
    decrypt_oauth_tokens,
    encrypt_oauth_tokens,
)
from app.services.google_oauth_service import (
    GoogleOAuthError,
    TokenResponse,
    refresh_google_token,
    revoke_google_token,
)

logger = get_logger(__name__)

async def retry_with_backoff(func, *args, retries=3, base_delay=1, **kwargs):
    """
    Retry a blocking function with exponential backoff.
    Uses asyncio.to_thread to avoid blocking the event loop.
    """
    import random
    for attempt in range(retries):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as e:
            if attempt == retries - 1:
                raise  # Give up after last attempt
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.3)
            logger.warning(
                "Retrying after failure",
                func=func.__name__,
                attempt=attempt + 1,
                delay=delay,
                error=str(e),
            )
            await asyncio.sleep(delay)

# Token management constants
TOKEN_REFRESH_BUFFER_MINUTES = 15  # Refresh tokens expiring within 15 minutes
REFRESH_FAILURE_THRESHOLD = 3  # Max consecutive refresh failures before requiring re-auth
TOKEN_CLEANUP_BATCH_SIZE = 50  # Batch size for token cleanup operations


class TokenServiceError(Exception):
    """Custom exception for token service operations."""

    def __init__(self, message: str, user_id: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.user_id = user_id
        self.recoverable = recoverable


class TokenService:
    """
    Service for managing OAuth token lifecycle with encryption and persistence.

    Handles storage, retrieval, expiration checking, refresh, and cleanup
    of OAuth tokens with proper error handling and logging.
    """

    def __init__(self):
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate token service configuration."""
        # Test encryption service availability
        try:
            from app.services.encryption_service import validate_encryption_config

            if not validate_encryption_config():
                raise TokenServiceError("Encryption service not properly configured")
        except Exception as e:
            raise TokenServiceError(f"Encryption service validation failed: {e}") from e

        logger.info("Token service initialized successfully")

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def store_tokens(
        self, user_id: str, token_response: TokenResponse, provider: str = "google"
    ) -> bool:
        """
        Store encrypted OAuth tokens in database.

        Args:
            user_id: UUID string of the user
            token_response: TokenResponse from OAuth flow
            provider: OAuth provider (default: "google")

        Returns:
            bool: True if storage successful, False otherwise

        Raises:
            TokenServiceError: If storage fails due to system errors
        """
        try:
            # Encrypt tokens before storage
            encrypted_access, encrypted_refresh = encrypt_oauth_tokens(
                access_token=token_response.access_token, refresh_token=token_response.refresh_token
            )

            # Prepare data for database
            query = """
            INSERT INTO oauth_tokens (
                user_id, provider, access_token, refresh_token,
                scope, expires_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (user_id)
            DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                scope = EXCLUDED.scope,
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW(),
                refresh_failure_count = 0,
                last_refresh_attempt = NULL
            """

            # Use database pool helper function
            affected_rows = await execute_query(
                query,
                (
                    user_id,
                    provider,
                    encrypted_access,
                    encrypted_refresh,
                    token_response.scope,
                    token_response.expires_at,
                ),
            )

            success = affected_rows > 0

            if success:
                logger.info(
                    "OAuth tokens stored successfully",
                    user_id=user_id,
                    provider=provider,
                    has_refresh_token=bool(token_response.refresh_token),
                    expires_at=(
                        token_response.expires_at.isoformat() if token_response.expires_at else None
                    ),
                )

            return success

        except EncryptionError as e:
            logger.error(
                "Token encryption failed", user_id=user_id, provider=provider, error=str(e)
            )
            raise TokenServiceError(f"Token encryption failed: {e}", user_id=user_id) from e

        except DatabaseError as e:
            logger.error(
                "Database error storing tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )
            raise TokenServiceError(f"Database error storing tokens: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error storing tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenServiceError(f"Token storage failed: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def get_tokens(self, user_id: str, provider: str = "google") -> OAuthToken | None:
        """
        Retrieve and decrypt OAuth tokens for user.

        Args:
            user_id: UUID string of the user
            provider: OAuth provider (default: "google")

        Returns:
            Optional[OAuthToken]: Decrypted token object if found, None otherwise

        Raises:
            TokenServiceError: If decryption or database errors occur
        """
        try:
            query = """
            SELECT access_token, refresh_token, scope, expires_at, updated_at
            FROM oauth_tokens
            WHERE user_id = %s AND provider = %s
            """

            # Use database pool helper function
            row = await fetch_one(query, (user_id, provider))

            if not row:
                logger.debug("No tokens found for user", user_id=user_id, provider=provider)
                return None

            # Unpack row data
            row_values = list(row.values())
            encrypted_access, encrypted_refresh, scope, expires_at, updated_at = row_values

            # Decrypt tokens
            access_token, refresh_token = decrypt_oauth_tokens(
                encrypted_access=encrypted_access, encrypted_refresh=encrypted_refresh
            )

            # Create domain object
            oauth_token = OAuthToken(
                user_id=user_id,
                provider=provider,
                access_token=access_token,
                refresh_token=refresh_token,
                scope=scope,
                expires_at=expires_at,
                updated_at=updated_at,
            )

            logger.debug(
                "Tokens retrieved successfully",
                user_id=user_id,
                provider=provider,
                expires_at=expires_at.isoformat() if expires_at else None,
                is_expired=oauth_token.is_expired(),
            )

            return oauth_token

        except EncryptionError as e:
            logger.error(
                "Token decryption failed", user_id=user_id, provider=provider, error=str(e)
            )
            raise TokenServiceError(f"Token decryption failed: {e}", user_id=user_id) from e

        except DatabaseError as e:
            logger.error(
                "Database error retrieving tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )
            raise TokenServiceError(
                f"Database error retrieving tokens: {e}", user_id=user_id
            ) from e

        except Exception as e:
            logger.error(
                "Unexpected error retrieving tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenServiceError(f"Token retrieval failed: {e}", user_id=user_id) from e

    async def refresh_token_if_needed(
        self, user_id: str, provider: str = "google"
    ) -> OAuthToken | None:
        """
        Check token expiration and refresh if needed (on-demand refresh).

        Args:
            user_id: UUID string of the user
            provider: OAuth provider (default: "google")

        Returns:
            Optional[OAuthToken]: Refreshed token if successful, None if no tokens or refresh failed

        Raises:
            TokenServiceError: If refresh fails due to system errors
        """
        try:
            # Get current tokens
            current_tokens = await self.get_tokens(user_id, provider)
            if not current_tokens:
                logger.debug("No tokens to refresh", user_id=user_id, provider=provider)
                return None

            # Check if refresh is needed
            if not current_tokens.needs_refresh(buffer_minutes=TOKEN_REFRESH_BUFFER_MINUTES):
                logger.debug(
                    "Token refresh not needed",
                    user_id=user_id,
                    provider=provider,
                    expires_at=(
                        current_tokens.expires_at.isoformat() if current_tokens.expires_at else None
                    ),
                )
                return current_tokens

            # Perform refresh
            return await self._perform_token_refresh(user_id, current_tokens, provider)

        except TokenServiceError:
            raise  # Re-raise token service errors
        except Exception as e:
            logger.error(
                "Unexpected error during token refresh check",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenServiceError(f"Token refresh check failed: {e}", user_id=user_id) from e

    async def force_refresh_token(
        self, user_id: str, provider: str = "google"
    ) -> OAuthToken | None:
        """
        Force token refresh regardless of expiration status.

        Args:
            user_id: UUID string of the user
            provider: OAuth provider (default: "google")

        Returns:
            Optional[OAuthToken]: Refreshed token if successful, None if failed

        Raises:
            TokenServiceError: If refresh fails due to system errors
        """
        try:
            current_tokens = await self.get_tokens(user_id, provider)
            if not current_tokens:
                logger.warning(
                    "No tokens found for forced refresh", user_id=user_id, provider=provider
                )
                return None

            logger.info("Forcing token refresh", user_id=user_id, provider=provider)
            return await self._perform_token_refresh(user_id, current_tokens, provider)

        except TokenServiceError:
            raise
        except Exception as e:
            logger.error(
                "Unexpected error during forced token refresh",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenServiceError(f"Forced token refresh failed: {e}", user_id=user_id) from e

    async def _perform_token_refresh(
        self, user_id: str, current_tokens: OAuthToken, provider: str
    ) -> OAuthToken | None:
        """
        Perform the actual token refresh operation.

        Args:
            user_id: UUID string of the user
            current_tokens: Current token object
            provider: OAuth provider

        Returns:
            Optional[OAuthToken]: Refreshed token if successful, None if failed

        Raises:
            TokenServiceError: If refresh fails due to system errors
        """
        try:
            if not current_tokens.refresh_token:
                logger.warning(
                    "No refresh token available for refresh", user_id=user_id, provider=provider
                )
                raise TokenServiceError(
                    "No refresh token available - re-authentication required",
                    user_id=user_id,
                    recoverable=False,
                )

            # Update refresh attempt tracking
            await self._update_refresh_attempt(user_id, provider)

            # Call Google OAuth service to refresh
            # Call Google OAuth service to refresh with timeout + retry
            new_token_response = await asyncio.wait_for(
                retry_with_backoff(
                refresh_google_token,
                current_tokens.refresh_token,
                retries=3,       # up to 3 tries
                base_delay=1     # 1s, 2s, 4s...
            ),
            timeout=10  # seconds per full refresh attempt
)

            # Store new tokens
            success = await self.store_tokens(user_id, new_token_response, provider)
            if not success:
                raise TokenServiceError("Failed to store refreshed tokens", user_id=user_id)

            # Get updated tokens
            refreshed_tokens = await self.get_tokens(user_id, provider)

            logger.info(
                "Token refresh successful",
                user_id=user_id,
                provider=provider,
                new_expires_at=(
                    refreshed_tokens.expires_at.isoformat()
                    if refreshed_tokens and refreshed_tokens.expires_at
                    else None
                ),
            )

            return refreshed_tokens

        except GoogleOAuthError as e:
            logger.error(
                "Google OAuth error during token refresh",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_code=getattr(e, "error_code", None),
            )

            # Update failure count
            await self._update_refresh_failure(user_id, provider)

            # Check if we should invalidate tokens
            if await self._should_invalidate_tokens(user_id, provider):
                logger.warning(
                    "Too many refresh failures - invalidating tokens",
                    user_id=user_id,
                    provider=provider,
                )
                await self.delete_tokens(user_id, provider)

            raise TokenServiceError(
                f"Token refresh failed: {e}", user_id=user_id, recoverable=False
            ) from e

        except TokenServiceError:
            raise
        except Exception as e:
            logger.error(
                "Unexpected error during token refresh",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            await self._update_refresh_failure(user_id, provider)
            raise TokenServiceError(f"Token refresh failed: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def delete_tokens(self, user_id: str, provider: str = "google") -> bool:
        """
        Delete OAuth tokens for user (for disconnection or cleanup).

        Args:
            user_id: UUID string of the user
            provider: OAuth provider (default: "google")

        Returns:
            bool: True if deletion successful, False otherwise
        """
        try:
            query = "DELETE FROM oauth_tokens WHERE user_id = %s AND provider = %s"

            # Use database pool helper function
            affected_rows = await execute_query(query, (user_id, provider))

            success = affected_rows > 0

            if success:
                logger.info("OAuth tokens deleted successfully", user_id=user_id, provider=provider)
            else:
                logger.debug("No tokens found to delete", user_id=user_id, provider=provider)

            return success

        except DatabaseError as e:
            logger.error(
                "Database error deleting tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error deleting tokens",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def revoke_and_delete_tokens(self, user_id: str, provider: str = "google") -> bool:
        """
        Revoke tokens with provider and delete from database.

        Args:
            user_id: UUID string of the user
            provider: OAuth provider (default: "google")

        Returns:
            bool: True if revocation and deletion successful, False otherwise
        """
        try:
            # Get tokens for revocation
            tokens = await self.get_tokens(user_id, provider)
            if not tokens:
                logger.debug("No tokens found to revoke", user_id=user_id, provider=provider)
                return True  # Nothing to revoke is considered success

            # Revoke with provider (best effort)
            revoke_success = True
            if tokens.access_token:

                revoke_success = await asyncio.to_thread(
                revoke_google_token,
                tokens.access_token
                )

                if not revoke_success:
                    logger.warning(
                        "Failed to revoke access token with provider",
                        user_id=user_id,
                        provider=provider,
                    )

            # Delete from database regardless of revocation result
            delete_success = await self.delete_tokens(user_id, provider)

            overall_success = delete_success  # Database deletion is more critical

            logger.info(
                "Token revocation and deletion completed",
                user_id=user_id,
                provider=provider,
                revoke_success=revoke_success,
                delete_success=delete_success,
                overall_success=overall_success,
            )

            return overall_success

        except Exception as e:
            logger.error(
                "Error during token revocation and deletion",
                user_id=user_id,
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def get_tokens_expiring_soon(
        self, provider: str = "google", buffer_minutes: int = TOKEN_REFRESH_BUFFER_MINUTES
    ) -> list[str]:
        """
        Get user IDs with tokens expiring within buffer time (for background refresh).

        Args:
            provider: OAuth provider (default: "google")
            buffer_minutes: Minutes before expiration to consider "expiring soon"

        Returns:
            list[str]: List of user IDs with tokens needing refresh
        """
        try:
            # FIXED: Use timezone-aware datetime
            buffer_time = datetime.now(UTC) + timedelta(minutes=buffer_minutes)

            query = """
            SELECT user_id
            FROM oauth_tokens
            WHERE provider = %s
            AND expires_at IS NOT NULL
            AND expires_at <= %s
            AND refresh_token IS NOT NULL
            AND (refresh_failure_count IS NULL OR refresh_failure_count < %s)
            LIMIT %s
            """

            # Use database pool helper function
            rows = await fetch_all(
                query,
                (
                    provider,
                    buffer_time,
                    REFRESH_FAILURE_THRESHOLD,
                    TOKEN_CLEANUP_BATCH_SIZE,
                ),
            )

            user_ids = [str(list(row.values())[0]) for row in rows]

            logger.debug(
                "Found tokens expiring soon",
                provider=provider,
                count=len(user_ids),
                buffer_minutes=buffer_minutes,
            )

            return user_ids

        except DatabaseError as e:
            logger.error(
                "Database error finding expiring tokens",
                provider=provider,
                error=str(e),
            )
            return []
        except Exception as e:
            logger.error(
                "Unexpected error finding expiring tokens",
                provider=provider,
                error=str(e),
                error_type=type(e).__name__,
            )
            return []

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _update_refresh_attempt(self, user_id: str, provider: str) -> None:
        """Update last refresh attempt timestamp."""
        try:
            query = """
            UPDATE oauth_tokens
            SET last_refresh_attempt = NOW()
            WHERE user_id = %s AND provider = %s
            """

            await execute_query(query, (user_id, provider))

        except Exception as e:
            logger.warning(
                "Failed to update refresh attempt timestamp",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _update_refresh_failure(self, user_id: str, provider: str) -> None:
        """Increment refresh failure count."""
        try:
            query = """
            UPDATE oauth_tokens
            SET refresh_failure_count = COALESCE(refresh_failure_count, 0) + 1,
                last_refresh_attempt = NOW()
            WHERE user_id = %s AND provider = %s
            """

            await execute_query(query, (user_id, provider))

        except Exception as e:
            logger.warning(
                "Failed to update refresh failure count",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _should_invalidate_tokens(self, user_id: str, provider: str) -> bool:
        """Check if tokens should be invalidated due to repeated failures."""
        try:
            query = """
            SELECT refresh_failure_count
            FROM oauth_tokens
            WHERE user_id = %s AND provider = %s
            """

            row = await fetch_one(query, (user_id, provider))

            if row:
                failure_count = list(row.values())[0]
                if failure_count:
                    return failure_count >= REFRESH_FAILURE_THRESHOLD

            return False

        except Exception as e:
            logger.warning(
                "Failed to check refresh failure count",
                user_id=user_id,
                provider=provider,
                error=str(e),
            )
            return False

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def health_check(self) -> dict[str, any]:
        """
        Check token service health.

        Returns:
            dict: Health status and metrics
        """
        try:
            health_data = {
                "healthy": True,
                "service": "token_service",
                "database_connectivity": "unknown",
                "encryption_service": "unknown",
            }

            # Test database connectivity
            try:
                row = await fetch_one("SELECT COUNT(*) FROM oauth_tokens")
                if row:
                    count = list(row.values())[0]
                    health_data["database_connectivity"] = "ok"
                    health_data["total_tokens"] = count
                else:
                    health_data["database_connectivity"] = "error: no result"
                    health_data["healthy"] = False
            except Exception as e:
                health_data["database_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Test encryption service
            try:
                from app.services.encryption_service import validate_encryption_config

                if validate_encryption_config():
                    health_data["encryption_service"] = "ok"
                else:
                    health_data["encryption_service"] = "validation_failed"
                    health_data["healthy"] = False
            except Exception as e:
                health_data["encryption_service"] = f"error: {str(e)}"
                health_data["healthy"] = False

            return health_data

        except Exception as e:
            logger.error("Token service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "token_service",
                "error": str(e),
            }


# Singleton instance for application use
token_service = TokenService()


# Convenience functions for easy import
async def store_oauth_tokens(user_id: str, token_response: TokenResponse) -> bool:
    """Store OAuth tokens for user."""
    return await token_service.store_tokens(user_id, token_response)


async def get_oauth_tokens(user_id: str) -> OAuthToken | None:
    """Get OAuth tokens for user."""
    return await token_service.get_tokens(user_id)


async def refresh_oauth_tokens(user_id: str) -> OAuthToken | None:
    """Refresh OAuth tokens if needed."""
    return await token_service.refresh_token_if_needed(user_id)


async def revoke_oauth_tokens(user_id: str) -> bool:
    """Revoke and delete OAuth tokens."""
    return await token_service.revoke_and_delete_tokens(user_id)


async def token_service_health() -> dict[str, any]:
    """Check token service health."""
    return await token_service.health_check()
