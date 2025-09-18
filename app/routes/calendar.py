"""
Calendar API Routes
HTTP endpoints for calendar operations and status management.
UPDATED: Now uses proper API request/response models following the established pattern.
"""

from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.calendar_request import (
    AvailabilityCheckRequest,
    CreateEventRequest,
)
from app.models.api.calendar_response import (
    AvailabilityResponse,
    CalendarEventResponse,
    CalendarHealthResponse,
    CalendarInfoResponse,
    CalendarsListResponse,
    CalendarStatusResponse,
    CreateEventResponse,
    EventsListResponse,
)
from app.services.calendar_operations_service import (
    CalendarConnectionError,
    calendar_connection_health,
    check_user_availability,
    create_user_event,
    get_calendar_status,
    get_user_calendars,
    get_user_upcoming_events,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/status", response_model=CalendarStatusResponse)
async def get_calendar_connection_status(claims: dict = Depends(auth_dependency)):
    """Get calendar connection status for authenticated user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        calendar_status = await get_calendar_status(user_id)

        return CalendarStatusResponse(
            connected=calendar_status.connected,
            calendars_accessible=calendar_status.calendars_accessible,
            primary_calendar_available=calendar_status.primary_calendar_available,
            can_create_events=calendar_status.can_create_events,
            connection_health=calendar_status.connection_health,
            health_details=calendar_status.health_details,
            expires_at=calendar_status.expires_at,
            needs_refresh=calendar_status.needs_refresh,
        )

    except Exception as e:
        logger.error("Error getting calendar status", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get calendar status",
        )


@router.get("/calendars", response_model=CalendarsListResponse)
async def list_user_calendars(claims: dict = Depends(auth_dependency)):
    """List calendars accessible to user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        calendars = await get_user_calendars(user_id)

        # Convert domain models to API response models
        calendar_responses = [
            CalendarInfoResponse(
                id=cal.id,
                summary=cal.summary,
                description=cal.description,
                timezone=cal.timezone,
                access_role=cal.access_role,
                primary=cal.primary,
                selected=cal.selected,
                can_create_events=cal.can_create_events(),
                color_id=cal.color_id,
                background_color=cal.background_color,
                foreground_color=cal.foreground_color,
            )
            for cal in calendars
        ]

        # Find primary calendar
        primary_calendar = next((cal for cal in calendar_responses if cal.primary), None)

        # Count writable calendars
        writable_count = sum(1 for cal in calendar_responses if cal.can_create_events)

        return CalendarsListResponse(
            calendars=calendar_responses,
            total_count=len(calendar_responses),
            primary_calendar=primary_calendar,
            writable_calendars=writable_count,
        )

    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error listing calendars", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list calendars"
        )


@router.get("/events", response_model=EventsListResponse)
async def get_upcoming_events(
    claims: dict = Depends(auth_dependency),
    hours_ahead: int = Query(default=24, ge=1, le=168, description="Hours ahead to look (1-168)"),
    max_events: int = Query(
        default=10, ge=1, le=100, description="Maximum events to return (1-100)"
    ),
    include_all_day: bool = Query(default=True, description="Include all-day events"),
    only_busy_events: bool = Query(default=False, description="Only events that show as busy"),
):
    """Get upcoming events for user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        events = await get_user_upcoming_events(user_id, hours_ahead, max_events)

        # Filter events based on request parameters
        filtered_events = events
        if not include_all_day:
            filtered_events = [e for e in filtered_events if not e.is_all_day()]
        if only_busy_events:
            filtered_events = [e for e in filtered_events if e.is_busy()]

        # Convert domain models to API response models
        event_responses = [
            CalendarEventResponse(
                id=event.id,
                summary=event.summary,
                description=event.description,
                start_time=event.start_time,
                end_time=event.end_time,
                timezone=event.timezone,
                status=event.status,
                location=event.location,
                is_all_day=event.is_all_day(),
                is_busy=event.is_busy(),
                attendees_count=len(event.attendees),
                created=event.created,
                updated=event.updated,
            )
            for event in filtered_events
        ]

        # Calculate time range
        from datetime import datetime

        now = datetime.now(UTC)
        time_max = now + timedelta(hours=hours_ahead)

        return EventsListResponse(
            events=event_responses,
            total_count=len(event_responses),
            time_range={"start": now, "end": time_max},
            calendars_queried=["primary"],  # Could be expanded to support multiple calendars
            has_more=len(events) >= max_events,  # Indicates if there might be more events
        )

    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting events", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get events"
        )


@router.post("/availability", response_model=AvailabilityResponse)
async def check_availability(
    request: AvailabilityCheckRequest, claims: dict = Depends(auth_dependency)
):
    """Check if user is available during specified time."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        availability = await check_user_availability(user_id, request.start_time, request.end_time)

        # Generate recommendations based on availability
        recommendations = []
        if not availability["is_free"]:
            recommendations.append("Consider scheduling at a different time")
            if availability["total_conflicts"] == 1:
                recommendations.append("There is 1 conflicting event")
            else:
                recommendations.append(
                    f"There are {availability['total_conflicts']} conflicting events"
                )

        return AvailabilityResponse(
            is_free=availability["is_free"],
            time_range={
                "start": request.start_time,
                "end": request.end_time,
            },
            busy_periods=availability["busy_periods"],
            calendars_checked=availability["calendars_checked"],
            total_conflicts=availability["total_conflicts"],
            recommendations=recommendations if recommendations else None,
        )

    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error checking availability", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to check availability"
        )


