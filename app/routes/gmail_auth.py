"""
Gmail Authentication Routes
HTTP endpoints for OAuth flow management and Gmail connection handling.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.auth.verify import auth_dependency
from app.config import settings
from app.features.vip_onboarding.services.scheduler import (
    VipSchedulerError,
    enqueue_vip_backfill_job,
)
from app.infrastructure.observability.logging import get_logger
from app.models.api.oauth_request import GmailAuthCallbackRequest
from app.models.api.oauth_response import (
    GmailAuthCallbackResponse,
    GmailAuthStatusResponse,
    GmailAuthURLResponse,
)
from app.services.gmail_auth_service import (
    GmailConnectionError,
    complete_gmail_oauth,
    disconnect_gmail,
    get_gmail_status,
    refresh_gmail_connection,
    start_gmail_oauth,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/gmail", tags=["gmail-auth"])


@router.get("/url", response_model=GmailAuthURLResponse)
async def get_oauth_url(claims: dict = Depends(auth_dependency)):
    """
    Generate Google OAuth authorization URL for Gmail connection.

    This endpoint initiates the OAuth flow by generating a secure authorization URL
    that the iOS app should open in a web view or external browser.

    Returns:
        GmailAuthURLResponse: OAuth URL and state parameter

    Raises:
        401: Invalid authentication token
        500: OAuth URL generation failed
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        logger.info("Generating Gmail OAuth URL", user_id=user_id)

        # Generate OAuth URL and state parameter
        oauth_url, state = await start_gmail_oauth(user_id)

        response = GmailAuthURLResponse(auth_url=oauth_url, state=state, total_scopes=6)

        logger.info(
            "Gmail OAuth URL generated successfully",
            user_id=user_id,
            state_preview=state[:8] + "...",
            url_length=len(oauth_url),
        )

        return response

    except GmailConnectionError as e:
        logger.error(
            "Gmail connection error during URL generation",
            user_id=user_id,
            error=str(e),
            error_code=getattr(e, "error_code", None),
        )

        # Map specific error codes to HTTP status codes
        if getattr(e, "error_code", None) == "config_error":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gmail service temporarily unavailable",
            ) from None
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate Gmail authorization URL",
            ) from None

    except Exception as e:
        logger.error(
            "Unexpected error during OAuth URL generation",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred"
        ) from None


