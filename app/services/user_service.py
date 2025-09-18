"""
User service for database operations.
Handles fetching user profile data from the database with Gmail connection health.
REFACTORED: Now uses database connection pool instead of direct psycopg connections.
"""

from datetime import UTC, datetime

from app.db.helpers import DatabaseError, fetch_all, fetch_one, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.models.domain.user_domain import Plan, UserProfile

logger = get_logger(__name__)


@with_db_retry(max_retries=3, base_delay=0.1)
async def get_user_profile(user_id: str) -> UserProfile | None:
    """
    Fetch complete user profile (user + settings + plan + Gmail health) from database.

    Args:
        user_id: UUID string of the user

    Returns:
        UserProfile domain model with Gmail connection health, None if not found
    """
    query = """
    SELECT
        u.id, u.email, u.display_name, u.is_active,
        u.timezone, u.onboarding_completed, u.gmail_connected, u.onboarding_step,
        u.calendar_connected,
        u.created_at, u.updated_at,
        us.voice_preferences,
        p.name as plan_name, p.max_daily_requests,
        -- Gmail token information for health assessment
        ot.expires_at as token_expires_at,
        ot.refresh_failure_count,
        ot.last_refresh_attempt,
        ot.updated_at as token_updated_at
    FROM users u
    LEFT JOIN user_settings us ON u.id = us.user_id
    LEFT JOIN user_subscriptions sub ON u.id = sub.user_id
    LEFT JOIN plans p ON sub.plan_name = p.name
    LEFT JOIN oauth_tokens ot ON u.id = ot.user_id AND ot.provider = 'google'
    WHERE u.id = %s AND u.is_active = true
    """

    try:
        # Use database pool helper function instead of direct connection
        row = await fetch_one(query, (user_id,))

        if not row:
            logger.info("User not found or inactive", user_id=user_id)
            return None

        # Unpack row data including Gmail token information
        row_values = list(row.values())
        (
            id_val,
            email,
            display_name,
            is_active,
            timezone,
            onboarding_completed,
            gmail_connected,
            onboarding_step,
            created_at,
            updated_at,
            voice_preferences,
            plan_name,
            max_daily_requests,
            token_expires_at,
            refresh_failure_count,
            last_refresh_attempt,
            token_updated_at,
        ) = row_values

        # Build domain objects
        plan = Plan(
            name=plan_name or "free",
            max_daily_requests=max_daily_requests or 100,
        )

        # Create enhanced user profile with Gmail health
        profile = UserProfile(
            user_id=str(id_val),
            email=email,
            display_name=display_name,
            is_active=is_active,
            timezone=timezone,
            onboarding_completed=onboarding_completed,
            gmail_connected=gmail_connected,
            onboarding_step=onboarding_step,
            voice_preferences=voice_preferences or {"tone": "professional", "speed": "normal"},
            plan=plan,
            created_at=created_at,
            updated_at=updated_at,
        )

        # Add Gmail connection health information
        profile = await _enhance_profile_with_gmail_health(
            profile,
            token_expires_at,
            refresh_failure_count,
            last_refresh_attempt,
            token_updated_at,
        )

        logger.info(
            "User profile retrieved with Gmail health",
            user_id=user_id,
            plan=plan.name,
            gmail_connected=gmail_connected,
            gmail_health=getattr(profile, "gmail_connection_health", "unknown"),
        )

        return profile

    except DatabaseError as e:
        logger.error("Database error retrieving user profile", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error retrieving user profile", user_id=user_id, error=str(e))
        return None


async def _enhance_profile_with_gmail_health(
    profile: UserProfile,
    token_expires_at: datetime | None,
    refresh_failure_count: int | None,
    last_refresh_attempt: datetime | None,
    token_updated_at: datetime | None,
) -> UserProfile:
    """
    Enhance user profile with Gmail connection health information.

    Args:
        profile: Base user profile
        token_expires_at: When Gmail tokens expire
        refresh_failure_count: Number of consecutive refresh failures
        last_refresh_attempt: Last time token refresh was attempted
        token_updated_at: When tokens were last updated

    Returns:
        Enhanced UserProfile with Gmail health attributes
    """
    try:
        # Determine Gmail connection health
        gmail_health = await _assess_gmail_connection_health(
            profile.gmail_connected,
            token_expires_at,
            refresh_failure_count,
            last_refresh_attempt,
            token_updated_at,
        )

        # Add Gmail health attributes to profile (dynamic attributes)
        profile.gmail_connection_health = gmail_health["status"]
        profile.gmail_health_details = gmail_health["details"]
        profile.gmail_needs_attention = gmail_health["needs_attention"]
        profile.gmail_token_expires_at = token_expires_at
        profile.gmail_last_refresh_attempt = last_refresh_attempt

        # Calculate if tokens need refresh soon (within 1 hour)
        profile.gmail_needs_refresh = False
        if token_expires_at and profile.gmail_connected:
            # FIXED: Ensure both datetimes have timezone info for comparison

            now = datetime.now(UTC)

            # Ensure token_expires_at has timezone info
            if token_expires_at.tzinfo is None:
                # Assume UTC if no timezone info
                token_expires_at = token_expires_at.replace(tzinfo=UTC)
            elif token_expires_at.tzinfo != UTC:
                # Convert to UTC if different timezone
                token_expires_at = token_expires_at.astimezone(UTC)

            time_until_expiry = token_expires_at - now
            profile.gmail_needs_refresh = time_until_expiry.total_seconds() < 3600  # 1 hour

        return profile

    except Exception as e:
        logger.warning(
            "Error enhancing profile with Gmail health", user_id=profile.user_id, error=str(e)
        )

        # Return profile with default health values
        profile.gmail_connection_health = "unknown"
        profile.gmail_health_details = {"error": "Unable to assess Gmail health"}
        profile.gmail_needs_attention = False
        profile.gmail_token_expires_at = token_expires_at
        profile.gmail_last_refresh_attempt = last_refresh_attempt
        profile.gmail_needs_refresh = False

        return profile


async def _assess_gmail_connection_health(
    gmail_connected: bool,
    token_expires_at: datetime | None,
    refresh_failure_count: int | None,
    last_refresh_attempt: datetime | None,
    token_updated_at: datetime | None,
) -> dict:
    """
    Assess Gmail connection health based on token status.

    Returns:
        dict: Health assessment with status, details, and action requirements
    """
    try:
        # User not connected to Gmail
        if not gmail_connected:
            return {
                "status": "disconnected",
                "details": {
                    "message": "Gmail not connected",
                    "action_required": "Connect Gmail account",
                },
                "needs_attention": True,
            }

        # Connected but no token information (inconsistent state)
        if not token_expires_at:
            return {
                "status": "invalid",
                "details": {
                    "message": "Gmail marked as connected but no tokens found",
                    "action_required": "Reconnect Gmail account",
                },
                "needs_attention": True,
            }

        # FIXED: Ensure both datetimes have timezone info for comparison

        # Convert current time to UTC with timezone info
        now = datetime.now(UTC)

        # Ensure token_expires_at has timezone info
        if token_expires_at.tzinfo is None:
            # Assume UTC if no timezone info
            token_expires_at = token_expires_at.replace(tzinfo=UTC)
        elif token_expires_at.tzinfo != UTC:
            # Convert to UTC if different timezone
            token_expires_at = token_expires_at.astimezone(UTC)

        # Check for excessive refresh failures
        if refresh_failure_count and refresh_failure_count >= 3:
            return {
                "status": "failing",
                "details": {
                    "message": f"Gmail connection failing ({refresh_failure_count} consecutive failures)",
                    "action_required": "Reconnect Gmail account",
                    "last_attempt": (
                        last_refresh_attempt.isoformat() if last_refresh_attempt else None
                    ),
                },
                "needs_attention": True,
            }

        # Check token expiration status
        time_until_expiry = token_expires_at - now

        if time_until_expiry.total_seconds() < 0:
            # Token is expired
            return {
                "status": "expired",
                "details": {
                    "message": "Gmail tokens expired",
                    "expired_since": abs(time_until_expiry.total_seconds() / 3600),  # Hours ago
                    "action_required": "Tokens will be refreshed automatically",
                },
                "needs_attention": refresh_failure_count and refresh_failure_count > 0,
            }

        elif time_until_expiry.total_seconds() < 3600:  # Less than 1 hour
            # Token expires soon
            return {
                "status": "expiring_soon",
                "details": {
                    "message": "Gmail tokens expire soon",
                    "expires_in_minutes": int(time_until_expiry.total_seconds() / 60),
                    "action_required": "Tokens will be refreshed automatically",
                },
                "needs_attention": False,
            }

        elif time_until_expiry.total_seconds() < 7200:  # Less than 2 hours
            # Token expires within 2 hours
            return {
                "status": "refresh_scheduled",
                "details": {
                    "message": "Gmail tokens will be refreshed soon",
                    "expires_in_hours": round(time_until_expiry.total_seconds() / 3600, 1),
                    "action_required": None,
                },
                "needs_attention": False,
            }

        else:
            # Token is healthy
            return {
                "status": "healthy",
                "details": {
                    "message": "Gmail connection is healthy",
                    "expires_in_hours": round(time_until_expiry.total_seconds() / 3600, 1),
                    "action_required": None,
                    "last_updated": token_updated_at.isoformat() if token_updated_at else None,
                },
                "needs_attention": False,
            }

    except Exception as e:
        logger.error("Error assessing Gmail connection health", error=str(e))
        return {
            "status": "unknown",
            "details": {"message": "Unable to assess Gmail connection health", "error": str(e)},
            "needs_attention": True,
        }


async def get_user_gmail_summary(user_id: str) -> dict:
    """
    Get comprehensive Gmail connection summary for user.

    Args:
        user_id: UUID string of the user

    Returns:
        dict: Gmail connection summary with health and recommendations
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            return {"user_found": False, "error": "User not found"}

        gmail_summary = {
            "user_found": True,
            "gmail_connected": profile.gmail_connected,
            "connection_health": getattr(profile, "gmail_connection_health", "unknown"),
            "needs_attention": getattr(profile, "gmail_needs_attention", False),
            "needs_refresh": getattr(profile, "gmail_needs_refresh", False),
            "health_details": getattr(profile, "gmail_health_details", {}),
            "token_expires_at": getattr(profile, "gmail_token_expires_at", None),
            "last_refresh_attempt": getattr(profile, "gmail_last_refresh_attempt", None),
            "onboarding_context": {
                "onboarding_step": profile.onboarding_step,
                "onboarding_completed": profile.onboarding_completed,
                "can_complete_onboarding": profile.onboarding_step == "gmail"
                and profile.gmail_connected,
            },
        }

        # Add recommendations based on health status
        gmail_summary["recommendations"] = _get_gmail_recommendations(gmail_summary)

        logger.debug(
            "Gmail summary generated",
            user_id=user_id,
            connection_health=gmail_summary["connection_health"],
            needs_attention=gmail_summary["needs_attention"],
        )

        return gmail_summary

    except Exception as e:
        logger.error("Error generating Gmail summary", user_id=user_id, error=str(e))
        return {"user_found": False, "error": f"Error generating summary: {str(e)}"}


def _get_gmail_recommendations(gmail_summary: dict) -> list:
    """Get actionable recommendations based on Gmail health status."""
    recommendations = []

    health_status = gmail_summary.get("connection_health", "unknown")
    needs_attention = gmail_summary.get("needs_attention", False)
    onboarding = gmail_summary.get("onboarding_context", {})

    if not gmail_summary.get("gmail_connected", False):
        if onboarding.get("onboarding_step") == "gmail":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "connect_gmail",
                    "message": "Connect your Gmail account to continue onboarding",
                    "user_action": "Tap 'Connect Gmail' button",
                }
            )
        else:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "connect_gmail",
                    "message": "Connect Gmail to enable voice email features",
                    "user_action": "Go to Settings > Connect Gmail",
                }
            )

    elif health_status in ["invalid", "failing"]:
        recommendations.append(
            {
                "priority": "high",
                "action": "reconnect_gmail",
                "message": "Gmail connection needs to be refreshed",
                "user_action": "Go to Settings > Reconnect Gmail",
            }
        )

    elif health_status == "expired" and needs_attention:
        recommendations.append(
            {
                "priority": "medium",
                "action": "wait_for_refresh",
                "message": "Gmail tokens will be refreshed automatically",
                "user_action": "No action needed - refresh in progress",
            }
        )

    elif onboarding.get("can_complete_onboarding"):
        recommendations.append(
            {
                "priority": "high",
                "action": "complete_onboarding",
                "message": "Complete your onboarding to start using voice features",
                "user_action": "Tap 'Complete Setup' button",
            }
        )

    elif health_status == "healthy" and not onboarding.get("onboarding_completed"):
        recommendations.append(
            {
                "priority": "low",
                "action": "explore_features",
                "message": "Gmail connected successfully - explore voice email features",
                "user_action": "Try saying 'What's new in my inbox?'",
            }
        )

    return recommendations


@with_db_retry(max_retries=3, base_delay=0.1)
async def get_users_needing_gmail_attention() -> list:
    """
    Get list of users whose Gmail connections need attention.

    Returns:
        list: Users with Gmail connection issues
    """
    try:
        query = """
        SELECT DISTINCT u.id, u.email, u.display_name, u.gmail_connected,
               u.onboarding_step, u.onboarding_completed,
               ot.expires_at, ot.refresh_failure_count
        FROM users u
        LEFT JOIN oauth_tokens ot ON u.id = ot.user_id AND ot.provider = 'google'
        WHERE u.is_active = true
        AND (
            -- Users marked as connected but no tokens
            (u.gmail_connected = true AND ot.user_id IS NULL)
            OR
            -- Users with failing tokens
            (ot.refresh_failure_count >= 3)
            OR
            -- Users with expired tokens and failures
            (ot.expires_at < NOW() AND ot.refresh_failure_count > 0)
        )
        ORDER BY u.updated_at DESC
        LIMIT 100
        """

        # Use database pool helper function
        rows = await fetch_all(query)

        users_needing_attention = []
        for row in rows:
            row_values = list(row.values())
            (
                user_id,
                email,
                display_name,
                gmail_connected,
                onboarding_step,
                onboarding_completed,
                expires_at,
                failure_count,
            ) = row_values

            # Determine issue type
            issue_type = "unknown"
            if gmail_connected and not expires_at:
                issue_type = "missing_tokens"
            elif failure_count and failure_count >= 3:
                issue_type = "refresh_failures"
            elif (
                expires_at
                and expires_at < datetime.now(UTC)
                and failure_count
                and failure_count > 0
            ):
                issue_type = "expired_with_failures"

            users_needing_attention.append(
                {
                    "user_id": str(user_id),
                    "email": email,
                    "display_name": display_name,
                    "gmail_connected": gmail_connected,
                    "onboarding_step": onboarding_step,
                    "onboarding_completed": onboarding_completed,
                    "issue_type": issue_type,
                    "token_expires_at": expires_at.isoformat() if expires_at else None,
                    "refresh_failure_count": failure_count or 0,
                }
            )

        logger.info("Found users needing Gmail attention", count=len(users_needing_attention))

        return users_needing_attention

    except DatabaseError as e:
        logger.error("Database error finding users needing Gmail attention", error=str(e))
        return []
    except Exception as e:
        logger.error("Error finding users needing Gmail attention", error=str(e))
        return []


@with_db_retry(max_retries=3, base_delay=0.1)
async def get_user_service_health() -> dict:
    """
    Get user service health status including Gmail connection statistics.

    Returns:
        dict: Service health and Gmail connection metrics
    """
    try:
        query = """
        SELECT
            COUNT(*) as total_active_users,
            COUNT(CASE WHEN gmail_connected = true THEN 1 END) as gmail_connected_users,
            COUNT(CASE WHEN onboarding_completed = true THEN 1 END) as completed_onboarding,
            COUNT(CASE WHEN onboarding_step = 'gmail' AND gmail_connected = false THEN 1 END) as stuck_on_gmail,
            -- Token health stats
            COUNT(CASE WHEN ot.user_id IS NOT NULL THEN 1 END) as users_with_tokens,
            COUNT(CASE WHEN ot.expires_at > NOW() THEN 1 END) as users_with_valid_tokens,
            COUNT(CASE WHEN ot.refresh_failure_count >= 3 THEN 1 END) as users_with_failing_tokens,
            AVG(CASE WHEN ot.refresh_failure_count IS NOT NULL THEN ot.refresh_failure_count ELSE 0 END) as avg_failure_count
        FROM users u
        LEFT JOIN oauth_tokens ot ON u.id = ot.user_id AND ot.provider = 'google'
        WHERE u.is_active = true
        """

        # Use database pool helper function
        row = await fetch_one(query)

        if row:
            row_values = list(row.values())
            (
                total_users,
                gmail_connected,
                completed,
                stuck_on_gmail,
                with_tokens,
                with_valid_tokens,
                with_failing_tokens,
                avg_failures,
            ) = row_values

            # Calculate health metrics
            gmail_connection_rate = (gmail_connected / total_users * 100) if total_users > 0 else 0
            onboarding_completion_rate = (completed / total_users * 100) if total_users > 0 else 0
            token_consistency_rate = (
                (with_tokens / gmail_connected * 100) if gmail_connected > 0 else 100
            )
            token_health_rate = (with_valid_tokens / with_tokens * 100) if with_tokens > 0 else 100

            health_data = {
                "healthy": True,
                "service": "user_service",
                "database_connectivity": "ok",
                "user_metrics": {
                    "total_active_users": total_users,
                    "gmail_connected_users": gmail_connected,
                    "completed_onboarding": completed,
                    "stuck_on_gmail_step": stuck_on_gmail,
                    "gmail_connection_rate_percent": round(gmail_connection_rate, 2),
                    "onboarding_completion_rate_percent": round(onboarding_completion_rate, 2),
                },
                "gmail_health_metrics": {
                    "users_with_tokens": with_tokens,
                    "users_with_valid_tokens": with_valid_tokens,
                    "users_with_failing_tokens": with_failing_tokens,
                    "token_consistency_rate_percent": round(token_consistency_rate, 2),
                    "token_health_rate_percent": round(token_health_rate, 2),
                    "average_failure_count": round(float(avg_failures or 0), 2),
                },
            }

            # Mark as unhealthy if critical metrics are poor
            if (
                gmail_connection_rate < 50
                or token_consistency_rate < 90
                or token_health_rate < 80
                or with_failing_tokens > total_users * 0.1
            ):  # More than 10% failing
                health_data["healthy"] = False
                health_data["concerns"] = []

                if gmail_connection_rate < 50:
                    health_data["concerns"].append("Low Gmail connection rate")
                if token_consistency_rate < 90:
                    health_data["concerns"].append("Token consistency issues")
                if token_health_rate < 80:
                    health_data["concerns"].append("High token failure rate")
                if with_failing_tokens > total_users * 0.1:
                    health_data["concerns"].append("Too many users with failing tokens")

            return health_data

        else:
            return {
                "healthy": False,
                "service": "user_service",
                "error": "No user data found",
            }

    except DatabaseError as e:
        logger.error("Database error in user service health check", error=str(e))
        return {
            "healthy": False,
            "service": "user_service",
            "database_connectivity": f"error: {str(e)}",
        }
    except Exception as e:
        logger.error("User service health check failed", error=str(e))
        return {"healthy": False, "service": "user_service", "error": str(e)}


# Convenience functions for easy import
async def get_user_with_gmail_health(user_id: str) -> UserProfile | None:
    """Get user profile with Gmail connection health information."""
    return await get_user_profile(user_id)


async def check_user_gmail_status(user_id: str) -> dict:
    """Get comprehensive Gmail status for user."""
    return await get_user_gmail_summary(user_id)


async def find_users_with_gmail_issues() -> list:
    """Find users whose Gmail connections need attention."""
    return await get_users_needing_gmail_attention()


async def user_service_health_check() -> dict:
    """Check user service health with Gmail metrics."""
    return await get_user_service_health()
