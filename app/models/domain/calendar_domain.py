# app/models/domain/calendar_domain.py
"""
Calendar Domain Models
Domain models for calendar operations and business logic.
Used by services for internal processing and business rules.
"""

from datetime import UTC, datetime, timedelta
from typing import Any


class CalendarEvent:
    """Domain model for calendar events with business logic."""

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
            return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)

        # Handle timed events (dateTime)
        if "dateTime" in dt_data:
            dt_str = dt_data["dateTime"]
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                return None

        return None

    def _parse_datetime_iso(self, dt_str: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def is_all_day(self) -> bool:
        """Check if this is an all-day event."""
        return "date" in self.raw_data.get("start", {})

    def is_busy(self) -> bool:
        """Check if this event shows as busy (blocks availability)."""
        transparency = self.raw_data.get("transparency", "opaque")
        return transparency == "opaque" and self.status == "confirmed"

    def duration_minutes(self) -> int:
        """Get event duration in minutes."""
        if not self.start_time or not self.end_time:
            return 0
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    def is_upcoming(self) -> bool:
        """Check if event is in the future."""
        if not self.start_time:
            return False
        now = datetime.now(UTC)
        # Ensure timezone compatibility
        start_time = self.start_time
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        elif start_time.tzinfo != UTC:
            start_time = start_time.astimezone(UTC)
        return start_time > now

    def is_today(self) -> bool:
        """Check if event is today."""
        if not self.start_time:
            return False
        now = datetime.now(UTC)
        start_time = self.start_time
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        elif start_time.tzinfo != UTC:
            start_time = start_time.astimezone(UTC)
        return start_time.date() == now.date()

    def conflicts_with(self, other_start: datetime, other_end: datetime) -> bool:
        """Check if this event conflicts with another time period."""
        if not self.start_time or not self.end_time:
            return False

        # Only busy events cause conflicts
        if not self.is_busy():
            return False

        # Ensure timezone compatibility
        start_time = self.start_time
        end_time = self.end_time

        if start_time.tzinfo != other_start.tzinfo:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)
            start_time = start_time.astimezone(other_start.tzinfo)

        if end_time.tzinfo != other_end.tzinfo:
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=UTC)
            end_time = end_time.astimezone(other_end.tzinfo)

        # Check for overlap: events conflict if one starts before the other ends
        return start_time < other_end and end_time > other_start

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
            "duration_minutes": self.duration_minutes(),
            "is_upcoming": self.is_upcoming(),
            "is_today": self.is_today(),
        }


class CalendarInfo:
    """Domain model for calendar metadata with business logic."""

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

    def can_modify_events(self) -> bool:
        """Check if we can modify events in this calendar."""
        return self.access_role in ["owner", "writer"]

    def can_delete_events(self) -> bool:
        """Check if we can delete events in this calendar (usually owner only)."""
        return self.access_role == "owner"

    def is_writable(self) -> bool:
        """Check if calendar allows any write operations."""
        return self.can_create_events()

    def is_readable(self) -> bool:
        """Check if we can read events from this calendar."""
        return self.access_role in ["owner", "writer", "reader"]

    def get_permission_summary(self) -> dict[str, bool]:
        """Get comprehensive permission summary."""
        return {
            "can_read": self.is_readable(),
            "can_create": self.can_create_events(),
            "can_modify": self.can_modify_events(),
            "can_delete": self.can_delete_events(),
            "is_primary": self.primary,
            "is_selected": self.selected,
        }

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
            "can_modify_events": self.can_modify_events(),
            "can_delete_events": self.can_delete_events(),
            "color_id": self.color_id,
            "background_color": self.background_color,
            "foreground_color": self.foreground_color,
            "permissions": self.get_permission_summary(),
        }


