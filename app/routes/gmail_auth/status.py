"""Gmail auth status and connection management routes."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.oauth_response import GmailAuthStatusResponse
from app.services.gmail.auth_service import (
    GmailConnectionError,
    disconnect_gmail,
    get_gmail_status,
    refresh_gmail_connection,
)

logger = get_logger(__name__)

router = APIRouter()


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
