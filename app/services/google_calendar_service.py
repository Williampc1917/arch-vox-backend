"""
Google Calendar API Service for calendar operations and event management.
Handles Calendar API client initialization, CRUD operations, and timezone management.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)

# Google Calendar API configuration
CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
CALENDAR_PRIMARY = "primary"  # User's primary calendar

# Request timeouts and retry configuration
REQUEST_TIMEOUT = 30  # seconds (calendar operations can be slower)
MAX_RETRIES = 3
BACKOFF_FACTOR = 2


class GoogleCalendarError(Exception):
    """Custom exception for Google Calendar API errors."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        status_code: int | None = None,
        response_data: dict | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.response_data = response_data or {}


class CalendarEvent:
    """Structured representation of a calendar event."""

    def __init__(self, data: dict):
        self.id = data.get("id")
        self.summary = data.get("summary", "")
        self.description = data.get("description", "")
        self.start_time = self._parse_datetime(data.get("start", {}))
        self.end_time = self._parse_datetime(data.get("end", {}))
        self.timezone = data.get("start", {}).get("timeZone", "UTC")
        self.status = data.get("status", "confirmed")
        self.attendees = data.get("attendees", [])
        self.location = data.get("location", "")
        self.created = self._parse_datetime_iso(data.get("created"))
        self.updated = self._parse_datetime_iso(data.get("updated"))
        self.raw_data = data

    def _parse_datetime(self, dt_data: dict) -> datetime | None:
        """Parse datetime from Google Calendar format."""
        if not dt_data:
            return None

        # Handle all-day events (date only)
        if "date" in dt_data:
            date_str = dt_data["date"]
            return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Handle timed events (dateTime)
        if "dateTime" in dt_data:
            dt_str = dt_data["dateTime"]
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                logger.warning("Failed to parse datetime", datetime_str=dt_str)
                return None

        return None

    def _parse_datetime_iso(self, dt_str: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Failed to parse ISO datetime", datetime_str=dt_str)
            return None

    def is_all_day(self) -> bool:
        """Check if this is an all-day event."""
        return "date" in self.raw_data.get("start", {})

    def is_busy(self) -> bool:
        """Check if this event shows as busy (blocks availability)."""
        transparency = self.raw_data.get("transparency", "opaque")
        return transparency == "opaque" and self.status == "confirmed"

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "summary": self.summary,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "timezone": self.timezone,
            "status": self.status,
            "location": self.location,
            "is_all_day": self.is_all_day(),
            "is_busy": self.is_busy(),
            "attendees_count": len(self.attendees),
            "created": self.created.isoformat() if self.created else None,
            "updated": self.updated.isoformat() if self.updated else None,
        }


class CalendarInfo:
    """Structured representation of calendar metadata."""

    def __init__(self, data: dict):
        self.id = data.get("id")
        self.summary = data.get("summary", "")
        self.description = data.get("description", "")
        self.timezone = data.get("timeZone", "UTC")
        self.access_role = data.get("accessRole", "reader")
        self.primary = data.get("primary", False)
        self.selected = data.get("selected", True)
        self.color_id = data.get("colorId")
        self.background_color = data.get("backgroundColor")
        self.foreground_color = data.get("foregroundColor")

    def can_create_events(self) -> bool:
        """Check if we can create events in this calendar."""
        return self.access_role in ["owner", "writer"]

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "summary": self.summary,
            "description": self.description,
            "timezone": self.timezone,
            "access_role": self.access_role,
            "primary": self.primary,
            "selected": self.selected,
            "can_create_events": self.can_create_events(),
            "color_id": self.color_id,
            "background_color": self.background_color,
            "foreground_color": self.foreground_color,
        }


