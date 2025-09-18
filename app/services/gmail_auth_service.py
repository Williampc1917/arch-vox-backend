"""
Gmail auth Service for high-level OAuth orchestration.
Coordinates OAuth flow, user status updates, and connection management.
REFACTORED: Now uses database connection pool instead of direct psycopg connections.
takes care of gmail and calendar auth and connection status updates.
"""

import asyncio
from datetime import datetime

from app.db.helpers import DatabaseError, execute_query, fetch_one, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.models.domain.oauth_domain import OAuthToken
from app.services.google_oauth_service import (
    GoogleOAuthError,
    exchange_oauth_code,
    generate_google_oauth_url,
)
from app.services.oauth_state_service import (
    OAuthStateError,
    generate_oauth_state,
    validate_oauth_state,
)
from app.services.token_service import (
    TokenServiceError,
    get_oauth_tokens,
    refresh_oauth_tokens,
    revoke_oauth_tokens,
    store_oauth_tokens,
)

logger = get_logger(__name__)


class GmailConnectionError(Exception):
    """Custom exception for Gmail connection operations."""

    def __init__(
        self,
        message: str,
        user_id: str | None = None,
        error_code: str | None = None,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.user_id = user_id
        self.error_code = error_code
        self.recoverable = recoverable


class GmailConnectionStatus:
    """Represents Gmail connection status for a user."""

    def __init__(
        self,
        connected: bool,
        user_id: str,
        provider: str = "google",
        scope: str | None = None,
        expires_at: datetime | None = None,
        needs_refresh: bool = False,
        last_used: datetime | None = None,
        connection_health: str = "unknown",
    ):
        self.connected = connected
        self.user_id = user_id
        self.provider = provider
        self.scope = scope
        self.expires_at = expires_at
        self.needs_refresh = needs_refresh
        self.last_used = last_used
        self.connection_health = connection_health

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "connected": self.connected,
            "provider": self.provider,
            "scope": self.scope,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self.needs_refresh,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "connection_health": self.connection_health,
        }


