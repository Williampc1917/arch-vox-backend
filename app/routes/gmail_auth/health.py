"""Gmail auth health check routes."""

from fastapi import APIRouter

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health")
def gmail_auth_health():
    """
    Health check for Gmail authentication system.

    This endpoint checks the health of all Gmail auth-related services
    and returns their status. No authentication required.
    """
    try:
        from app.services.gmail.auth_service import gmail_connection_health

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