@router.post("/events", response_model=CreateEventResponse)
async def create_event(request: CreateEventRequest, claims: dict = Depends(auth_dependency)):
    """Create new calendar event."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        event = await create_user_event(
            user_id,
            request.summary,
            request.start_time,
            request.end_time,
            request.description,
            request.location,
        )

        # Convert domain model to API response model
        event_response = CalendarEventResponse(
            id=event.id,
            summary=event.summary,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            timezone=event.timezone,
            status=event.status,
            location=event.location,
            is_all_day=event.is_all_day(),
            is_busy=event.is_busy(),
            attendees_count=len(event.attendees),
            created=event.created,
            updated=event.updated,
        )

        # Generate Google Calendar link (if event ID is available)
        google_link = None
        if event.id and hasattr(event, "raw_data"):
            google_link = event.raw_data.get("htmlLink")

        return CreateEventResponse(
            success=True,
            event=event_response,
            message=f"Event '{request.summary}' created successfully",
            calendar_id=request.calendar_id or "primary",
            google_event_link=google_link,
        )

    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error creating event", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create event"
        )


@router.get("/health", response_model=CalendarHealthResponse)
async def calendar_health_check():
    """Health check for calendar system."""
    try:
        from datetime import datetime

        health = await calendar_connection_health()

        # Structure the health response according to our API model
        return CalendarHealthResponse(
            healthy=health.get("healthy", False),
            service="calendar",
            timestamp=datetime.now(UTC),
            google_calendar_api={
                "healthy": health.get("calendar_api_connectivity") == "ok",
                "connectivity": health.get("calendar_api_connectivity", "unknown"),
            },
            oauth_tokens={
                "healthy": True,  # Would need more detailed check
                "system_operational": True,
            },
            database_connectivity={
                "healthy": health.get("database_connectivity") == "ok",
                "status": health.get("database_connectivity", "unknown"),
            },
            supported_operations=health.get(
                "capabilities",
                [
                    "get_connection_status",
                    "get_user_calendars",
                    "get_upcoming_events",
                    "check_availability",
                    "create_event",
                ],
            ),
            api_version="v3",
            issues_found=[],  # Would be populated based on health check results
            recommendations=[],  # Would be generated based on issues found
        )

    except Exception as e:
        logger.error("Calendar health check failed", error=str(e))
        return CalendarHealthResponse(
            healthy=False,
            service="calendar",
            timestamp=datetime.now(UTC),
            google_calendar_api={"healthy": False, "error": str(e)},
            oauth_tokens={"healthy": False, "error": "Health check failed"},
            database_connectivity={"healthy": False, "error": str(e)},
            supported_operations=[],
            api_version="v3",
            issues_found=[f"Health check failed: {str(e)}"],
            recommendations=[{"priority": "high", "action": "Check service logs"}],
        )