class CalendarConnectionStatus:
    """Domain model representing calendar connection status for a user."""

    def __init__(
        self,
        connected: bool,
        user_id: str,
        provider: str = "google",
        scope: str | None = None,
        expires_at: datetime | None = None,
        needs_refresh: bool = False,
        last_used: datetime | None = None,
        connection_health: str = "unknown",
        calendars_accessible: int = 0,
        primary_calendar_available: bool = False,
        can_create_events: bool = False,
        health_details: dict[str, Any] | None = None,
    ):
        self.connected = connected
        self.user_id = user_id
        self.provider = provider
        self.scope = scope
        self.expires_at = expires_at
        self.needs_refresh = needs_refresh
        self.last_used = last_used
        self.connection_health = connection_health
        self.calendars_accessible = calendars_accessible
        self.primary_calendar_available = primary_calendar_available
        self.can_create_events = can_create_events
        self.health_details = health_details or {}

    def is_healthy(self) -> bool:
        """Check if connection is in healthy state."""
        healthy_states = ["healthy", "refresh_scheduled", "expiring_soon"]
        return self.connection_health in healthy_states

    def needs_attention(self) -> bool:
        """Check if connection needs user attention."""
        attention_states = ["expired", "failing", "invalid", "no_tokens", "error"]
        return self.connection_health in attention_states

    def is_functional(self) -> bool:
        """Check if connection can perform basic operations."""
        return self.connected and self.calendars_accessible > 0

    def get_capabilities(self) -> dict[str, Any]:
        """Get summary of calendar capabilities."""
        return {
            "calendars_accessible": self.calendars_accessible,
            "primary_calendar_available": self.primary_calendar_available,
            "can_create_events": self.can_create_events,
            "can_read_events": self.connected and self.calendars_accessible > 0,
            "can_check_availability": self.connected and self.calendars_accessible > 0,
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Get comprehensive health summary."""
        return {
            "status": self.connection_health,
            "is_healthy": self.is_healthy(),
            "needs_attention": self.needs_attention(),
            "is_functional": self.is_functional(),
            "details": self.health_details,
            "token_info": {
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "needs_refresh": self.needs_refresh,
                "last_used": self.last_used.isoformat() if self.last_used else None,
            },
            "capabilities": self.get_capabilities(),
        }

    def time_until_expiry(self) -> int | None:
        """Get minutes until token expiry (None if no expiry or already expired)."""
        if not self.expires_at:
            return None

        now = datetime.now(UTC)
        expires_at = self.expires_at

        # Ensure timezone compatibility
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        elif expires_at.tzinfo != UTC:
            expires_at = expires_at.astimezone(UTC)

        if expires_at <= now:
            return None  # Already expired

        delta = expires_at - now
        return int(delta.total_seconds() / 60)

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Get actionable recommendations based on connection status."""
        recommendations = []

        if not self.connected:
            recommendations.append(
                {
                    "priority": "high",
                    "action": "connect_calendar",
                    "message": "Connect your Google Calendar to enable calendar features",
                    "user_action": "Go to Settings > Connect Google Calendar",
                }
            )
        elif self.needs_attention():
            if self.connection_health == "expired":
                recommendations.append(
                    {
                        "priority": "high",
                        "action": "refresh_tokens",
                        "message": "Calendar access has expired and will be refreshed automatically",
                        "user_action": "No action needed - refresh in progress",
                    }
                )
            elif self.connection_health in ["failing", "invalid"]:
                recommendations.append(
                    {
                        "priority": "high",
                        "action": "reconnect_calendar",
                        "message": "Calendar connection needs to be refreshed",
                        "user_action": "Go to Settings > Reconnect Google Calendar",
                    }
                )
        elif not self.can_create_events:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "upgrade_permissions",
                    "message": "Calendar access is read-only. Reconnect to enable event creation",
                    "user_action": "Go to Settings > Reconnect Google Calendar with full permissions",
                }
            )
        elif self.calendars_accessible == 0:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "check_permissions",
                    "message": "No calendars are accessible. Check your Google Calendar permissions",
                    "user_action": "Go to Settings > Reconnect Google Calendar",
                }
            )

        return recommendations

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "connected": self.connected,
            "provider": self.provider,
            "scope": self.scope,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self.needs_refresh,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "connection_health": self.connection_health,
            "capabilities": self.get_capabilities(),
            "health_details": self.health_details,
            "health_summary": self.get_health_summary(),
            "time_until_expiry_minutes": self.time_until_expiry(),
            "recommendations": self.get_recommendations(),
        }


class CalendarAvailability:
    """Domain model for calendar availability information."""

    def __init__(
        self,
        is_free: bool,
        start_time: datetime,
        end_time: datetime,
        busy_periods: list[dict[str, Any]],
        calendars_checked: dict[str, Any],
        total_conflicts: int = 0,
    ):
        self.is_free = is_free
        self.start_time = start_time
        self.end_time = end_time
        self.busy_periods = busy_periods
        self.calendars_checked = calendars_checked
        self.total_conflicts = total_conflicts

    def duration_minutes(self) -> int:
        """Get total duration being checked in minutes."""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    def get_free_periods(self) -> list[dict[str, datetime]]:
        """Calculate free time periods within the checked range."""
        if not self.busy_periods:
            return [{"start": self.start_time, "end": self.end_time}]

        # Sort busy periods by start time
        sorted_busy = sorted(
            self.busy_periods,
            key=lambda p: datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
        )

        free_periods = []
        current_time = self.start_time

        for busy_period in sorted_busy:
            busy_start = datetime.fromisoformat(busy_period["start"].replace("Z", "+00:00"))
            busy_end = datetime.fromisoformat(busy_period["end"].replace("Z", "+00:00"))

            # Add free period before this busy period
            if current_time < busy_start:
                free_periods.append({"start": current_time, "end": busy_start})

            # Move current time to end of busy period
            current_time = max(current_time, busy_end)

        # Add final free period if any time remains
        if current_time < self.end_time:
            free_periods.append({"start": current_time, "end": self.end_time})

        return free_periods

    def get_largest_free_block_minutes(self) -> int:
        """Get the largest continuous free time block in minutes."""
        free_periods = self.get_free_periods()
        if not free_periods:
            return 0

        max_duration = 0
        for period in free_periods:
            delta = period["end"] - period["start"]
            duration = int(delta.total_seconds() / 60)
            max_duration = max(max_duration, duration)

        return max_duration

    def suggest_alternative_times(self, duration_minutes: int = 60) -> list[dict[str, datetime]]:
        """Suggest alternative time slots that can fit the requested duration."""
        free_periods = self.get_free_periods()
        suggestions = []

        for period in free_periods:
            period_duration = (period["end"] - period["start"]).total_seconds() / 60
            if period_duration >= duration_minutes:
                suggestions.append(
                    {
                        "start": period["start"],
                        "end": period["start"] + timedelta(minutes=duration_minutes),
                        "available_minutes": int(period_duration),
                    }
                )

        return suggestions[:3]  # Return top 3 suggestions

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "is_free": self.is_free,
            "time_range": {
                "start": self.start_time.isoformat(),
                "end": self.end_time.isoformat(),
                "duration_minutes": self.duration_minutes(),
            },
            "busy_periods": self.busy_periods,
            "calendars_checked": self.calendars_checked,
            "total_conflicts": self.total_conflicts,
            "free_periods": [
                {
                    "start": p["start"].isoformat(),
                    "end": p["end"].isoformat(),
                    "duration_minutes": int((p["end"] - p["start"]).total_seconds() / 60),
                }
                for p in self.get_free_periods()
            ],
            "largest_free_block_minutes": self.get_largest_free_block_minutes(),
        }