@router.post("/callback", response_model=GmailAuthCallbackResponse)
async def oauth_callback(
    request: GmailAuthCallbackRequest, claims: dict = Depends(auth_dependency)
):
    """
    Handle OAuth callback and complete Gmail connection.

    This endpoint processes the authorization code from Google's OAuth callback
    and completes the connection process by exchanging the code for tokens.

    Args:
        request: OAuth callback request with code and state

    Returns:
        GmailAuthCallbackResponse: Connection success status and next steps

    Raises:
        400: Invalid callback data or OAuth flow failed
        401: Invalid authentication token or state validation failed
        500: Token storage or processing failed
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        logger.info(
            "Processing Gmail OAuth callback",
            user_id=user_id,
            code_preview=request.code[:12] + "...",
            state_preview=request.state[:8] + "...",
        )

        # Complete OAuth flow
        result = await complete_gmail_oauth(user_id=user_id, code=request.code, state=request.state)

        if result["success"]:
            # Check current onboarding status and advance accordingly
            onboarding_completed = result["onboarding_completed"]
            onboarding_step = result.get("onboarding_step", "completed")

            # Handle based on current onboarding step
            if onboarding_completed:
                # User already completed onboarding before - just confirm connection
                next_step = "redirect_to_main_app"
                message = "Gmail connected successfully! You can now use voice features to manage your email."

                response = GmailAuthCallbackResponse(
                    success=True,
                    message=message,
                    gmail_connected=True,
                    next_step=next_step,
                    onboarding_completed=True,
                )

            elif onboarding_step == "gmail":
                # User is on gmail step - advance to email_style step
                from app.services.onboarding_service import advance_to_email_style_step

                updated_profile = await advance_to_email_style_step(user_id)

                if updated_profile and updated_profile.onboarding_step == "email_style":
                    logger.info(
                        "User advanced to email_style step after Gmail connection",
                        user_id=user_id,
                        next_required_step="email_style_selection",
                    )

                    response = GmailAuthCallbackResponse(
                        success=True,
                        message="Gmail connected successfully! Please select your email style to continue.",
                        gmail_connected=True,
                        next_step="go_to_email_style_step",
                        onboarding_completed=False,
                    )
                else:
                    logger.warning(
                        "Failed to advance to email_style step after Gmail connection",
                        user_id=user_id,
                    )
                    response = GmailAuthCallbackResponse(
                        success=False,
                        message="Gmail connected but failed to advance onboarding. Please try again.",
                        gmail_connected=True,
                        next_step="stay_on_gmail",
                        onboarding_completed=False,
                    )

            elif onboarding_step == "profile":
                # User somehow connected Gmail before completing profile - unusual but handle it
                next_step = "go_to_profile_step"
                message = (
                    "Gmail connected successfully! Please complete your profile setup to continue."
                )

                response = GmailAuthCallbackResponse(
                    success=True,
                    message=message,
                    gmail_connected=True,
                    next_step=next_step,
                    onboarding_completed=False,
                )

            else:
                # Better fallback handling for unexpected states
                logger.warning(
                    "Unexpected onboarding step after Gmail connection",
                    user_id=user_id,
                    onboarding_step=onboarding_step,
                    onboarding_completed=onboarding_completed,
                )

                # Try to handle gracefully based on step
                if onboarding_step in ["email_style", "completed"]:
                    next_step = "redirect_to_main_app"
                    message = "Gmail connected successfully! You can now use voice features to manage your email."
                    response = GmailAuthCallbackResponse(
                        success=True,
                        message=message,
                        gmail_connected=True,
                        next_step=next_step,
                        onboarding_completed=onboarding_completed,
                    )
                else:
                    next_step = "stay_on_gmail"
                    message = "Gmail connected successfully! Please continue with your setup."
                    response = GmailAuthCallbackResponse(
                        success=True,
                        message=message,
                        gmail_connected=True,
                        next_step=next_step,
                        onboarding_completed=False,
                    )

            logger.info(
                "Gmail OAuth callback completed successfully",
                user_id=user_id,
                next_step=response.next_step,
                onboarding_completed=response.onboarding_completed,
            )

            if settings.VIP_BACKFILL_ENABLED:
                try:
                    await enqueue_vip_backfill_job(user_id, trigger_reason="gmail_connect")
                except VipSchedulerError as exc:
                    logger.warning(
                        "Failed to enqueue VIP backfill job",
                        user_id=user_id,
                        error=str(exc),
                    )

            return response
        else:
            # This shouldn't happen if complete_gmail_oauth doesn't raise an exception
            logger.error("Gmail OAuth callback returned false without exception", user_id=user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OAuth completion failed unexpectedly",
            )

    except GmailConnectionError as e:
        logger.error(
            "Gmail connection error during callback",
            user_id=user_id,
            error=str(e),
            error_code=getattr(e, "error_code", None),
        )

        # Map specific error codes to appropriate HTTP responses
        error_code = getattr(e, "error_code", None)

        if error_code == "invalid_state":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Security validation failed. Please try connecting Gmail again.",
            ) from None
        elif error_code in ["oauth_failed", "access_denied"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail authorization was denied or failed. Please try again and grant the required permissions.",
            ) from None
        elif error_code == "token_storage_failed":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save Gmail connection. Please try again.",
            ) from None
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Gmail connection failed: {str(e)}"
            ) from None

    except Exception as e:
        logger.error(
            "Unexpected error during OAuth callback",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while connecting Gmail",
        ) from None


@router.get("/status", response_model=GmailAuthStatusResponse)
async def get_connection_status(claims: dict = Depends(auth_dependency)):
    """
    Get current Gmail connection status for the authenticated user.

    Returns comprehensive information about the user's Gmail connection including
    connection health, token expiration, and refresh requirements.

    Returns:
        GmailAuthStatusResponse: Current Gmail connection status

    Raises:
        401: Invalid authentication token
        500: Status check failed
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        logger.debug("Getting Gmail connection status", user_id=user_id)

        # Get comprehensive connection status
        status_info = await get_gmail_status(user_id)

        response = GmailAuthStatusResponse(
            connected=status_info.connected,
            provider=status_info.provider,
            scope=status_info.scope,
            expires_at=status_info.expires_at,
            needs_refresh=status_info.needs_refresh,
        )

        logger.debug(
            "Gmail connection status retrieved",
            user_id=user_id,
            connected=status_info.connected,
            needs_refresh=status_info.needs_refresh,
            connection_health=status_info.connection_health,
        )

        return response

    except Exception as e:
        logger.error(
            "Error getting Gmail connection status",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check Gmail connection status",
        ) from None


