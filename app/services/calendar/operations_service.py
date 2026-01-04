"""
Calendar operations Service for high-level calendar orchestration.
Manages calendar connection status, health monitoring, and integration with user management.
UPDATED: Now uses domain models from app.models.domain.calendar_domain
ARCHITECTURE: Mirrors gmail_connection_service.py patterns for consistency.
"""

from datetime import UTC, datetime
from typing import Any

from app.db.helpers import DatabaseError, execute_query, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.models.domain.calendar_domain import (
    CalendarAvailability,
    CalendarConnectionStatus,
    CalendarEvent,
    CalendarInfo,
)
from app.services.calendar.google_client import (
    GoogleCalendarError,
    google_calendar_service,
)
from app.services.core.token_service import (
    TokenServiceError,
    get_oauth_tokens,
)

logger = get_logger(__name__)


class CalendarConnectionError(Exception):
    """Custom exception for calendar connection operations."""

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


class CalendarConnectionService:
    """
    High-level service for Calendar connection management.

    Orchestrates calendar operations, manages connection status,
    and integrates with existing OAuth and user management systems.
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
                raise CalendarConnectionError("Database pool not initialized")
        except Exception as e:
            raise CalendarConnectionError(f"Database pool validation failed: {e}") from e

        self._config_validated = True
        logger.info("Calendar connection service initialized successfully")

    async def get_connection_status(self, user_id: str) -> CalendarConnectionStatus:
        """
        Get comprehensive calendar connection status for user.

        Args:
            user_id: UUID string of the user

        Returns:
            CalendarConnectionStatus: Complete calendar connection information
        """
        self._ensure_config_validated()

        try:
            logger.debug("Getting calendar connection status", user_id=user_id)

            # Check if user has calendar-enabled OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)

            if not oauth_tokens:
                return CalendarConnectionStatus(
                    connected=False, user_id=user_id, connection_health="no_tokens"
                )

            if not oauth_tokens.has_calendar_access():
                return CalendarConnectionStatus(
                    connected=False,
                    user_id=user_id,
                    connection_health="no_calendar_permissions",
                    scope=oauth_tokens.scope,
                    expires_at=oauth_tokens.expires_at,
                    needs_refresh=oauth_tokens.needs_refresh(),
                )

            # Test calendar access and get capabilities
            calendar_capabilities = await self._test_calendar_access(oauth_tokens.access_token)

            # Determine connection health
            connection_health = self._assess_calendar_health(oauth_tokens, calendar_capabilities)

            return CalendarConnectionStatus(
                connected=True,
                user_id=user_id,
                provider=oauth_tokens.provider,
                scope=oauth_tokens.scope,
                expires_at=oauth_tokens.expires_at,
                needs_refresh=oauth_tokens.needs_refresh(),
                last_used=getattr(oauth_tokens, "last_used_at", None),
                connection_health=connection_health["status"],
                calendars_accessible=calendar_capabilities.get("calendars_count", 0),
                primary_calendar_available=calendar_capabilities.get("has_primary", False),
                can_create_events=calendar_capabilities.get("can_create_events", False),
                health_details=connection_health,
            )

        except Exception as e:
            logger.error(
                "Error getting calendar connection status",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return CalendarConnectionStatus(
                connected=False,
                user_id=user_id,
                connection_health="error",
                health_details={"error": str(e)},
            )

    async def _test_calendar_access(self, access_token: str) -> dict[str, Any]:
        """
        Test calendar access and determine capabilities.

        Args:
            access_token: OAuth access token

        Returns:
            Dict: Calendar capabilities and test results
        """
        try:
            # Test by listing calendars
            calendars = await google_calendar_service.list_calendars(access_token)

            # Find primary calendar
            primary_calendar = None
            for calendar in calendars:
                if calendar.primary:
                    primary_calendar = calendar
                    break

            # Count calendars with different access levels
            readable_calendars = len(calendars)
            writable_calendars = len([cal for cal in calendars if cal.can_create_events()])

            capabilities = {
                "calendars_count": readable_calendars,
                "writable_calendars": writable_calendars,
                "has_primary": primary_calendar is not None,
                "can_create_events": (
                    primary_calendar.can_create_events() if primary_calendar else False
                ),
                "primary_calendar_id": primary_calendar.id if primary_calendar else None,
                "access_test_successful": True,
            }

            logger.debug(
                "Calendar access test successful",
                calendars_count=readable_calendars,
                writable_calendars=writable_calendars,
                has_primary=capabilities["has_primary"],
            )

            return capabilities

        except GoogleCalendarError as e:
            logger.warning(
                "Calendar access test failed",
                error=str(e),
                error_code=getattr(e, "error_code", None),
            )
            return {
                "calendars_count": 0,
                "writable_calendars": 0,
                "has_primary": False,
                "can_create_events": False,
                "access_test_successful": False,
                "error": str(e),
                "error_code": getattr(e, "error_code", None),
            }

    def _assess_calendar_health(
        self, oauth_tokens, calendar_capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Assess calendar connection health based on tokens and capabilities.

        Args:
            oauth_tokens: OAuth token object
            calendar_capabilities: Calendar access test results

        Returns:
            Dict: Health assessment with status and recommendations
        """
        try:
            # Check token health first
            token_health = oauth_tokens.get_health_status()

            # Calendar-specific health checks
            if not calendar_capabilities.get("access_test_successful", False):
                return {
                    "status": "api_error",
                    "message": "Calendar API access failed",
                    "severity": "high",
                    "action_required": "Check calendar permissions",
                    "details": {
                        "token_health": token_health,
                        "calendar_error": calendar_capabilities.get("error", "Unknown error"),
                        "error_code": calendar_capabilities.get("error_code"),
                    },
                }

            if not calendar_capabilities.get("has_primary", False):
                return {
                    "status": "no_primary_calendar",
                    "message": "Primary calendar not accessible",
                    "severity": "medium",
                    "action_required": "Verify calendar permissions",
                    "details": {
                        "token_health": token_health,
                        "calendars_found": calendar_capabilities.get("calendars_count", 0),
                    },
                }

            if not calendar_capabilities.get("can_create_events", False):
                return {
                    "status": "read_only",
                    "message": "Calendar access is read-only",
                    "severity": "medium",
                    "action_required": "Grant calendar edit permissions for full functionality",
                    "details": {
                        "token_health": token_health,
                        "calendars_accessible": calendar_capabilities.get("calendars_count", 0),
                        "writable_calendars": calendar_capabilities.get("writable_calendars", 0),
                    },
                }

            # Check token-based health
            if token_health["status"] != "healthy":
                return {
                    "status": token_health["status"],
                    "message": f"Calendar tokens {token_health['message'].lower()}",
                    "severity": token_health["severity"],
                    "action_required": token_health.get("action_required"),
                    "details": {
                        "token_health": token_health,
                        "calendar_capabilities": calendar_capabilities,
                    },
                }

            # All checks passed
            return {
                "status": "healthy",
                "message": f"Calendar access healthy - {calendar_capabilities['calendars_count']} calendars accessible",
                "severity": "none",
                "action_required": None,
                "details": {
                    "token_health": token_health,
                    "calendar_capabilities": calendar_capabilities,
                },
            }

        except Exception as e:
            logger.error("Error assessing calendar health", error=str(e))
            return {
                "status": "assessment_error",
                "message": f"Health assessment failed: {e}",
                "severity": "medium",
                "details": {"error": str(e)},
            }

    async def get_user_calendars(self, user_id: str) -> list[CalendarInfo]:
        """
        Get list of calendars accessible to user.

        Args:
            user_id: UUID string of the user

        Returns:
            List[CalendarInfo]: List of accessible calendars

        Raises:
            CalendarConnectionError: If getting calendars fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise CalendarConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_calendar_access():
                raise CalendarConnectionError("No calendar permissions", user_id=user_id)

            # Get calendars
            calendars = await google_calendar_service.list_calendars(oauth_tokens.access_token)

            logger.info(
                "User calendars retrieved",
                user_id=user_id,
                calendar_count=len(calendars),
            )

            return calendars

        except GoogleCalendarError as e:
            logger.error(
                "Calendar API error getting user calendars",
                user_id=user_id,
                error=str(e),
            )
            raise CalendarConnectionError(f"Calendar API error: {e}", user_id=user_id) from e

        except TokenServiceError as e:
            logger.error(
                "Token service error getting user calendars",
                user_id=user_id,
                error=str(e),
            )
            raise CalendarConnectionError(f"Token error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error getting user calendars",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise CalendarConnectionError(f"Failed to get calendars: {e}", user_id=user_id) from e

    async def get_upcoming_events(
        self, user_id: str, hours_ahead: int = 24, max_events: int = 10
    ) -> list[CalendarEvent]:
        """
        Get upcoming events for user.

        Args:
            user_id: UUID string of the user
            hours_ahead: How many hours ahead to look
            max_events: Maximum number of events to return

        Returns:
            List[CalendarEvent]: Upcoming events

        Raises:
            CalendarConnectionError: If getting events fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise CalendarConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_calendar_access():
                raise CalendarConnectionError("No calendar permissions", user_id=user_id)

            # Calculate time range
            from datetime import timedelta

            now = datetime.now(UTC)
            time_max = now + timedelta(hours=hours_ahead)

            # Get events
            events = await google_calendar_service.list_events(
                access_token=oauth_tokens.access_token,
                time_min=now,
                time_max=time_max,
                max_results=max_events,
            )

            logger.info(
                "Upcoming events retrieved",
                user_id=user_id,
                event_count=len(events),
                hours_ahead=hours_ahead,
            )

            return events

        except GoogleCalendarError as e:
            logger.error(
                "Calendar API error getting upcoming events",
                user_id=user_id,
                error=str(e),
            )
            raise CalendarConnectionError(f"Calendar API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error getting upcoming events",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise CalendarConnectionError(f"Failed to get events: {e}", user_id=user_id) from e

    async def check_availability(
        self, user_id: str, start_time: datetime, end_time: datetime
    ) -> CalendarAvailability:
        """
        Check if user is available during specified time.

        Args:
            user_id: UUID string of the user
            start_time: Start of time period to check
            end_time: End of time period to check

        Returns:
            CalendarAvailability: Availability information with smart analysis

        Raises:
            CalendarConnectionError: If availability check fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise CalendarConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_calendar_access():
                raise CalendarConnectionError("No calendar permissions", user_id=user_id)

            # Check availability using Google Calendar service
            availability_data = await google_calendar_service.check_availability(
                access_token=oauth_tokens.access_token,
                start_time=start_time,
                end_time=end_time,
            )

            # Convert to domain model with enhanced functionality
            availability = CalendarAvailability(
                is_free=availability_data["is_free"],
                start_time=start_time,
                end_time=end_time,
                busy_periods=availability_data["busy_periods"],
                calendars_checked=availability_data["calendars_checked"],
                total_conflicts=availability_data["total_conflicts"],
            )

            logger.info(
                "Availability check completed",
                user_id=user_id,
                is_free=availability.is_free,
                conflicts=availability.total_conflicts,
                duration_minutes=availability.duration_minutes(),
            )

            return availability

        except GoogleCalendarError as e:
            logger.error(
                "Calendar API error checking availability",
                user_id=user_id,
                error=str(e),
            )
            raise CalendarConnectionError(f"Calendar API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error checking availability",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise CalendarConnectionError(
                f"Failed to check availability: {e}", user_id=user_id
            ) from e

    async def create_event(
        self,
        user_id: str,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        description: str = "",
        location: str = "",
        calendar_id: str | None = None,
    ) -> CalendarEvent:
        """
        Create calendar event for user.

        Args:
            user_id: UUID string of the user
            summary: Event title
            start_time: Event start time
            end_time: Event end time
            description: Event description
            location: Event location
            calendar_id: Specific calendar ID (default: primary)

        Returns:
            CalendarEvent: Created event

        Raises:
            CalendarConnectionError: If event creation fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise CalendarConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_calendar_access():
                raise CalendarConnectionError("No calendar permissions", user_id=user_id)

            # Create event
            event = await google_calendar_service.create_event(
                access_token=oauth_tokens.access_token,
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                calendar_id=calendar_id or "primary",
            )

            # Update last used timestamp
            await self._update_calendar_usage(user_id)

            logger.info(
                "Calendar event created",
                user_id=user_id,
                event_id=event.id,
                summary=summary,
            )

            return event

        except GoogleCalendarError as e:
            logger.error(
                "Calendar API error creating event",
                user_id=user_id,
                summary=summary,
                error=str(e),
            )
            raise CalendarConnectionError(f"Calendar API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error creating event",
                user_id=user_id,
                summary=summary,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise CalendarConnectionError(f"Failed to create event: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _update_calendar_usage(self, user_id: str) -> None:
        """
        Update calendar last used timestamp.

        Args:
            user_id: UUID string of the user
        """
        try:
            # Note: This could be extended to track calendar-specific usage
            # For now, we'll rely on the oauth_tokens updated_at field
            query = """
            UPDATE oauth_tokens
            SET updated_at = NOW()
            WHERE user_id = %s AND provider = 'google'
            """

            await execute_query(query, (user_id,))

            logger.debug("Calendar usage timestamp updated", user_id=user_id)

        except DatabaseError as e:
            logger.warning(
                "Failed to update calendar usage timestamp",
                user_id=user_id,
                error=str(e),
            )
            # Don't raise exception for usage tracking failure

    async def get_connection_metrics(self) -> dict[str, Any]:
        """
        Get calendar connection metrics for monitoring.

        Returns:
            Dict: Calendar connection metrics and statistics
        """
        try:
            # This would query database for calendar-specific metrics
            # For now, leverage existing user service metrics
            from app.services.core.user_service import get_user_service_health

            user_health = await get_user_service_health()

            # Extract calendar-relevant metrics
            metrics = {
                "total_users": user_health.get("user_metrics", {}).get("total_active_users", 0),
                "users_with_tokens": user_health.get("gmail_health_metrics", {}).get(
                    "users_with_tokens", 0
                ),
                "healthy_connections": "unknown",  # Would need calendar-specific health tracking
                "calendar_api_connectivity": "unknown",  # Would test Calendar API
                "service": "calendar_connection",
                "timestamp": datetime.utcnow().isoformat(),
            }

            return metrics

        except Exception as e:
            logger.error("Error getting calendar connection metrics", error=str(e))
            return {
                "service": "calendar_connection",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    async def health_check(self) -> dict[str, Any]:
        """
        Check calendar connection service health.

        Returns:
            Dict: Health status and metrics
        """
        try:
            health_data = {
                "healthy": True,
                "service": "calendar_connection",
                "database_connectivity": "unknown",
                "calendar_api_connectivity": "unknown",
            }

            # Test database connectivity (use existing pattern)
            try:

                # We'll test this asyncronously in a real implementation
                # For now, assume healthy if no exception
                health_data["database_connectivity"] = "ok"
            except Exception as e:
                health_data["database_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Test calendar API service health
            try:
                from app.services.calendar.google_client import google_calendar_health

                calendar_health = await google_calendar_health()
                health_data["calendar_api_connectivity"] = (
                    "ok" if calendar_health.get("healthy", False) else "error"
                )
                if not calendar_health.get("healthy", False):
                    health_data["healthy"] = False
                    health_data["calendar_api_error"] = calendar_health.get(
                        "error", "Unknown error"
                    )
            except Exception as e:
                health_data["calendar_api_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Add service capabilities
            health_data["capabilities"] = [
                "get_connection_status",
                "get_user_calendars",
                "get_upcoming_events",
                "check_availability",
                "create_event",
            ]

            return health_data

        except Exception as e:
            logger.error("Calendar connection service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "calendar_connection",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }


# Singleton instance for application use
calendar_connection_service = CalendarConnectionService()


# Convenience functions for easy import
async def get_calendar_status(user_id: str) -> CalendarConnectionStatus:
    """Get calendar connection status for user."""
    return await calendar_connection_service.get_connection_status(user_id)


async def get_user_calendars(user_id: str) -> list[CalendarInfo]:
    """Get calendars accessible to user."""
    return await calendar_connection_service.get_user_calendars(user_id)


async def get_user_upcoming_events(
    user_id: str, hours_ahead: int = 24, max_events: int = 10
) -> list[CalendarEvent]:
    """Get upcoming events for user."""
    return await calendar_connection_service.get_upcoming_events(user_id, hours_ahead, max_events)


async def check_user_availability(
    user_id: str, start_time: datetime, end_time: datetime
) -> dict[str, Any]:
    """Check if user is available during specified time."""
    availability = await calendar_connection_service.check_availability(
        user_id, start_time, end_time
    )
    return availability.to_dict()  # Convert domain model to dict for backward compatibility


async def create_user_event(
    user_id: str,
    summary: str,
    start_time: datetime,
    end_time: datetime,
    description: str = "",
    location: str = "",
) -> CalendarEvent:
    """Create calendar event for user."""
    return await calendar_connection_service.create_event(
        user_id, summary, start_time, end_time, description, location
    )


async def calendar_connection_health() -> dict[str, Any]:
    """Check calendar connection service health."""
    return await calendar_connection_service.health_check()


@with_db_retry(max_retries=3, base_delay=0.1)
async def _update_user_calendar_status(user_id: str, connected: bool) -> bool:
    """Update only the calendar connection flag."""
    try:
        query = """
        UPDATE users
        SET calendar_connected = %s, updated_at = NOW()
        WHERE id = %s AND is_active = true
        """

        affected_rows = await execute_query(query, (connected, user_id))

        success = affected_rows > 0
        if success:
            logger.info("User calendar status updated", user_id=user_id, connected=connected)
        else:
            logger.warning("No user found to update calendar status", user_id=user_id)

        return success

    except DatabaseError as e:
        logger.error("Database error updating calendar status", user_id=user_id, error=str(e))
        return False