class GmailConnectionService:
    """
    High-level service for Gmail OAuth connection management.

    Orchestrates the complete OAuth flow, manages user connection status,
    and integrates with existing user management services.
    """

    def __init__(self):
        self._config_validated = False

    def _ensure_config_validated(self) -> None:
        """Validate service configuration when first used."""
        if self._config_validated:
            return

        # Test database pool availability
        try:
            from app.db.pool import db_pool

            if not db_pool._initialized:
                raise GmailConnectionError("Database pool not initialized")
        except Exception as e:
            raise GmailConnectionError(f"Database pool validation failed: {e}") from e

        self._config_validated = True
        logger.info("Gmail connection service initialized successfully")

    def initiate_oauth_flow(self, user_id: str) -> tuple[str, str]:
        """
        Initiate OAuth flow for Gmail connection.

        Args:
            user_id: UUID string of the user

        Returns:
            Tuple[str, str]: (oauth_url, state) for the OAuth flow

        Raises:
            GmailConnectionError: If OAuth initiation fails
        """
        self._ensure_config_validated()

        try:
            logger.info("Initiating Gmail OAuth flow", user_id=user_id)

            # Generate secure state parameter
            state = generate_oauth_state(user_id)

            # Generate Google OAuth URL
            oauth_url = generate_google_oauth_url(state)

            logger.info(
                "OAuth flow initiated successfully",
                user_id=user_id,
                state_preview=state[:8] + "...",
                url_length=len(oauth_url),
            )

            return oauth_url, state

        except OAuthStateError as e:
            logger.error("OAuth state generation failed", user_id=user_id, error=str(e))
            raise GmailConnectionError(f"OAuth initiation failed: {e}", user_id=user_id) from e

        except GoogleOAuthError as e:
            logger.error(
                "Google OAuth URL generation failed",
                user_id=user_id,
                error=str(e),
                error_code=getattr(e, "error_code", None),
            )
            raise GmailConnectionError(f"OAuth URL generation failed: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error during OAuth initiation",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"OAuth initiation failed: {e}", user_id=user_id) from e

    async def complete_oauth_flow(self, user_id: str, authorization_code: str, state: str) -> bool:
        """
        Complete OAuth flow and establish Gmail connection.

        Args:
            user_id: UUID string of the user
            authorization_code: Authorization code from OAuth callback
            state: State parameter for validation

        Returns:
            bool: True if connection established successfully

        Raises:
            GmailConnectionError: If OAuth completion fails
        """
        self._ensure_config_validated()

        try:
            logger.info(
                "Completing Gmail OAuth flow",
                user_id=user_id,
                code_preview=authorization_code[:12] + "...",
                state_preview=state[:8] + "...",
            )

            # Validate state parameter (CSRF protection)
            if not validate_oauth_state(state, user_id):
                logger.warning(
                    "OAuth state validation failed",
                    user_id=user_id,
                    state_preview=state[:8] + "...",
                )
                raise GmailConnectionError(
                    "Invalid OAuth state - possible security issue",
                    user_id=user_id,
                    error_code="invalid_state",
                )

            # Exchange authorization code for tokens
            token_response = exchange_oauth_code(authorization_code)

            # Store encrypted tokens in database
            store_success = store_oauth_tokens(user_id, token_response)
            if not store_success:
                raise GmailConnectionError(
                    "Failed to store OAuth tokens",
                    user_id=user_id,
                    error_code="token_storage_failed",
                )

            # Update user Gmail connection status
            await self._update_user_gmail_status(user_id, connected=True)

            # Check if Calendar permissions were also granted and update status
            await self._update_calendar_status_if_granted(user_id, token_response.scope)

            logger.info(
                "Gmail OAuth flow completed successfully",
                user_id=user_id,
                has_refresh_token=bool(token_response.refresh_token),
                expires_at=(
                    token_response.expires_at.isoformat() if token_response.expires_at else None
                ),
            )

            return True

        except GmailConnectionError:
            raise  # Re-raise Gmail connection errors

        except GoogleOAuthError as e:
            logger.error(
                "Google OAuth error during flow completion",
                user_id=user_id,
                error=str(e),
                error_code=getattr(e, "error_code", None),
            )
            raise GmailConnectionError(
                f"OAuth flow failed: {e}",
                user_id=user_id,
                error_code=getattr(e, "error_code", "oauth_failed"),
            ) from e

        except TokenServiceError as e:
            logger.error(
                "Token service error during flow completion", user_id=user_id, error=str(e)
            )
            raise GmailConnectionError(
                f"Token storage failed: {e}", user_id=user_id, error_code="token_storage_failed"
            ) from e

        except Exception as e:
            logger.error(
                "Unexpected error during OAuth flow completion",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"OAuth completion failed: {e}", user_id=user_id) from e

    async def get_connection_status(self, user_id: str) -> GmailConnectionStatus:
        """
        Get comprehensive Gmail connection status for user.

        Args:
            user_id: UUID string of the user

        Returns:
            GmailConnectionStatus: Complete connection status information
        """
        self._ensure_config_validated()

        try:
            logger.debug("Getting Gmail connection status", user_id=user_id)

            # Check database for user Gmail status
            user_connected = await self._get_user_gmail_status(user_id)

            if not user_connected:
                return GmailConnectionStatus(
                    connected=False, user_id=user_id, connection_health="disconnected"
                )

            # Get OAuth tokens
            tokens = get_oauth_tokens(user_id)

            if not tokens:
                # User marked as connected but no tokens - inconsistent state
                logger.warning(
                    "User marked as Gmail connected but no tokens found", user_id=user_id
                )
                # Fix inconsistent state
                await self._update_user_gmail_status(user_id, connected=False)

                return GmailConnectionStatus(
                    connected=False, user_id=user_id, connection_health="token_missing"
                )

            # Determine connection health
            connection_health = self._assess_connection_health(tokens)

            return GmailConnectionStatus(
                connected=True,
                user_id=user_id,
                provider=tokens.provider,
                scope=tokens.scope,
                expires_at=tokens.expires_at,
                needs_refresh=tokens.needs_refresh(),
                last_used=getattr(tokens, "last_used_at", None),
                connection_health=connection_health,
            )

        except Exception as e:
            logger.error(
                "Error getting Gmail connection status",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return GmailConnectionStatus(
                connected=False, user_id=user_id, connection_health="error"
            )

    async def disconnect_gmail(self, user_id: str) -> bool:
        """
        Disconnect Gmail and revoke all tokens.

        Args:
            user_id: UUID string of the user

        Returns:
            bool: True if disconnection successful
        """
        self._ensure_config_validated()

        try:
            logger.info("Disconnecting Gmail for user", user_id=user_id)

            # Revoke tokens with Google and delete from database
            revoke_success = revoke_oauth_tokens(user_id)

            # Update user status regardless of revocation result
            # (local disconnection should succeed even if remote revocation fails)
            await self._update_user_gmail_status(user_id, connected=False)

            # Update onboarding step if user was in Gmail step
            await self._handle_disconnect_onboarding_update(user_id)

            logger.info(
                "Gmail disconnection completed", user_id=user_id, revoke_success=revoke_success
            )

            return True

        except Exception as e:
            logger.error(
                "Error during Gmail disconnection",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def refresh_connection(self, user_id: str) -> bool:
        """
        Refresh Gmail connection tokens.

        Args:
            user_id: UUID string of the user

        Returns:
            bool: True if refresh successful

        Raises:
            GmailConnectionError: If refresh fails due to system errors
        """
        self._ensure_config_validated()

        try:
            logger.info("Refreshing Gmail connection", user_id=user_id)

            # Attempt token refresh
            refreshed_tokens = refresh_oauth_tokens(user_id)

            if refreshed_tokens:
                logger.info(
                    "Gmail connection refreshed successfully",
                    user_id=user_id,
                    new_expires_at=(
                        refreshed_tokens.expires_at.isoformat()
                        if refreshed_tokens.expires_at
                        else None
                    ),
                )
                return True
            else:
                logger.warning(
                    "Gmail connection refresh failed - tokens not found or refresh failed",
                    user_id=user_id,
                )

                # Update user status to disconnected
                await self._update_user_gmail_status(user_id, connected=False)

                raise GmailConnectionError(
                    "Gmail connection refresh failed - re-authentication required",
                    user_id=user_id,
                    error_code="refresh_failed",
                )

        except TokenServiceError as e:
            logger.error(
                "Token service error during connection refresh", user_id=user_id, error=str(e)
            )

            # Update user status if refresh failed permanently
            if not getattr(e, "recoverable", True):
                await self._update_user_gmail_status(user_id, connected=False)

            raise GmailConnectionError(
                f"Connection refresh failed: {e}", user_id=user_id, error_code="refresh_failed"
            ) from e

        except Exception as e:
            logger.error(
                "Unexpected error during connection refresh",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Connection refresh failed: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _get_user_gmail_status(self, user_id: str) -> bool:
        """
        Get Gmail connection status from users table.

        Args:
            user_id: UUID string of the user

        Returns:
            bool: True if user has Gmail connected, False otherwise

        Raises:
            GmailConnectionError: If database operation fails
        """
        try:
            query = "SELECT gmail_connected FROM users WHERE id = %s AND is_active = true"

            # Use database pool helper function
            row = await fetch_one(query, (user_id,))

            if row:
                gmail_connected = list(row.values())[0]
                return bool(gmail_connected)
            else:
                logger.warning("User not found or inactive", user_id=user_id)
                return False

        except DatabaseError as e:
            logger.error(
                "Database error getting user Gmail status",
                user_id=user_id,
                error=str(e),
            )
            raise GmailConnectionError(
                f"Database error getting Gmail status: {e}", user_id=user_id
            ) from e
        except Exception as e:
            logger.error(
                "Unexpected error getting user Gmail status",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to get Gmail status: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _update_user_gmail_status(self, user_id: str, connected: bool) -> bool:
        """
        Update Gmail connection status in users table.

        Args:
            user_id: UUID string of the user
            connected: Whether Gmail is connected or not

        Returns:
            bool: True if update successful, False otherwise

        Raises:
            GmailConnectionError: If database operation fails
        """
        try:
            # Update gmail_connected and potentially onboarding_step
            if connected:
                # When connecting, advance onboarding step appropriately
                query = """
                UPDATE users
                SET gmail_connected = %s,
                    onboarding_step = CASE
                        WHEN onboarding_step = 'profile' THEN 'gmail'
                        WHEN onboarding_step = 'gmail' THEN 'completed'
                        ELSE onboarding_step
                    END,
                    onboarding_completed = CASE
                        WHEN onboarding_step = 'gmail' THEN true
                        ELSE onboarding_completed
                    END,
                    updated_at = NOW()
                WHERE id = %s AND is_active = true
                """
            else:
                # When disconnecting, just update gmail_connected
                query = """
                UPDATE users
                SET gmail_connected = %s, updated_at = NOW()
                WHERE id = %s AND is_active = true
                """

            # Use database pool helper function
            affected_rows = await execute_query(query, (connected, user_id))

            success = affected_rows > 0

            if success:
                logger.info("User Gmail status updated", user_id=user_id, connected=connected)
            else:
                logger.warning(
                    "No user found to update Gmail status", user_id=user_id, connected=connected
                )

            return success

        except DatabaseError as e:
            logger.error(
                "Database error updating user Gmail status",
                user_id=user_id,
                connected=connected,
                error=str(e),
            )
            raise GmailConnectionError(
                f"Database error updating Gmail status: {e}", user_id=user_id
            ) from e
        except Exception as e:
            logger.error(
                "Unexpected error updating user Gmail status",
                user_id=user_id,
                connected=connected,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(
                f"Failed to update Gmail status: {e}", user_id=user_id
            ) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _handle_disconnect_onboarding_update(self, user_id: str) -> None:
        """
        Handle onboarding step updates when disconnecting Gmail.

        Args:
            user_id: UUID string of the user

        Raises:
            GmailConnectionError: If database operation fails
        """
        try:
            # If user was on 'completed' step, move back to 'gmail'
            # If user was on 'gmail' step, move back to 'profile'
            query = """
            UPDATE users
            SET onboarding_step = CASE
                WHEN onboarding_step = 'completed' THEN 'gmail'
                WHEN onboarding_step = 'gmail' THEN 'profile'
                ELSE onboarding_step
            END,
            onboarding_completed = CASE
                WHEN onboarding_step = 'completed' THEN false
                ELSE onboarding_completed
            END,
            updated_at = NOW()
            WHERE id = %s AND is_active = true
            """

            # Use database pool helper function
            await execute_query(query, (user_id,))

            logger.debug("Onboarding step updated after Gmail disconnect", user_id=user_id)

        except DatabaseError as e:
            logger.error(
                "Database error updating onboarding step after disconnect",
                user_id=user_id,
                error=str(e),
            )
            raise GmailConnectionError(
                f"Database error updating onboarding: {e}", user_id=user_id
            ) from e
        except Exception as e:
            logger.warning(
                "Failed to update onboarding step after Gmail disconnect",
                user_id=user_id,
                error=str(e),
            )
            # Don't raise exception for onboarding updates - they're not critical
            logger.debug("Onboarding update failure is non-critical", user_id=user_id)

    async def _update_calendar_status_if_granted(self, user_id: str, scope: str) -> None:
        """
        Check if Calendar permissions were granted and update user status accordingly.

        Args:
            user_id: UUID string of the user
            scope: OAuth scope string from token response
        """
        try:
            # Check if scope contains calendar permissions
            calendar_indicators = ["calendar.readonly", "calendar.events", "calendar"]
            has_calendar_access = any(indicator in scope for indicator in calendar_indicators)

            if has_calendar_access:
                # Import and call the calendar status update function
                from app.services.calendar_operations_service import _update_user_calendar_status

                await _update_user_calendar_status(user_id, connected=True)

                logger.info(
                    "Calendar permissions detected and status updated",
                    user_id=user_id,
                    scope_preview=scope[:50] + "..." if len(scope) > 50 else scope,
                )
            else:
                logger.debug(
                    "No calendar permissions detected in OAuth scope",
                    user_id=user_id,
                    scope_preview=scope[:50] + "..." if len(scope) > 50 else scope,
                )

        except Exception as e:
            logger.error(
                "Error updating calendar status after OAuth completion",
                user_id=user_id,
                error=str(e),
            )
            # Don't raise exception - Calendar is optional, Gmail is required

    def _assess_connection_health(self, tokens: OAuthToken) -> str:
        """Assess the health of the Gmail connection."""
        try:
            if tokens.is_expired():
                if tokens.refresh_token:
                    return "expired_but_refreshable"
                else:
                    return "expired_no_refresh"

            if tokens.needs_refresh(buffer_minutes=60):  # Within 1 hour of expiring
                return "expiring_soon"

            return "healthy"

        except Exception as e:
            logger.warning(
                "Error assessing connection health", user_id=tokens.user_id, error=str(e)
            )
            return "unknown"

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def get_connection_metrics(self) -> dict[str, any]:
        """
        Get Gmail connection metrics for monitoring.

        Returns:
            dict: Connection metrics and statistics

        Raises:
            GmailConnectionError: If database operation fails
        """
        try:
            query = """
            SELECT
                COUNT(*) as total_users,
                COUNT(CASE WHEN gmail_connected = true THEN 1 END) as connected_users,
                COUNT(CASE WHEN gmail_connected = true AND onboarding_completed = true THEN 1 END) as completed_users
            FROM users
            WHERE is_active = true
            """

            # Use database pool helper function
            row = await fetch_one(query)

            if row:
                row_values = list(row.values())
                total_users, connected_users, completed_users = row_values

                # Calculate connection rate
                connection_rate = (connected_users / total_users * 100) if total_users > 0 else 0

                return {
                    "total_users": total_users,
                    "connected_users": connected_users,
                    "completed_users": completed_users,
                    "connection_rate_percent": round(connection_rate, 2),
                    "healthy": True,
                }

            return {"healthy": False, "error": "No data returned"}

        except DatabaseError as e:
            logger.error("Database error getting connection metrics", error=str(e))
            raise GmailConnectionError(f"Database error getting metrics: {e}") from e
        except Exception as e:
            logger.error("Error getting connection metrics", error=str(e))
            raise GmailConnectionError(f"Failed to get connection metrics: {e}") from e

    def health_check(self) -> dict[str, any]:
        """
        Check Gmail connection service health.

        Returns:
            dict: Health status and metrics
        """
        try:
            health_data = {
                "healthy": True,
                "service": "gmail_connection",
                "database_connectivity": "unknown",
            }

            # Test database connectivity - FIX: Use asyncio.run for async method
            try:
                metrics = asyncio.run(self.get_connection_metrics())
                if metrics.get("healthy", False):
                    health_data["database_connectivity"] = "ok"
                    health_data.update(metrics)
                else:
                    health_data["database_connectivity"] = "error"
                    health_data["healthy"] = False
            except Exception as e:
                health_data["database_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Test dependent services - simpler approach without event loop conflicts
            try:
                from app.services.google_oauth_service import google_oauth_health
                from app.services.oauth_state_service import oauth_state_health
                from app.services.token_service import token_service_health

                # Call async health checks individually to avoid event loop conflicts
                try:
                    oauth_state_result = asyncio.run(oauth_state_health())
                    if isinstance(oauth_state_result, dict):
                        health_data["oauth_state_service"] = oauth_state_result.get(
                            "healthy", False
                        )
                    else:
                        health_data["oauth_state_service"] = False
                        health_data["oauth_state_error"] = (
                            f"Unexpected result type: {type(oauth_state_result)}"
                        )
                except Exception as e:
                    health_data["oauth_state_service"] = False
                    health_data["oauth_state_error"] = str(e)

                try:
                    token_service_result = asyncio.run(token_service_health())
                    if isinstance(token_service_result, dict):
                        health_data["token_service"] = token_service_result.get("healthy", False)
                    else:
                        health_data["token_service"] = False
                        health_data["token_service_error"] = (
                            f"Unexpected result type: {type(token_service_result)}"
                        )
                except Exception as e:
                    health_data["token_service"] = False
                    health_data["token_service_error"] = str(e)

                # Sync health check
                try:
                    google_oauth_result = google_oauth_health()
                    if isinstance(google_oauth_result, dict):
                        health_data["google_oauth_service"] = google_oauth_result.get(
                            "healthy", False
                        )
                    else:
                        health_data["google_oauth_service"] = False
                except Exception as e:
                    health_data["google_oauth_service"] = False
                    health_data["google_oauth_error"] = str(e)

                # Overall health depends on all services
                if not all(
                    [
                        health_data["oauth_state_service"],
                        health_data["google_oauth_service"],
                        health_data["token_service"],
                    ]
                ):
                    health_data["healthy"] = False

            except Exception as e:
                health_data["dependency_check"] = f"error: {str(e)}"
                health_data["healthy"] = False

            return health_data

        except Exception as e:
            logger.error("Gmail connection service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "gmail_connection",
                "error": str(e),
            }


# Singleton instance for application use
gmail_connection_service = GmailConnectionService()


# Convenience functions for easy import
def start_gmail_oauth(user_id: str) -> tuple[str, str]:
    """Start Gmail OAuth flow."""
    return gmail_connection_service.initiate_oauth_flow(user_id)


async def complete_gmail_oauth(user_id: str, code: str, state: str) -> bool:
    """Complete Gmail OAuth flow."""
    return await gmail_connection_service.complete_oauth_flow(user_id, code, state)


async def get_gmail_status(user_id: str) -> GmailConnectionStatus:
    """Get Gmail connection status."""
    return await gmail_connection_service.get_connection_status(user_id)


async def disconnect_gmail(user_id: str) -> bool:
    """Disconnect Gmail."""
    return await gmail_connection_service.disconnect_gmail(user_id)


async def refresh_gmail_connection(user_id: str) -> bool:
    """Refresh Gmail connection."""
    return await gmail_connection_service.refresh_connection(user_id)


def gmail_connection_health() -> dict[str, any]:
    """Check Gmail connection service health."""
    return gmail_connection_service.health_check()