@router.delete("/disconnect", response_model=dict)
async def disconnect_gmail_account(claims: dict = Depends(auth_dependency)):
    """
    Disconnect Gmail account and revoke all access tokens.

    This endpoint completely removes the Gmail connection by revoking tokens
    with Google and cleaning up all stored authentication data.

    Returns:
        dict: Disconnection success status and message

    Raises:
        401: Invalid authentication token
        500: Disconnection process failed
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        logger.info("Disconnecting Gmail account", user_id=user_id)

        # Disconnect Gmail and revoke tokens
        success = await disconnect_gmail(user_id)

        if success:
            logger.info("Gmail account disconnected successfully", user_id=user_id)

            return {
                "success": True,
                "message": "Gmail account disconnected successfully. You can reconnect anytime from the settings.",
                "gmail_connected": False,
            }
        else:
            logger.warning("Gmail disconnection returned false", user_id=user_id)

            # Even if disconnection had issues, return success since user intent was to disconnect
            return {
                "success": True,
                "message": "Gmail account disconnected. Some cleanup operations may have failed, but your account is no longer connected.",
                "gmail_connected": False,
            }

    except Exception as e:
        logger.error(
            "Error during Gmail disconnection",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disconnect Gmail account. Please try again.",
        ) from None


@router.post("/refresh", response_model=dict)
async def refresh_connection(claims: dict = Depends(auth_dependency)):
    """
    Manually refresh Gmail connection tokens.

    This endpoint forces a refresh of the Gmail access tokens, which can be useful
    if the connection status shows that tokens need refreshing or if API calls are failing.

    Returns:
        dict: Refresh success status and updated connection info

    Raises:
        400: No Gmail connection found or refresh failed
        401: Invalid authentication token
        500: Token refresh process failed
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        logger.info("Manually refreshing Gmail connection", user_id=user_id)

        # Attempt to refresh the connection
        success = await refresh_gmail_connection(user_id)

        if success:
            # Get updated status after refresh
            updated_status = await get_gmail_status(user_id)

            logger.info("Gmail connection refreshed successfully", user_id=user_id)

            return {
                "success": True,
                "message": "Gmail connection refreshed successfully.",
                "connection_status": {
                    "connected": updated_status.connected,
                    "expires_at": (
                        updated_status.expires_at.isoformat() if updated_status.expires_at else None
                    ),
                    "needs_refresh": updated_status.needs_refresh,
                    "connection_health": updated_status.connection_health,
                },
            }
        else:
            logger.warning("Gmail connection refresh failed", user_id=user_id)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to refresh Gmail connection. You may need to reconnect your Gmail account.",
            )

    except GmailConnectionError as e:
        logger.error(
            "Gmail connection error during refresh",
            user_id=user_id,
            error=str(e),
            error_code=getattr(e, "error_code", None),
        )

        error_code = getattr(e, "error_code", None)

        if error_code == "refresh_failed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail token refresh failed. Please reconnect your Gmail account.",
            ) from None
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Gmail connection refresh failed: {str(e)}",
            ) from None

    except Exception as e:
        logger.error(
            "Unexpected error during Gmail connection refresh",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while refreshing Gmail connection",
        ) from None


