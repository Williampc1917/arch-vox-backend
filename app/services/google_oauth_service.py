"""
Google OAuth Service for Gmail API + Calendar API integration.
Handles OAuth URL generation, token exchange, and Google API interactions.
UPDATED: Now includes Calendar scopes for Gmail + Calendar triage functionality.
"""

import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

# OAuth configuration
GOOGLE_OAUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# UPDATED: Combined Gmail + Calendar scopes for triage functionality
GMAIL_CALENDAR_SCOPES = [
    # Gmail scopes for email management
    "https://www.googleapis.com/auth/gmail.readonly",  # Read emails
    "https://www.googleapis.com/auth/gmail.send",  # Send emails
    "https://www.googleapis.com/auth/gmail.compose",  # Draft emails
    "https://www.googleapis.com/auth/gmail.modify",  # Mark as read/unread
    # Calendar scopes for availability and event management
    "https://www.googleapis.com/auth/calendar.readonly",  # Check availability
    "https://www.googleapis.com/auth/calendar.events",  # Create/modify events
]

# Request timeouts and retry configuration
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # 2, 4, 8 seconds
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class GoogleOAuthError(Exception):
    """Custom exception for Google OAuth-related errors."""

    def __init__(
        self, message: str, error_code: str | None = None, response_data: dict | None = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.response_data = response_data or {}


class TokenResponse:
    """Structured representation of OAuth token response."""

    def __init__(self, data: dict):
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.token_type = data.get("token_type", "Bearer")
        self.expires_in = data.get("expires_in")
        self.scope = data.get("scope", "")

        # Calculate expiration timestamp
        if self.expires_in:
            self.expires_at = datetime.utcnow() + timedelta(seconds=int(self.expires_in))
        else:
            self.expires_at = None

    def is_valid(self) -> bool:
        """Check if token response contains required fields."""
        return bool(self.access_token and self.token_type)

    def has_gmail_access(self) -> bool:
        """Check if token has Gmail API access."""
        gmail_scopes = ["gmail.readonly", "gmail.send", "gmail.compose", "gmail.modify"]
        return any(scope in self.scope for scope in gmail_scopes)

    def has_calendar_access(self) -> bool:
        """Check if token has Calendar API access."""
        calendar_scopes = ["calendar.readonly", "calendar.events", "calendar"]
        return any(scope in self.scope for scope in calendar_scopes)

    def get_granted_scopes(self) -> dict:
        """Get breakdown of granted scopes by service."""
        scopes = self.scope.split() if self.scope else []

        gmail_scopes = [s for s in scopes if "gmail" in s]
        calendar_scopes = [s for s in scopes if "calendar" in s]

        return {
            "gmail": gmail_scopes,
            "calendar": calendar_scopes,
            "total_count": len(scopes),
            "has_gmail": len(gmail_scopes) > 0,
            "has_calendar": len(calendar_scopes) > 0,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }


class GoogleOAuthService:
    """
    Service for Google OAuth 2.0 operations with Gmail + Calendar APIs.

    Handles OAuth URL generation, token exchange, refresh, and revocation
    with proper error handling and retry logic for both Gmail and Calendar access.
    """

    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.gmail_redirect_uri()
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate Google OAuth configuration."""
        if not self.client_id:
            raise GoogleOAuthError("GOOGLE_CLIENT_ID not configured")
        if not self.client_secret:
            raise GoogleOAuthError("GOOGLE_CLIENT_SECRET not configured")
        if not self.redirect_uri:
            raise GoogleOAuthError("GOOGLE_REDIRECT_URI not configured")

        logger.info(
            "Google OAuth service initialized with Gmail + Calendar scopes",
            client_id_preview=self.client_id[:12] + "...",
            redirect_uri=self.redirect_uri,
            total_scopes=len(GMAIL_CALENDAR_SCOPES),
            gmail_scopes=len([s for s in GMAIL_CALENDAR_SCOPES if "gmail" in s]),
            calendar_scopes=len([s for s in GMAIL_CALENDAR_SCOPES if "calendar" in s]),
        )

    async def _post_with_retry(self, url: str, data: dict, operation: str) -> httpx.Response:
        """
        Perform POST request with retry/backoff handling.

        Args:
            url: Target URL
            data: Form data payload
            operation: Operation name for logging context
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = await client.post(url, data=data, headers=headers)

                    if (
                        response.status_code in RETRY_STATUS_CODES
                        and attempt < MAX_RETRIES
                    ):
                        wait_time = BACKOFF_FACTOR ** attempt
                        logger.warning(
                            "Google OAuth transient status",
                            operation=operation,
                            status_code=response.status_code,
                            attempt=attempt,
                            wait_time=wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    return response

                except httpx.RequestError as exc:
                    last_error = exc

                    if attempt == MAX_RETRIES:
                        raise

                    wait_time = BACKOFF_FACTOR ** attempt
                    logger.warning(
                        "Google OAuth request error, retrying",
                        operation=operation,
                        attempt=attempt,
                        wait_time=wait_time,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    await asyncio.sleep(wait_time)

        # All retries exhausted
        if last_error:
            raise last_error
        raise GoogleOAuthError(f"{operation} failed: Unknown error")

    def generate_oauth_url(self, state: str) -> str:
        """
        Generate Google OAuth authorization URL for Gmail + Calendar access.

        Args:
            state: CSRF protection state parameter

        Returns:
            str: Complete OAuth authorization URL

        Raises:
            GoogleOAuthError: If URL generation fails
        """
        try:
            params = {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(GMAIL_CALENDAR_SCOPES),  # UPDATED: Combined scopes
                "response_type": "code",
                "state": state,
                "access_type": "offline",  # Request refresh token
                "prompt": "consent",  # Force consent screen to get refresh token
                "include_granted_scopes": "true",  # Incremental authorization
            }

            oauth_url = f"{GOOGLE_OAUTH_BASE_URL}?{urlencode(params)}"

            logger.info(
                "OAuth URL generated successfully for Gmail + Calendar",
                state_preview=state[:8] + "...",
                url_length=len(oauth_url),
                gmail_scopes=len([s for s in GMAIL_CALENDAR_SCOPES if "gmail" in s]),
                calendar_scopes=len([s for s in GMAIL_CALENDAR_SCOPES if "calendar" in s]),
            )

            return oauth_url

        except Exception as e:
            logger.error(
                "Failed to generate OAuth URL",
                state_preview=state[:8] + "...",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GoogleOAuthError(f"OAuth URL generation failed: {e}") from e

    async def exchange_code_for_tokens(self, authorization_code: str) -> TokenResponse:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            authorization_code: Authorization code from OAuth callback

        Returns:
            TokenResponse: Parsed token response with access/refresh tokens

        Raises:
            GoogleOAuthError: If token exchange fails
        """
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": authorization_code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            }

            logger.info(
                "Exchanging authorization code for Gmail + Calendar tokens",
                code_preview=authorization_code[:12] + "...",
            )

            response = await self._post_with_retry(
                GOOGLE_TOKEN_URL, data, operation="code_exchange"
            )

            return self._handle_token_response(response, "code_exchange")

        except httpx.RequestError as e:
            logger.error(
                "Network error during token exchange",
                code_preview=authorization_code[:12] + "...",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GoogleOAuthError(f"Network error during token exchange: {e}") from e
        except Exception as e:
            logger.error(
                "Unexpected error during token exchange",
                code_preview=authorization_code[:12] + "...",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GoogleOAuthError(f"Token exchange failed: {e}") from e

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """
        Refresh access token using refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            TokenResponse: New access token (may include new refresh token)

        Raises:
            GoogleOAuthError: If token refresh fails
        """
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }

            logger.info(
                "Refreshing access token for Gmail + Calendar",
                refresh_token_preview=refresh_token[:12] + "...",
            )

            response = await self._post_with_retry(
                GOOGLE_TOKEN_URL, data, operation="token_refresh"
            )

            token_response = self._handle_token_response(response, "token_refresh")

            # Google may not return a new refresh token on refresh
            # If no new refresh token, preserve the existing one
            if not token_response.refresh_token:
                token_response.refresh_token = refresh_token
                logger.debug("Preserved existing refresh token")

            return token_response

        except httpx.RequestError as e:
            logger.error(
                "Network error during token refresh",
                refresh_token_preview=refresh_token[:12] + "...",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GoogleOAuthError(f"Network error during token refresh: {e}") from e
        except Exception as e:
            logger.error(
                "Unexpected error during token refresh",
                refresh_token_preview=refresh_token[:12] + "...",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GoogleOAuthError(f"Token refresh failed: {e}") from e

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke access or refresh token.

        Args:
            token: Access token or refresh token to revoke

        Returns:
            bool: True if revocation successful, False otherwise
        """
        try:
            data = {"token": token}

            logger.info(
                "Revoking Gmail + Calendar token",
                token_preview=token[:12] + "...",
            )

            response = await self._post_with_retry(
                GOOGLE_REVOKE_URL, data, operation="token_revocation"
            )

            success = response.status_code == 200

            if success:
                logger.info("Gmail + Calendar token revoked successfully")
            else:
                logger.warning(
                    "Token revocation failed",
                    status_code=response.status_code,
                    response_text=response.text[:200],
                )

            return success

        except httpx.RequestError as e:
            logger.error(
                "Network error during token revocation",
                token_preview=token[:12] + "...",
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error during token revocation",
                token_preview=token[:12] + "...",
                error=str(e),
            )
            return False

    def validate_token_permissions(self, token_response: TokenResponse) -> dict:
        """
        Validate that token has required Gmail + Calendar permissions.

        Args:
            token_response: Token response to validate

        Returns:
            dict: Validation results with permission breakdown
        """
        try:
            granted_scopes = token_response.get_granted_scopes()

            # Check for required Gmail scopes
            required_gmail = ["gmail.readonly", "gmail.send", "gmail.compose", "gmail.modify"]
            missing_gmail = []
            for required in required_gmail:
                if not any(required in scope for scope in granted_scopes["gmail"]):
                    missing_gmail.append(required)

            # Check for required Calendar scopes
            required_calendar = ["calendar.readonly", "calendar.events"]
            missing_calendar = []
            for required in required_calendar:
                if not any(required in scope for scope in granted_scopes["calendar"]):
                    missing_calendar.append(required)

            validation_result = {
                "valid": len(missing_gmail) == 0 and len(missing_calendar) == 0,
                "gmail_valid": len(missing_gmail) == 0,
                "calendar_valid": len(missing_calendar) == 0,
                "granted_scopes": granted_scopes,
                "missing_gmail_scopes": missing_gmail,
                "missing_calendar_scopes": missing_calendar,
                "total_granted": granted_scopes["total_count"],
                "total_required": len(GMAIL_CALENDAR_SCOPES),
            }

            logger.info(
                "Token permission validation completed",
                valid=validation_result["valid"],
                gmail_valid=validation_result["gmail_valid"],
                calendar_valid=validation_result["calendar_valid"],
                total_granted=validation_result["total_granted"],
            )

            return validation_result

        except Exception as e:
            logger.error(
                "Error during token permission validation",
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "valid": False,
                "error": str(e),
                "gmail_valid": False,
                "calendar_valid": False,
            }

    def _handle_token_response(self, response: httpx.Response, operation: str) -> TokenResponse:
        """
        Handle and validate token response from Google.

        Args:
            response: HTTP response from Google token endpoint
            operation: Operation name for logging (e.g., "code_exchange", "token_refresh")

        Returns:
            TokenResponse: Parsed and validated token response

        Raises:
            GoogleOAuthError: If response is invalid or contains errors
        """
        # Log response details for debugging
        logger.debug(
            f"Google {operation} response",
            status_code=response.status_code,
            response_size=len(response.text),
        )

        # Handle HTTP errors
        if not response.is_success:
            try:
                error_data = response.json()
                error_code = error_data.get("error", "unknown_error")
                error_description = error_data.get("error_description", "No description provided")

                logger.error(
                    f"Google {operation} failed",
                    status_code=response.status_code,
                    error_code=error_code,
                    error_description=error_description,
                )

                # Map common Google OAuth errors to user-friendly messages
                user_message = self._map_google_error(error_code)

                raise GoogleOAuthError(
                    user_message,
                    error_code=error_code,
                    response_data=error_data,
                )

            except ValueError:
                # Response is not JSON
                logger.error(
                    f"Google {operation} failed with non-JSON response",
                    status_code=response.status_code,
                    response_text=response.text[:200],
                )
                raise GoogleOAuthError(
                    f"Google OAuth service error (HTTP {response.status_code})"
                ) from None

        # Parse successful response
        try:
            data = response.json()
            token_response = TokenResponse(data)

            if not token_response.is_valid():
                logger.error(
                    f"Invalid token response from Google {operation}",
                    has_access_token=bool(token_response.access_token),
                    has_refresh_token=bool(token_response.refresh_token),
                    token_type=token_response.token_type,
                )
                raise GoogleOAuthError("Invalid token response from Google")

            # Log detailed scope information
            granted_scopes = token_response.get_granted_scopes()
            logger.info(
                f"Google {operation} successful",
                token_type=token_response.token_type,
                expires_in=token_response.expires_in,
                has_refresh_token=bool(token_response.refresh_token),
                gmail_scopes_count=len(granted_scopes["gmail"]),
                calendar_scopes_count=len(granted_scopes["calendar"]),
                has_gmail_access=granted_scopes["has_gmail"],
                has_calendar_access=granted_scopes["has_calendar"],
            )

            return token_response

        except ValueError as e:
            logger.error(
                f"Failed to parse Google {operation} response",
                response_text=response.text[:200],
                error=str(e),
            )
            raise GoogleOAuthError(f"Failed to parse Google response: {e}") from e

    def _map_google_error(self, error_code: str) -> str:
        """
        Map Google OAuth error codes to user-friendly messages.

        Args:
            error_code: Google OAuth error code

        Returns:
            str: User-friendly error message
        """
        error_messages = {
            "access_denied": "Gmail and Calendar access was denied. Please try connecting again and grant the required permissions.",
            "invalid_grant": "Authorization code expired or invalid. Please try connecting to Gmail and Calendar again.",
            "invalid_client": "Gmail and Calendar connection configuration error. Please contact support.",
            "invalid_request": "Invalid Gmail and Calendar connection request. Please try again.",
            "unauthorized_client": "Gmail and Calendar connection not authorized. Please contact support.",
            "unsupported_grant_type": "Gmail and Calendar connection method not supported. Please contact support.",
            "invalid_scope": "Invalid Gmail or Calendar permissions requested. Please contact support.",
        }

        return error_messages.get(
            error_code,
            f"Gmail and Calendar connection failed ({error_code}). Please try again or contact support.",
        )

    def health_check(self) -> dict[str, any]:
        """
        Check Google OAuth service health.

        Returns:
            dict: Health status and configuration
        """
        try:
            health_data = {
                "healthy": True,
                "service": "google_oauth",
                "config_valid": True,
                "endpoints": {
                    "auth_url": GOOGLE_OAUTH_BASE_URL,
                    "token_url": GOOGLE_TOKEN_URL,
                    "revoke_url": GOOGLE_REVOKE_URL,
                },
                "scopes": {
                    "total": GMAIL_CALENDAR_SCOPES,
                    "gmail": [s for s in GMAIL_CALENDAR_SCOPES if "gmail" in s],
                    "calendar": [s for s in GMAIL_CALENDAR_SCOPES if "calendar" in s],
                    "count": {
                        "total": len(GMAIL_CALENDAR_SCOPES),
                        "gmail": len([s for s in GMAIL_CALENDAR_SCOPES if "gmail" in s]),
                        "calendar": len([s for s in GMAIL_CALENDAR_SCOPES if "calendar" in s]),
                    },
                },
                "redirect_uri": self.redirect_uri,
            }

            # Test basic connectivity to Google (without making OAuth requests)
            try:
                response = httpx.get(GOOGLE_OAUTH_BASE_URL, timeout=5.0)
                health_data["google_connectivity"] = (
                    "ok" if response.is_success else f"error_{response.status_code}"
                )
            except httpx.RequestError as e:
                health_data["google_connectivity"] = f"error_{type(e).__name__}"
                health_data["healthy"] = False

            return health_data

        except Exception as e:
            logger.error("Google OAuth service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "google_oauth",
                "error": str(e),
            }


# Singleton instance for application use
google_oauth_service = GoogleOAuthService()


# Convenience functions for easy import
def generate_google_oauth_url(state: str) -> str:
    """Generate Google OAuth authorization URL for Gmail + Calendar."""
    return google_oauth_service.generate_oauth_url(state)


async def exchange_oauth_code(authorization_code: str) -> TokenResponse:
    """Exchange authorization code for Gmail + Calendar tokens."""
    return await google_oauth_service.exchange_code_for_tokens(authorization_code)


async def refresh_google_token(refresh_token: str) -> TokenResponse:
    """Refresh Google access token for Gmail + Calendar."""
    return await google_oauth_service.refresh_access_token(refresh_token)


async def revoke_google_token(token: str) -> bool:
    """Revoke Google OAuth token."""
    return await google_oauth_service.revoke_token(token)


def validate_google_token_permissions(token_response: TokenResponse) -> dict:
    """Validate Gmail + Calendar token permissions."""
    return google_oauth_service.validate_token_permissions(token_response)


def google_oauth_health() -> dict[str, any]:
    """Check Google OAuth service health."""
    return google_oauth_service.health_check()
