"""
Google OAuth Service for Gmail API integration.
Handles OAuth URL generation, token exchange, and Google API interactions.
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

# OAuth configuration
GOOGLE_OAUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Gmail scopes - minimal permissions for email management
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",  # Read emails
    "https://www.googleapis.com/auth/gmail.send",  # Send emails
    "https://www.googleapis.com/auth/gmail.compose",  # Draft emails
    "https://www.googleapis.com/auth/gmail.modify",  # Mark as read/unread
]

# Request timeouts and retry configuration
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # 2, 4, 8 seconds


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
    Service for Google OAuth 2.0 operations with Gmail API.

    Handles OAuth URL generation, token exchange, refresh, and revocation
    with proper error handling and retry logic.
    """

    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.gmail_redirect_uri()
        self._validate_config()
        self._session = self._create_session()

    def _validate_config(self) -> None:
        """Validate Google OAuth configuration."""
        if not self.client_id:
            raise GoogleOAuthError("GOOGLE_CLIENT_ID not configured")
        if not self.client_secret:
            raise GoogleOAuthError("GOOGLE_CLIENT_SECRET not configured")
        if not self.redirect_uri:
            raise GoogleOAuthError("GOOGLE_REDIRECT_URI not configured")

        logger.info(
            "Google OAuth service initialized",
            client_id_preview=self.client_id[:12] + "...",
            redirect_uri=self.redirect_uri,
            scopes_count=len(GMAIL_SCOPES),
        )

    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy."""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP codes
            allowed_methods=["GET", "POST"],  # Retry on these methods
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    def generate_oauth_url(self, state: str) -> str:
        """
        Generate Google OAuth authorization URL.

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
                "scope": " ".join(GMAIL_SCOPES),
                "response_type": "code",
                "state": state,
                "access_type": "offline",  # Request refresh token
                "prompt": "consent",  # Force consent screen to get refresh token
                "include_granted_scopes": "true",  # Incremental authorization
            }

            oauth_url = f"{GOOGLE_OAUTH_BASE_URL}?{urlencode(params)}"

            # ============================================================================
            # 🚨 TEMPORARY DEBUG LOGGING - DELETE THIS ENTIRE BLOCK AFTER TESTING! 🚨
            # ============================================================================
            # TODO: REMOVE THIS DEBUG LOG BEFORE PRODUCTION DEPLOYMENT
            # This logs sensitive OAuth URLs that should not be in production logs
            logger.warning(
                "🔧 TEMP DEBUG: OAuth URL Generated (DELETE THIS LOG!)",
                oauth_url=oauth_url,
                redirect_uri=self.redirect_uri,
                client_id_preview=self.client_id[:12] + "...",
            )
            print(f"🔧 DEBUG OAuth URL: {oauth_url}")  # Also print to console
            print(f"🔧 DEBUG Redirect URI: {self.redirect_uri}")
            # ============================================================================
            # 🚨 END OF TEMPORARY DEBUG BLOCK - DELETE EVERYTHING ABOVE THIS LINE 🚨
            # ============================================================================

            logger.info(
                "OAuth URL generated successfully",
                state_preview=state[:8] + "...",
                url_length=len(oauth_url),
                scopes=len(GMAIL_SCOPES),
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

    def exchange_code_for_tokens(self, authorization_code: str) -> TokenResponse:
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
                "Exchanging authorization code for tokens",
                code_preview=authorization_code[:12] + "...",
            )

            response = self._session.post(
                GOOGLE_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            )

            return self._handle_token_response(response, "code_exchange")

        except requests.exceptions.RequestException as e:
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

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
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
                "Refreshing access token",
                refresh_token_preview=refresh_token[:12] + "...",
            )

            response = self._session.post(
                GOOGLE_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            )

            token_response = self._handle_token_response(response, "token_refresh")

            # Google may not return a new refresh token on refresh
            # If no new refresh token, preserve the existing one
            if not token_response.refresh_token:
                token_response.refresh_token = refresh_token
                logger.debug("Preserved existing refresh token")

            return token_response

        except requests.exceptions.RequestException as e:
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

    def revoke_token(self, token: str) -> bool:
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
                "Revoking token",
                token_preview=token[:12] + "...",
            )

            response = self._session.post(
                GOOGLE_REVOKE_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            )

            success = response.status_code == 200

            if success:
                logger.info("Token revoked successfully")
            else:
                logger.warning(
                    "Token revocation failed",
                    status_code=response.status_code,
                    response_text=response.text[:200],
                )

            return success

        except requests.exceptions.RequestException as e:
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

    def _handle_token_response(self, response: requests.Response, operation: str) -> TokenResponse:
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
        if not response.ok:
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

            logger.info(
                f"Google {operation} successful",
                token_type=token_response.token_type,
                expires_in=token_response.expires_in,
                has_refresh_token=bool(token_response.refresh_token),
                scope_count=len(token_response.scope.split()) if token_response.scope else 0,
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
            "access_denied": "Gmail access was denied. Please try connecting again and grant the required permissions.",
            "invalid_grant": "Authorization code expired or invalid. Please try connecting to Gmail again.",
            "invalid_client": "Gmail connection configuration error. Please contact support.",
            "invalid_request": "Invalid Gmail connection request. Please try again.",
            "unauthorized_client": "Gmail connection not authorized. Please contact support.",
            "unsupported_grant_type": "Gmail connection method not supported. Please contact support.",
            "invalid_scope": "Invalid Gmail permissions requested. Please contact support.",
        }

        return error_messages.get(
            error_code,
            f"Gmail connection failed ({error_code}). Please try again or contact support.",
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
                "scopes": GMAIL_SCOPES,
                "redirect_uri": self.redirect_uri,
            }

            # Test basic connectivity to Google (without making OAuth requests)
            try:
                response = requests.head(GOOGLE_OAUTH_BASE_URL, timeout=5)
                health_data["google_connectivity"] = (
                    "ok" if response.ok else f"error_{response.status_code}"
                )
            except requests.exceptions.RequestException as e:
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
    """Generate Google OAuth authorization URL."""
    return google_oauth_service.generate_oauth_url(state)


def exchange_oauth_code(authorization_code: str) -> TokenResponse:
    """Exchange authorization code for tokens."""
    return google_oauth_service.exchange_code_for_tokens(authorization_code)


def refresh_google_token(refresh_token: str) -> TokenResponse:
    """Refresh Google access token."""
    return google_oauth_service.refresh_access_token(refresh_token)


def revoke_google_token(token: str) -> bool:
    """Revoke Google OAuth token."""
    return google_oauth_service.revoke_token(token)


def google_oauth_health() -> dict[str, any]:
    """Check Google OAuth service health."""
    return google_oauth_service.health_check()
