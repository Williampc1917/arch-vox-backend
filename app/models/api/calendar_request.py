# app/models/api/calendar_request.py
"""
Calendar API request models.
Used by routes for input validation.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AvailabilityCheckRequest(BaseModel):
    """Request for checking calendar availability."""

    start_time: datetime = Field(..., description="Start time to check")
    end_time: datetime = Field(..., description="End time to check")
    calendar_ids: list[str] | None = Field(
        default=None, description="Specific calendar IDs to check (default: primary only)"
    )


class CreateEventRequest(BaseModel):
    """Request for creating a calendar event."""

    summary: str = Field(..., min_length=1, max_length=200, description="Event title")
    start_time: datetime = Field(..., description="Event start time")
    end_time: datetime = Field(..., description="Event end time")
    description: str = Field(default="", max_length=1000, description="Event description")
    location: str = Field(default="", max_length=500, description="Event location")
    calendar_id: str | None = Field(default=None, description="Target calendar ID")
    attendees: list[str] | None = Field(default=None, description="Attendee email addresses")
    timezone: str = Field(default="UTC", description="Event timezone")


class UpdateEventRequest(BaseModel):
    """Request for updating a calendar event."""

    summary: str | None = Field(None, min_length=1, max_length=200, description="New event title")
    start_time: datetime | None = Field(None, description="New start time")
    end_time: datetime | None = Field(None, description="New end time")
    description: str | None = Field(None, max_length=1000, description="New description")
    location: str | None = Field(None, max_length=500, description="New location")
    timezone: str | None = Field(None, description="New timezone")


class GetEventsRequest(BaseModel):
    """Request for getting calendar events with filters."""

    hours_ahead: int = Field(default=24, ge=1, le=168, description="Hours ahead to look (1-168)")
    max_events: int = Field(
        default=10, ge=1, le=100, description="Maximum events to return (1-100)"
    )
    calendar_ids: list[str] | None = Field(default=None, description="Specific calendars to query")
    include_all_day: bool = Field(default=True, description="Include all-day events")
    only_busy_events: bool = Field(default=False, description="Only events that show as busy")