class GoogleCalendarService:
    """
    Service for Google Calendar API operations.

    Handles calendar list management, event CRUD operations, availability checking,
    and timezone management with proper error handling and retry logic.
    """

    def __init__(self):
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy for Calendar API."""
        session = requests.Session()

        # Configure retry strategy for Calendar API
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP codes
            allowed_methods=["GET", "POST", "PUT", "PATCH"],  # Calendar operations
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        return session

    def _get_auth_headers(self, access_token: str) -> dict:
        """Get authorization headers for Calendar API requests."""
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _handle_api_response(self, response: requests.Response, operation: str) -> dict:
        """
        Handle and validate Calendar API response.

        Args:
            response: HTTP response from Calendar API
            operation: Operation name for logging

        Returns:
            dict: Parsed response data

        Raises:
            GoogleCalendarError: If response contains errors
        """
        logger.debug(
            f"Calendar API {operation} response",
            status_code=response.status_code,
            response_size=len(response.text) if response.text else 0,
        )

        # Handle successful responses
        if response.ok:
            try:
                return response.json() if response.text else {}
            except ValueError as e:
                logger.error(f"Failed to parse Calendar API {operation} response", error=str(e))
                raise GoogleCalendarError(f"Invalid response format: {e}") from e

        # Handle API errors
        try:
            error_data = response.json() if response.text else {}
            error_info = error_data.get("error", {})
            
            error_code = error_info.get("code", "unknown")
            error_message = error_info.get("message", "Unknown Calendar API error")
            
            logger.error(
                f"Calendar API {operation} failed",
                status_code=response.status_code,
                error_code=error_code,
                error_message=error_message,
            )

            # Map common Calendar API errors
            user_message = self._map_calendar_error(error_code, error_message)

            raise GoogleCalendarError(
                user_message,
                error_code=str(error_code),
                status_code=response.status_code,
                response_data=error_data,
            )

        except ValueError:
            # Non-JSON error response
            logger.error(
                f"Calendar API {operation} failed with non-JSON response",
                status_code=response.status_code,
                response_text=response.text[:200] if response.text else "",
            )
            raise GoogleCalendarError(
                f"Calendar API error (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from None

    def _map_calendar_error(self, error_code: str, error_message: str) -> str:
        """Map Calendar API error codes to user-friendly messages."""
        error_mappings = {
            "403": "Calendar access denied. Please check permissions.",
            "404": "Calendar or event not found.",
            "400": "Invalid calendar request format.",
            "401": "Calendar authorization expired. Please reconnect.",
            "429": "Too many calendar requests. Please try again later.",
            "500": "Google Calendar service temporarily unavailable.",
        }

        return error_mappings.get(error_code, f"Calendar error: {error_message}")

    async def list_calendars(self, access_token: str) -> List[CalendarInfo]:
        """
        List all calendars accessible to the user.

        Args:
            access_token: Valid OAuth access token

        Returns:
            List[CalendarInfo]: List of accessible calendars

        Raises:
            GoogleCalendarError: If listing calendars fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/users/me/calendarList"
            headers = self._get_auth_headers(access_token)

            logger.info("Listing user calendars")

            response = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            data = self._handle_api_response(response, "list_calendars")

            calendars = []
            for item in data.get("items", []):
                calendar_info = CalendarInfo(item)
                calendars.append(calendar_info)

            logger.info("Calendars listed successfully", calendar_count=len(calendars))
            return calendars

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error listing calendars", error=str(e))
            raise GoogleCalendarError(f"Failed to list calendars: {e}") from e

    async def get_calendar(self, access_token: str, calendar_id: str = CALENDAR_PRIMARY) -> CalendarInfo:
        """
        Get information about a specific calendar.

        Args:
            access_token: Valid OAuth access token
            calendar_id: Calendar ID (default: primary)

        Returns:
            CalendarInfo: Calendar information

        Raises:
            GoogleCalendarError: If getting calendar fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/users/me/calendarList/{calendar_id}"
            headers = self._get_auth_headers(access_token)

            logger.info("Getting calendar info", calendar_id=calendar_id)

            response = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            data = self._handle_api_response(response, "get_calendar")

            calendar_info = CalendarInfo(data)
            logger.info("Calendar info retrieved successfully", calendar_id=calendar_id)
            return calendar_info

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error getting calendar", calendar_id=calendar_id, error=str(e))
            raise GoogleCalendarError(f"Failed to get calendar: {e}") from e

    async def list_events(
        self,
        access_token: str,
        calendar_id: str = CALENDAR_PRIMARY,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        single_events: bool = True,
    ) -> List[CalendarEvent]:
        """
        List events from a calendar.

        Args:
            access_token: Valid OAuth access token
            calendar_id: Calendar ID (default: primary)
            time_min: Start time filter (default: now)
            time_max: End time filter (optional)
            max_results: Maximum number of events to return
            single_events: Expand recurring events into individual instances

        Returns:
            List[CalendarEvent]: List of calendar events

        Raises:
            GoogleCalendarError: If listing events fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events"
            headers = self._get_auth_headers(access_token)

            # Build query parameters
            params = {
                "maxResults": max_results,
                "singleEvents": single_events,
                "orderBy": "startTime" if single_events else "updated",
            }

            if time_min:
                params["timeMin"] = time_min.isoformat()
            else:
                # Default to current time
                params["timeMin"] = datetime.now(timezone.utc).isoformat()

            if time_max:
                params["timeMax"] = time_max.isoformat()

            logger.info(
                "Listing calendar events",
                calendar_id=calendar_id,
                time_min=params.get("timeMin"),
                time_max=params.get("timeMax"),
                max_results=max_results,
            )

            response = self._session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            data = self._handle_api_response(response, "list_events")

            events = []
            for item in data.get("items", []):
                event = CalendarEvent(item)
                events.append(event)

            logger.info(
                "Events listed successfully",
                calendar_id=calendar_id,
                event_count=len(events),
            )
            return events

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error listing events", calendar_id=calendar_id, error=str(e))
            raise GoogleCalendarError(f"Failed to list events: {e}") from e

    async def get_event(
        self, access_token: str, event_id: str, calendar_id: str = CALENDAR_PRIMARY
    ) -> CalendarEvent:
        """
        Get a specific event by ID.

        Args:
            access_token: Valid OAuth access token
            event_id: Event ID
            calendar_id: Calendar ID (default: primary)

        Returns:
            CalendarEvent: Event details

        Raises:
            GoogleCalendarError: If getting event fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events/{event_id}"
            headers = self._get_auth_headers(access_token)

            logger.info("Getting calendar event", event_id=event_id, calendar_id=calendar_id)

            response = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            data = self._handle_api_response(response, "get_event")

            event = CalendarEvent(data)
            logger.info("Event retrieved successfully", event_id=event_id)
            return event

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error getting event", event_id=event_id, error=str(e))
            raise GoogleCalendarError(f"Failed to get event: {e}") from e

    async def create_event(
        self,
        access_token: str,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        calendar_id: str = CALENDAR_PRIMARY,
        description: str = "",
        location: str = "",
        timezone_str: str = "UTC",
        attendees: List[str] | None = None,
    ) -> CalendarEvent:
        """
        Create a new calendar event.

        Args:
            access_token: Valid OAuth access token
            summary: Event title
            start_time: Event start time
            end_time: Event end time
            calendar_id: Calendar ID (default: primary)
            description: Event description
            location: Event location
            timezone_str: Timezone for the event
            attendees: List of attendee email addresses

        Returns:
            CalendarEvent: Created event

        Raises:
            GoogleCalendarError: If creating event fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events"
            headers = self._get_auth_headers(access_token)

            # Build event data
            event_data = {
                "summary": summary,
                "description": description,
                "location": location,
                "start": {
                    "dateTime": start_time.isoformat(),
                    "timeZone": timezone_str,
                },
                "end": {
                    "dateTime": end_time.isoformat(),
                    "timeZone": timezone_str,
                },
            }

            if attendees:
                event_data["attendees"] = [{"email": email} for email in attendees]

            logger.info(
                "Creating calendar event",
                summary=summary,
                start_time=start_time.isoformat(),
                calendar_id=calendar_id,
            )

            response = self._session.post(
                url, headers=headers, data=json.dumps(event_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "create_event")

            event = CalendarEvent(data)
            logger.info("Event created successfully", event_id=event.id, summary=summary)
            return event

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error creating event", summary=summary, error=str(e))
            raise GoogleCalendarError(f"Failed to create event: {e}") from e

    async def update_event(
        self,
        access_token: str,
        event_id: str,
        calendar_id: str = CALENDAR_PRIMARY,
        summary: str | None = None,
        description: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        location: str | None = None,
        timezone_str: str | None = None,
    ) -> CalendarEvent:
        """
        Update an existing calendar event.

        Args:
            access_token: Valid OAuth access token
            event_id: Event ID to update
            calendar_id: Calendar ID (default: primary)
            summary: New event title (optional)
            description: New event description (optional)
            start_time: New start time (optional)
            end_time: New end time (optional)
            location: New location (optional)
            timezone_str: New timezone (optional)

        Returns:
            CalendarEvent: Updated event

        Raises:
            GoogleCalendarError: If updating event fails
        """
        try:
            # First get the existing event
            existing_event = await self.get_event(access_token, event_id, calendar_id)
            
            url = f"{CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events/{event_id}"
            headers = self._get_auth_headers(access_token)

            # Build update data (only include fields that are being updated)
            update_data = {}
            
            if summary is not None:
                update_data["summary"] = summary
            if description is not None:
                update_data["description"] = description
            if location is not None:
                update_data["location"] = location
                
            if start_time is not None or timezone_str is not None:
                update_data["start"] = {
                    "dateTime": (start_time or existing_event.start_time).isoformat(),
                    "timeZone": timezone_str or existing_event.timezone,
                }
                
            if end_time is not None or timezone_str is not None:
                update_data["end"] = {
                    "dateTime": (end_time or existing_event.end_time).isoformat(),
                    "timeZone": timezone_str or existing_event.timezone,
                }

            logger.info(
                "Updating calendar event",
                event_id=event_id,
                calendar_id=calendar_id,
                fields_updated=list(update_data.keys()),
            )

            response = self._session.patch(
                url, headers=headers, data=json.dumps(update_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "update_event")

            event = CalendarEvent(data)
            logger.info("Event updated successfully", event_id=event_id)
            return event

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error updating event", event_id=event_id, error=str(e))
            raise GoogleCalendarError(f"Failed to update event: {e}") from e

    async def delete_event(
        self, access_token: str, event_id: str, calendar_id: str = CALENDAR_PRIMARY
    ) -> bool:
        """
        Delete a calendar event.

        Args:
            access_token: Valid OAuth access token
            event_id: Event ID to delete
            calendar_id: Calendar ID (default: primary)

        Returns:
            bool: True if deletion successful

        Raises:
            GoogleCalendarError: If deleting event fails
        """
        try:
            url = f"{CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events/{event_id}"
            headers = self._get_auth_headers(access_token)

            logger.info("Deleting calendar event", event_id=event_id, calendar_id=calendar_id)

            response = self._session.delete(url, headers=headers, timeout=REQUEST_TIMEOUT)

            # For DELETE operations, success is typically 204 No Content
            if response.status_code == 204:
                logger.info("Event deleted successfully", event_id=event_id)
                return True
            else:
                self._handle_api_response(response, "delete_event")
                return True

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error deleting event", event_id=event_id, error=str(e))
            raise GoogleCalendarError(f"Failed to delete event: {e}") from e

    async def check_availability(
        self,
        access_token: str,
        start_time: datetime,
        end_time: datetime,
        calendar_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Check availability during a specific time period.

        Args:
            access_token: Valid OAuth access token
            start_time: Start of time period to check
            end_time: End of time period to check
            calendar_ids: List of calendar IDs to check (default: primary only)

        Returns:
            Dict: Availability information with busy periods

        Raises:
            GoogleCalendarError: If availability check fails
        """
        try:
            if calendar_ids is None:
                calendar_ids = [CALENDAR_PRIMARY]

            url = f"{CALENDAR_API_BASE_URL}/freeBusy"
            headers = self._get_auth_headers(access_token)

            # Build freebusy query
            query_data = {
                "timeMin": start_time.isoformat(),
                "timeMax": end_time.isoformat(),
                "items": [{"id": cal_id} for cal_id in calendar_ids],
            }

            logger.info(
                "Checking calendar availability",
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
                calendar_count=len(calendar_ids),
            )

            response = self._session.post(
                url, headers=headers, data=json.dumps(query_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "check_availability")

            # Process busy periods
            all_busy_periods = []
            calendars_status = {}

            for cal_id in calendar_ids:
                calendar_busy = data.get("calendars", {}).get(cal_id, {})
                busy_periods = calendar_busy.get("busy", [])
                
                calendars_status[cal_id] = {
                    "busy_periods": busy_periods,
                    "busy_count": len(busy_periods),
                    "errors": calendar_busy.get("errors", []),
                }
                
                all_busy_periods.extend(busy_periods)

            # Determine overall availability
            is_free = len(all_busy_periods) == 0

            availability_info = {
                "is_free": is_free,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                },
                "busy_periods": all_busy_periods,
                "calendars_checked": calendars_status,
                "total_conflicts": len(all_busy_periods),
            }

            logger.info(
                "Availability check completed",
                is_free=is_free,
                busy_periods_count=len(all_busy_periods),
            )

            return availability_info

        except GoogleCalendarError:
            raise
        except Exception as e:
            logger.error("Unexpected error checking availability", error=str(e))
            raise GoogleCalendarError(f"Failed to check availability: {e}") from e

    def health_check(self) -> Dict[str, Any]:
        """
        Check Google Calendar service health.

        Returns:
            Dict: Health status and configuration
        """
        try:
            health_data = {
                "healthy": True,
                "service": "google_calendar",
                "api_base_url": CALENDAR_API_BASE_URL,
                "request_timeout": REQUEST_TIMEOUT,
                "max_retries": MAX_RETRIES,
                "supported_operations": [
                    "list_calendars",
                    "get_calendar", 
                    "list_events",
                    "get_event",
                    "create_event",
                    "update_event",
                    "delete_event",
                    "check_availability",
                ],
            }

            # Test basic connectivity to Google Calendar API
            try:
                # Simple HEAD request to check API availability
                response = requests.head(CALENDAR_API_BASE_URL, timeout=5)
                health_data["api_connectivity"] = (
                    "ok" if response.status_code in [200, 401, 403] else f"error_{response.status_code}"
                )
            except requests.exceptions.RequestException as e:
                health_data["api_connectivity"] = f"error_{type(e).__name__}"
                health_data["healthy"] = False

            return health_data

        except Exception as e:
            logger.error("Google Calendar service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "google_calendar",
                "error": str(e),
            }


# Singleton instance for application use
google_calendar_service = GoogleCalendarService()


# Convenience functions for easy import
async def list_user_calendars(access_token: str) -> List[CalendarInfo]:
    """List all calendars for user."""
    return await google_calendar_service.list_calendars(access_token)


async def get_calendar_events(
    access_token: str,
    calendar_id: str = CALENDAR_PRIMARY,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    max_results: int = 50,
) -> List[CalendarEvent]:
    """Get events from calendar."""
    return await google_calendar_service.list_events(
        access_token, calendar_id, time_min, time_max, max_results
    )


async def create_calendar_event(
    access_token: str,
    summary: str,
    start_time: datetime,
    end_time: datetime,
    calendar_id: str = CALENDAR_PRIMARY,
    description: str = "",
    location: str = "",
    timezone_str: str = "UTC",
) -> CalendarEvent:
    """Create new calendar event."""
    return await google_calendar_service.create_event(
        access_token, summary, start_time, end_time, calendar_id, description, location, timezone_str
    )


async def check_calendar_availability(
    access_token: str,
    start_time: datetime,
    end_time: datetime,
    calendar_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Check availability in calendars."""
    return await google_calendar_service.check_availability(access_token, start_time, end_time, calendar_ids)


def google_calendar_health() -> Dict[str, Any]:
    """Check Google Calendar service health."""
    return google_calendar_service.health_check()