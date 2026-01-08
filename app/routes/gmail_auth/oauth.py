"""
Gmail OAuth routes for connection flow.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.verify import auth_dependency
from app.config import settings
from app.features.vip_onboarding.services.scheduler import (
    VipSchedulerError,
    enqueue_vip_backfill_job,
)
from app.infrastructure.observability.logging import get_logger
from app.models.api.oauth_request import GmailAuthCallbackRequest
from app.models.api.oauth_response import GmailAuthCallbackResponse, GmailAuthURLResponse
from app.services.gmail.auth_service import (
    GmailConnectionError,
    complete_gmail_oauth,
    start_gmail_oauth,
)
from app.services.infrastructure.oauth_state_service import get_oauth_state_owner
from app.services.infrastructure.redis_client import fast_redis

logger = get_logger(__name__)

router = APIRouter()

_CALLBACK_HTML_PATH = Path(__file__).resolve().parents[2] / "static" / "oauth_callback.html"
_OAUTH_SUCCESS_URL = "https://try-claroai.com/gmail-connected/"
_OAUTH_FAILURE_URL = "https://try-claroai.com/gmail-connection-failed/"


def _load_oauth_callback_html() -> str:
    """Load the static OAuth callback HTML page."""
    try:
        return _CALLBACK_HTML_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"/>'
            "<title>Gmail Connection</title></head>"
            '<body style="font-family:sans-serif;text-align:center;padding:40px;">'
            "<h2>Gmail connection complete</h2><p>Return to the app to continue.</p></body></html>"
        )


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
                from app.services.core.onboarding_service import advance_to_email_style_step

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
        elif error_code in [
            "oauth_failed",
            "access_denied",
            "missing_gmail_scopes",
            "missing_calendar_scopes",
            "missing_required_scopes",
        ]:
            return GmailAuthCallbackResponse(
                success=False,
                message=(
                    "Gmail + Calendar access is required. "
                    "Please allow both permissions to continue."
                ),
                gmail_connected=False,
                next_step="stay_on_gmail",
                onboarding_completed=False,
            )
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

        if settings.environment == "production":
            return RedirectResponse(_OAUTH_FAILURE_URL, status_code=302)
        return HTMLResponse(content=_load_oauth_callback_html(), status_code=400)

    try:
        logger.info(
            "Processing Gmail OAuth callback redirect",
            state_preview=state[:8] + "...",
        )

        state_owner = await get_oauth_state_owner(state)
        if not state_owner:
            logger.warning(
                "OAuth callback state not recognized",
                state_preview=state[:8] + "...",
            )
            if settings.environment == "production":
                return RedirectResponse(_OAUTH_FAILURE_URL, status_code=302)
            return HTMLResponse(content=_load_oauth_callback_html(), status_code=400)

        # Store OAuth data in Redis using fast client
        import json
        from datetime import datetime

        oauth_data = {
            "code": code,
            "state": state,
            "user_id": state_owner,
            "timestamp": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow().timestamp() + 300),
        }

        oauth_data_json = json.dumps(oauth_data)
        redis_key = f"oauth_callback_data:{state}"

        # Use fast Redis client (now 1ms instead of 25ms)
        success = await fast_redis.set_with_ttl(redis_key, oauth_data_json, 300)

        if not success:
            logger.error("Failed to store OAuth data in Redis", state_preview=state[:8] + "...")
            raise Exception("Failed to store OAuth data")

        logger.info(
            "OAuth callback data stored successfully",
            state_preview=state[:8] + "...",
            redis_key_preview=redis_key[:30] + "...",
        )

        if settings.environment == "production":
            return RedirectResponse(_OAUTH_SUCCESS_URL, status_code=302)
        return HTMLResponse(content=_load_oauth_callback_html())

    except Exception as e:
        logger.error(
            "Error processing OAuth callback redirect",
            state_preview=state[:8] + "...",
            error=str(e),
            error_type=type(e).__name__,
        )

        if settings.environment == "production":
            return RedirectResponse(_OAUTH_FAILURE_URL, status_code=302)
        return HTMLResponse(content=_load_oauth_callback_html(), status_code=500)


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

        redis_key = f"oauth_callback_data:{state}"

        # Use fast Redis client (now 1ms instead of 25ms)
        redis_response = await fast_redis.get(redis_key)

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

        stored_user_id = oauth_data.get("user_id")
        if not stored_user_id:
            stored_user_id = await get_oauth_state_owner(state)

        if not stored_user_id:
            logger.warning(
                "OAuth callback data missing owner",
                user_id=user_id,
                state_preview=state[:8] + "...",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OAuth callback data does not belong to this user.",
            )

        if stored_user_id and stored_user_id != user_id:
            logger.warning(
                "OAuth callback data user mismatch",
                user_id=user_id,
                state_preview=state[:8] + "...",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OAuth callback data does not belong to this user.",
            )

        logger.info(
            "OAuth callback data retrieved successfully",
            user_id=user_id,
            state_preview=state[:8] + "...",
            has_code=bool(oauth_data.get("code")),
        )

        await fast_redis.delete(redis_key)

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
