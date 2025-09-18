# app/models/api/calendar_response.py
"""
Calendar API response models.
Used by routes for output formatting.
Follows the same pattern as oauth_response.py
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CalendarInfoResponse(BaseModel):
    """Response model for calendar information."""

    id: str = Field(..., description="Calendar ID")
    summary: str = Field(..., description="Calendar name")
    description: str = Field(default="", description="Calendar description")
    timezone: str = Field(..., description="Calendar timezone")
    access_role: str = Field(..., description="User's access role")
    primary: bool = Field(..., description="Is this the primary calendar")
    selected: bool = Field(..., description="Is this calendar selected")
    can_create_events: bool = Field(..., description="Can user create events in this calendar")
    color_id: str | None = Field(None, description="Calendar color ID")
    background_color: str | None = Field(None, description="Calendar background color")
    foreground_color: str | None = Field(None, description="Calendar text color")


class CalendarEventResponse(BaseModel):
    """Response model for calendar events."""

    id: str = Field(..., description="Event ID")
    summary: str = Field(..., description="Event title")
    description: str = Field(default="", description="Event description")
    start_time: datetime | None = Field(None, description="Event start time")
    end_time: datetime | None = Field(None, description="Event end time")
    timezone: str = Field(..., description="Event timezone")
    status: str = Field(..., description="Event status")
    location: str = Field(default="", description="Event location")
    is_all_day: bool = Field(..., description="Is this an all-day event")
    is_busy: bool = Field(..., description="Does this event block availability")
    attendees_count: int = Field(..., description="Number of attendees")
    created: datetime | None = Field(None, description="When event was created")
    updated: datetime | None = Field(None, description="When event was last updated")


class CalendarStatusResponse(BaseModel):
    """Response for calendar connection status."""

    connected: bool = Field(..., description="Whether calendar is connected")
    calendars_accessible: int = Field(..., description="Number of accessible calendars")
    primary_calendar_available: bool = Field(..., description="Is primary calendar accessible")
    can_create_events: bool = Field(..., description="Can user create events")
    connection_health: str = Field(..., description="Connection health status")
    health_details: dict[str, Any] = Field(..., description="Detailed health information")
    expires_at: datetime | None = Field(None, description="When tokens expire")
    needs_refresh: bool = Field(default=False, description="Whether tokens need refresh")


class CalendarsListResponse(BaseModel):
    """Response for listing calendars."""

    calendars: list[CalendarInfoResponse] = Field(..., description="List of accessible calendars")
    total_count: int = Field(..., description="Total number of calendars")
    primary_calendar: CalendarInfoResponse | None = Field(
        None, description="User's primary calendar"
    )
    writable_calendars: int = Field(..., description="Number of calendars user can write to")


class EventsListResponse(BaseModel):
    """Response for listing events."""

    events: list[CalendarEventResponse] = Field(..., description="List of events")
    total_count: int = Field(..., description="Total number of events found")
    time_range: dict[str, datetime] = Field(..., description="Time range queried")
    calendars_queried: list[str] = Field(..., description="Calendar IDs that were queried")
    has_more: bool = Field(default=False, description="Whether more events are available")


class AvailabilityResponse(BaseModel):
    """Response for availability checking."""

    is_free: bool = Field(..., description="Whether user is free during the time period")
    time_range: dict[str, datetime] = Field(..., description="Time period checked")
    busy_periods: list[dict[str, Any]] = Field(..., description="Conflicting busy periods")
    calendars_checked: dict[str, Any] = Field(..., description="Per-calendar availability status")
    total_conflicts: int = Field(..., description="Total number of conflicting events")
    recommendations: list[str] | None = Field(None, description="Scheduling recommendations")


class CreateEventResponse(BaseModel):
    """Response for event creation."""

    success: bool = Field(..., description="Whether event creation succeeded")
    event: CalendarEventResponse = Field(..., description="Created event details")
    message: str = Field(..., description="User-friendly success message")
    calendar_id: str = Field(..., description="Calendar where event was created")
    google_event_link: str | None = Field(None, description="Link to event in Google Calendar")


class UpdateEventResponse(BaseModel):
    """Response for event updates."""

    success: bool = Field(..., description="Whether event update succeeded")
    event: CalendarEventResponse = Field(..., description="Updated event details")
    message: str = Field(..., description="User-friendly success message")
    changes_made: list[str] = Field(..., description="List of fields that were changed")


class DeleteEventResponse(BaseModel):
    """Response for event deletion."""

    success: bool = Field(..., description="Whether event deletion succeeded")
    message: str = Field(..., description="User-friendly success message")
    event_id: str = Field(..., description="ID of deleted event")


class CalendarHealthResponse(BaseModel):
    """Response for calendar service health check."""

    healthy: bool = Field(..., description="Overall calendar service health")
    service: str = Field(default="calendar", description="Service name")
    timestamp: datetime = Field(..., description="Health check timestamp")

    # Component health
    google_calendar_api: dict[str, Any] = Field(..., description="Google Calendar API health")
    oauth_tokens: dict[str, Any] = Field(..., description="OAuth token system health")
    database_connectivity: dict[str, Any] = Field(..., description="Database health")

    # Capabilities
    supported_operations: list[str] = Field(..., description="Supported calendar operations")
    api_version: str = Field(default="v3", description="Google Calendar API version")

    # Issues and recommendations
    issues_found: list[str] = Field(default_factory=list, description="Issues detected")
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Health recommendations"
    )


class CalendarMetricsResponse(BaseModel):
    """Response for calendar system metrics."""

    timestamp: datetime = Field(..., description="Metrics timestamp")

    # User metrics
    total_users: int = Field(..., description="Total active users")
    calendar_connected_users: int = Field(..., description="Users with calendar connected")
    calendar_connection_rate: float = Field(..., description="Calendar connection rate percentage")

    # Usage metrics
    events_created_24h: int | None = Field(None, description="Events created in last 24 hours")
    availability_checks_24h: int | None = Field(
        None, description="Availability checks in last 24 hours"
    )

    # Health metrics
    healthy_connections: int = Field(..., description="Number of healthy calendar connections")
    connections_needing_attention: int = Field(default=0, description="Connections with issues")

    # API metrics
    api_success_rate: float | None = Field(None, description="Google Calendar API success rate")
    average_api_latency_ms: float | None = Field(None, description="Average API response time")


# Enhanced models for combined Gmail + Calendar responses
class GoogleServicesStatusResponse(BaseModel):
    """Combined status for Gmail + Calendar services."""

    connected: bool = Field(..., description="Whether any Google services are connected")
    provider: str = Field(default="google", description="OAuth provider")

    # Individual service status
    gmail: dict[str, Any] = Field(..., description="Gmail connection status")
    calendar: dict[str, Any] = Field(..., description="Calendar connection status")

    # Combined metrics
    services_connected: int = Field(..., description="Number of connected services")
    total_services: int = Field(default=2, description="Total available services")
    overall_health: str = Field(..., description="Overall connection health")

    # Token information
    expires_at: datetime | None = Field(None, description="When tokens expire")
    needs_refresh: bool = Field(default=False, description="Whether tokens need refresh")
    last_used: datetime | None = Field(None, description="Last service usage time")


class GoogleServicesHealthResponse(BaseModel):
    """Comprehensive health for Google services integration."""

    overall_healthy: bool = Field(..., description="Overall system health")
    timestamp: datetime = Field(..., description="Health check timestamp")

    # Service health
    gmail_health: dict[str, Any] = Field(..., description="Gmail service health")
    calendar_health: dict[str, Any] = Field(..., description="Calendar service health")
    oauth_system_health: dict[str, Any] = Field(..., description="OAuth system health")

    # Integration metrics
    total_scopes_configured: int = Field(..., description="Total OAuth scopes configured")
    gmail_scopes_count: int = Field(..., description="Gmail-specific scopes")
    calendar_scopes_count: int = Field(..., description="Calendar-specific scopes")

    # Issues and recommendations
    issues_found: list[str] = Field(default_factory=list, description="System issues detected")
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Improvement recommendations"
    )
