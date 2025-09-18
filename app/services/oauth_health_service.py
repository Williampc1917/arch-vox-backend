"""
Enhanced health check functions for Gmail + Calendar OAuth system.
UPDATED: Now includes Calendar permission validation and comprehensive health monitoring.
"""

from datetime import datetime
from typing import Any

from app.infrastructure.observability.logging import get_logger
from app.models.domain.oauth_domain import OAuthToken

logger = get_logger(__name__)


async def validate_oauth_permissions(token: OAuthToken) -> dict[str, Any]:
    """
    Comprehensive validation of OAuth token permissions for Gmail + Calendar.

    Args:
        token: OAuth token to validate

    Returns:
        Dict: Detailed validation results
    """
    try:
        # Get token health status (includes permission validation)
        health_status = token.get_health_status()

        # Get detailed scope breakdown
        scope_breakdown = token.get_scope_breakdown()

        # Validate required permissions
        permission_validation = token.validate_required_permissions()

        # Check specific API access
        api_access = {
            "gmail": {
                "available": token.has_gmail_access(),
                "scopes": token.get_gmail_scopes(),
                "permissions": scope_breakdown["gmail"]["permissions"],
                "missing": permission_validation.get("missing_gmail_permissions", []),
            },
            "calendar": {
                "available": token.has_calendar_access(),
                "scopes": token.get_calendar_scopes(),
                "permissions": scope_breakdown["calendar"]["permissions"],
                "missing": permission_validation.get("missing_calendar_permissions", []),
            },
        }

        # Overall validation result
        validation_result = {
            "valid": permission_validation["valid"],
            "health_status": health_status,
            "api_access": api_access,
            "scope_breakdown": scope_breakdown,
            "permission_validation": permission_validation,
            "recommendations": _generate_permission_recommendations(
                permission_validation, health_status
            ),
        }

        logger.info(
            "OAuth permission validation completed",
            user_id=token.user_id,
            valid=validation_result["valid"],
            gmail_valid=permission_validation["gmail_valid"],
            calendar_valid=permission_validation["calendar_valid"],
            health_status=health_status["status"],
        )

        return validation_result

    except Exception as e:
        logger.error(
            "Error during OAuth permission validation",
            user_id=token.user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return {
            "valid": False,
            "error": str(e),
            "health_status": {"status": "error", "message": "Validation failed"},
            "api_access": {"gmail": {"available": False}, "calendar": {"available": False}},
        }


def _generate_permission_recommendations(
    permission_validation: dict, health_status: dict
) -> list[dict]:
    """Generate actionable recommendations based on permission validation."""
    recommendations = []

    # Permission-based recommendations
    if not permission_validation.get("gmail_valid", False):
        missing_gmail = permission_validation.get("missing_gmail_permissions", [])
        recommendations.append(
            {
                "priority": "high",
                "type": "missing_permissions",
                "service": "Gmail",
                "message": f"Missing Gmail permissions: {', '.join(missing_gmail)}",
                "action": "Re-authenticate to grant Gmail permissions",
                "user_action": "Go to Settings > Reconnect Gmail",
            }
        )

    if not permission_validation.get("calendar_valid", False):
        missing_calendar = permission_validation.get("missing_calendar_permissions", [])
        recommendations.append(
            {
                "priority": "high",
                "type": "missing_permissions",
                "service": "Calendar",
                "message": f"Missing Calendar permissions: {', '.join(missing_calendar)}",
                "action": "Re-authenticate to grant Calendar permissions",
                "user_action": "Go to Settings > Reconnect Google Services",
            }
        )

    # Health-based recommendations
    health_severity = health_status.get("severity", "none")
    if health_severity == "high":
        recommendations.append(
            {
                "priority": "high",
                "type": "token_health",
                "service": "OAuth",
                "message": health_status.get("message", "Token health issue"),
                "action": health_status.get("action_required", "Token refresh required"),
                "user_action": "Connection will be refreshed automatically",
            }
        )
    elif health_severity == "medium":
        recommendations.append(
            {
                "priority": "medium",
                "type": "token_health",
                "service": "OAuth",
                "message": health_status.get("message", "Token expiring soon"),
                "action": health_status.get("action_required", "Token refresh recommended"),
                "user_action": "No action needed - automatic refresh scheduled",
            }
        )

    return recommendations


async def check_gmail_calendar_health() -> dict[str, Any]:
    """
    Comprehensive health check for Gmail + Calendar OAuth system.

    Returns:
        Dict: System health status and metrics
    """
    try:
        health_data = {
            "healthy": True,
            "service": "gmail_calendar_oauth",
            "timestamp": datetime.utcnow().isoformat(),
            "components": {},
        }

        # Check Google OAuth service health
        try:
            from app.services.google_oauth_service import google_oauth_health

            oauth_health = google_oauth_health()
            health_data["components"]["google_oauth"] = oauth_health
            if not oauth_health.get("healthy", False):
                health_data["healthy"] = False
        except Exception as e:
            health_data["components"]["google_oauth"] = {"healthy": False, "error": str(e)}
            health_data["healthy"] = False

        # Check OAuth state service health
        try:
            from app.services.oauth_state_service import oauth_state_health

            state_health = await oauth_state_health()
            health_data["components"]["oauth_state"] = state_health
            if not state_health.get("healthy", False):
                health_data["healthy"] = False
        except Exception as e:
            health_data["components"]["oauth_state"] = {"healthy": False, "error": str(e)}
            health_data["healthy"] = False

        # Check token service health
        try:
            from app.services.token_service import token_service_health

            token_health = await token_service_health()
            health_data["components"]["token_service"] = token_health
            if not token_health.get("healthy", False):
                health_data["healthy"] = False
        except Exception as e:
            health_data["components"]["token_service"] = {"healthy": False, "error": str(e)}
            health_data["healthy"] = False

        # Check Gmail connection service health
        try:
            from app.services.gmail_auth_service import gmail_connection_health

            gmail_health = gmail_connection_health()
            health_data["components"]["gmail_connection"] = gmail_health
            if not gmail_health.get("healthy", False):
                health_data["healthy"] = False
        except Exception as e:
            health_data["components"]["gmail_connection"] = {"healthy": False, "error": str(e)}
            health_data["healthy"] = False

        # Add scope validation summary
        try:
            from app.services.google_oauth_service import GMAIL_CALENDAR_SCOPES

            health_data["scope_configuration"] = {
                "total_scopes": len(GMAIL_CALENDAR_SCOPES),
                "gmail_scopes": len([s for s in GMAIL_CALENDAR_SCOPES if "gmail" in s]),
                "calendar_scopes": len([s for s in GMAIL_CALENDAR_SCOPES if "calendar" in s]),
                "all_scopes": GMAIL_CALENDAR_SCOPES,
            }
        except Exception as e:
            health_data["scope_configuration"] = {"error": str(e)}

        # Overall health assessment
        component_health = [
            comp.get("healthy", False) for comp in health_data["components"].values()
        ]
        health_data["healthy"] = all(component_health) and len(component_health) > 0

        # Add summary
        healthy_components = sum(
            1 for comp in health_data["components"].values() if comp.get("healthy", False)
        )
        total_components = len(health_data["components"])

        health_data["summary"] = {
            "overall_healthy": health_data["healthy"],
            "healthy_components": healthy_components,
            "total_components": total_components,
            "health_percentage": (
                round((healthy_components / total_components * 100), 2)
                if total_components > 0
                else 0
            ),
        }

        if not health_data["healthy"]:
            failing_components = [
                name
                for name, comp in health_data["components"].items()
                if not comp.get("healthy", False)
            ]
            health_data["summary"]["failing_components"] = failing_components

        logger.info(
            "Gmail + Calendar OAuth health check completed",
            overall_healthy=health_data["healthy"],
            healthy_components=healthy_components,
            total_components=total_components,
        )

        return health_data

    except Exception as e:
        logger.error("Gmail + Calendar OAuth health check failed", error=str(e))
        return {
            "healthy": False,
            "service": "gmail_calendar_oauth",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


async def validate_user_oauth_setup(user_id: str) -> dict[str, Any]:
    """
    Validate complete OAuth setup for a specific user.

    Args:
        user_id: UUID string of the user

    Returns:
        Dict: User-specific OAuth validation results
    """
    try:
        from app.services.gmail_auth_service import get_gmail_status
        from app.services.token_service import get_oauth_tokens

        # Get user's OAuth tokens
        oauth_tokens = await get_oauth_tokens(user_id)
        if not oauth_tokens:
            return {
                "valid": False,
                "user_id": user_id,
                "error": "No OAuth tokens found",
                "recommendations": [
                    {
                        "priority": "high",
                        "type": "no_tokens",
                        "message": "No Gmail or Calendar access configured",
                        "action": "Connect Gmail and Calendar",
                        "user_action": "Go to Settings > Connect Google Services",
                    }
                ],
            }

        # Validate permissions
        permission_validation = await validate_oauth_permissions(oauth_tokens)

        # Get connection status
        connection_status = get_gmail_status(user_id)

        # Compile validation result
        validation_result = {
            "valid": permission_validation["valid"],
            "user_id": user_id,
            "connection_status": connection_status.to_dict(),
            "permission_validation": permission_validation,
            "token_health": permission_validation["health_status"],
            "recommendations": permission_validation["recommendations"],
            "services": {
                "gmail": {
                    "connected": connection_status.connected and oauth_tokens.has_gmail_access(),
                    "permissions_valid": permission_validation.get("gmail_valid", False),
                    "scopes": oauth_tokens.get_gmail_scopes(),
                },
                "calendar": {
                    "connected": connection_status.connected and oauth_tokens.has_calendar_access(),
                    "permissions_valid": permission_validation.get("calendar_valid", False),
                    "scopes": oauth_tokens.get_calendar_scopes(),
                },
            },
        }

        logger.info(
            "User OAuth setup validation completed",
            user_id=user_id,
            valid=validation_result["valid"],
            gmail_connected=validation_result["services"]["gmail"]["connected"],
            calendar_connected=validation_result["services"]["calendar"]["connected"],
        )

        return validation_result

    except Exception as e:
        logger.error(
            "Error validating user OAuth setup",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return {
            "valid": False,
            "user_id": user_id,
            "error": str(e),
            "recommendations": [
                {
                    "priority": "high",
                    "type": "validation_error",
                    "message": "Unable to validate OAuth setup",
                    "action": "Try reconnecting Google services",
                    "user_action": "Go to Settings > Reconnect Google Services",
                }
            ],
        }


async def get_oauth_system_metrics() -> dict[str, Any]:
    """
    Get comprehensive metrics for the OAuth system.

    Returns:
        Dict: System metrics and statistics
    """
    try:
        from app.services.user_service import get_user_service_health

        # Get user service health (includes OAuth metrics)
        user_health = await get_user_service_health()

        # Get system health
        system_health = await check_gmail_calendar_health()

        # Compile metrics
        metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "system_health": system_health,
            "user_metrics": user_health.get("user_metrics", {}),
            "gmail_health_metrics": user_health.get("gmail_health_metrics", {}),
            "oauth_scope_metrics": {
                "configured_scopes": system_health.get("scope_configuration", {}),
                "scope_validation": "All required Gmail + Calendar scopes configured",
            },
            "service_status": {
                comp_name: comp_data.get("healthy", False)
                for comp_name, comp_data in system_health.get("components", {}).items()
            },
        }

        logger.info(
            "OAuth system metrics compiled",
            system_healthy=system_health.get("healthy", False),
            total_users=user_health.get("user_metrics", {}).get("total_active_users", 0),
            gmail_connected=user_health.get("user_metrics", {}).get("gmail_connected_users", 0),
        )

        return metrics

    except Exception as e:
        logger.error("Error compiling OAuth system metrics", error=str(e))
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e),
            "system_health": {"healthy": False, "error": "Metrics compilation failed"},
        }