# Health check endpoint for Gmail auth system
@router.get("/health")
def gmail_auth_health():
    """
    Health check for Gmail authentication system.

    This endpoint checks the health of all Gmail auth-related services
    and returns their status. No authentication required.

    Returns:
        dict: Health status of Gmail auth components
    """
    try:
        from app.services.gmail_auth_service import gmail_connection_health

        health_status = gmail_connection_health()

        return {
            "service": "gmail_auth",
            "healthy": health_status.get("healthy", False),
            "timestamp": health_status.get("timestamp"),
            "components": health_status,
            "endpoints": [
                "GET /auth/gmail/url",
                "POST /auth/gmail/callback",
                "GET /auth/gmail/status",
                "DELETE /auth/gmail/disconnect",
                "POST /auth/gmail/refresh",
            ],
        }

    except Exception as e:
        logger.error("Gmail auth health check failed", error=str(e))
        return {
            "service": "gmail_auth",
            "healthy": False,
            "error": str(e),
            "endpoints": [
                "GET /auth/gmail/url",
                "POST /auth/gmail/callback",
                "GET /auth/gmail/status",
                "DELETE /auth/gmail/disconnect",
                "POST /auth/gmail/refresh",
            ],
        }


# Additional utility endpoint for debugging (remove in production)
@router.get("/debug/connection-metrics")
async def get_connection_metrics(claims: dict = Depends(auth_dependency)):
    """
    Get Gmail connection metrics for debugging and monitoring.

    This endpoint provides detailed metrics about Gmail connections
    across all users. Should be removed or restricted in production.

    Returns:
        dict: Connection metrics and statistics

    Raises:
        401: Invalid authentication token
        500: Metrics retrieval failed
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        from app.services.gmail_auth_service import gmail_connection_service

        metrics = gmail_connection_service.get_connection_metrics()

        logger.info("Gmail connection metrics requested", user_id=user_id)

        return {
            "service": "gmail_auth_metrics",
            "timestamp": metrics.get("timestamp"),
            "metrics": metrics,
        }

    except Exception as e:
        logger.error("Error getting Gmail connection metrics", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve connection metrics",
        ) from None

    # Add this NEW endpoint ABOVE your existing POST /callback endpoint


# FIXED GET callback endpoint - replace in app/routes/gmail_auth.py

# TEMPORARY DEBUG VERSION of the GET callback endpoint
# Replace this in app/routes/gmail_auth.py to debug Redis issue

# Production version - replace the debug GET callback in app/routes/gmail_auth.py


@router.get("/callback")
async def oauth_callback_redirect(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="OAuth state parameter"),
    error: str = Query(None, description="OAuth error if any"),
):
    """Handle OAuth callback redirect from Google (GET request)."""

    if error:
        logger.warning(
            "OAuth callback received error", error=error, state_preview=state[:8] + "..."
        )

        return HTMLResponse(
            content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Gmail Connection Failed</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #f5f5f7;">
            <div style="max-width: 400px; margin: 0 auto; background: white; padding: 40px; border-radius: 12px;">
                <h2 style="color: #ff3b30;">Gmail Connection Failed</h2>
                <p>Error: {error}</p>
                <p>Please return to your app and try connecting Gmail again.</p>
                <button onclick="window.close()" style="background: #007AFF; color: white; padding: 12px 24px; border-radius: 8px; border: none;">
                    Close Window
                </button>
            </div>
        </body>
        </html>
        """,
            status_code=400,
        )

    try:
        logger.info(
            "Processing Gmail OAuth callback redirect",
            code_preview=code[:12] + "...",
            state_preview=state[:8] + "...",
        )

        # Store OAuth data in Redis using fast client
        import json
        from datetime import datetime

        from app.services.redis_store import set_with_ttl

        oauth_data = {
            "code": code,
            "state": state,
            "timestamp": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow().timestamp() + 300),
        }

        oauth_data_json = json.dumps(oauth_data)
        redis_key = f"oauth_callback_data:{state}"

        # Use fast Redis client (now 1ms instead of 25ms)
        success = await set_with_ttl(redis_key, oauth_data_json, 300)

        if not success:
            logger.error("Failed to store OAuth data in Redis", state_preview=state[:8] + "...")
            raise Exception("Failed to store OAuth data")

        logger.info(
            "OAuth callback data stored successfully",
            state_preview=state[:8] + "...",
            redis_key_preview=redis_key[:30] + "...",
        )

        return HTMLResponse(
            content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Gmail Connected Successfully!</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                    margin: 0; padding: 40px; background: #f5f5f7; text-align: center;
                }
                .container {
                    max-width: 400px; margin: 0 auto; background: white;
                    padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                }
                .success { color: #34c759; margin: 20px 0; }
                .button {
                    background: #007AFF; color: white; padding: 12px 24px;
                    border-radius: 8px; border: none; cursor: pointer;
                }
                .highlight { background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 20px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2 class="success">✅ Gmail Connected Successfully!</h2>
                <div class="highlight">
                    <p><strong>Please return to your mobile app to continue.</strong></p>
                    <p>Your Gmail connection is being processed...</p>
                </div>

                <button class="button" onclick="window.close()">Close Window</button>

                <div style="margin-top: 30px; font-size: 14px; color: #8e8e93;">
                    <p>This window will close automatically in <span id="countdown">10</span> seconds.</p>
                </div>
            </div>
            <script>
                let countdown = 10;
                const countdownEl = document.getElementById('countdown');

                const timer = setInterval(() => {
                    countdown--;
                    countdownEl.textContent = countdown;

                    if (countdown <= 0) {
                        clearInterval(timer);
                        window.close();
                    }
                }, 1000);
            </script>
        </body>
        </html>
        """
        )

    except Exception as e:
        logger.error(
            "Error processing OAuth callback redirect",
            code_preview=code[:12] + "...",
            state_preview=state[:8] + "...",
            error=str(e),
            error_type=type(e).__name__,
        )

        return HTMLResponse(
            content="""
        <!DOCTYPE html>
        <html>
        <head><title>Connection Error</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 40px;">
            <h2 style="color: #ff3b30;">⚠️ Connection Error</h2>
            <p>An error occurred while processing your Gmail connection.</p>
            <p><strong>Please return to your app and try again.</strong></p>
            <button onclick="window.close()" style="background: #007AFF; color: white; padding: 12px 24px; border-radius: 8px; border: none;">
                Close Window
            </button>
        </body>
        </html>
        """,
            status_code=500,
        )


# FIXED retrieve endpoint with fast Redis
@router.get("/callback/retrieve/{state}")
async def retrieve_oauth_data(state: str, claims: dict = Depends(auth_dependency)):
    """Retrieve OAuth callback data by state parameter."""

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        import json

        from app.services.redis_store import get

        redis_key = f"oauth_callback_data:{state}"

        # Use fast Redis client (now 1ms instead of 25ms)
        redis_response = await get(redis_key)

        if not redis_response:
            logger.warning(
                "OAuth callback data not found",
                user_id=user_id,
                state_preview=state[:8] + "...",
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="OAuth callback data not found or expired. Please try connecting Gmail again.",
            )

        # With fast Redis client, we get the value directly (no Upstash wrapper)
        # The fast Redis client handles the response format internally
        oauth_data_json = redis_response

        # Parse the JSON string into a Python dict
        if isinstance(oauth_data_json, str):
            oauth_data = json.loads(oauth_data_json)
        else:
            # If it's already a dict, use it directly
            oauth_data = oauth_data_json

        # Verify we have the required fields
        if not isinstance(oauth_data, dict) or "code" not in oauth_data:
            logger.error(
                "OAuth data missing required fields",
                user_id=user_id,
                state_preview=state[:8] + "...",
                has_code=oauth_data.get("code") if isinstance(oauth_data, dict) else False,
                data_type=type(oauth_data).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid OAuth data format. Please try connecting Gmail again.",
            )

        logger.info(
            "OAuth callback data retrieved successfully",
            user_id=user_id,
            state_preview=state[:8] + "...",
            has_code=bool(oauth_data.get("code")),
        )

        return {
            "success": True,
            "code": oauth_data["code"],
            "state": oauth_data["state"],
            "timestamp": oauth_data["timestamp"],
            "message": "OAuth data retrieved successfully.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error retrieving OAuth callback data",
            user_id=user_id,
            state_preview=state[:8] + "...",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve OAuth callback data.",
        ) from None
