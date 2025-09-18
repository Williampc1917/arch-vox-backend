"""
Calendar API Routes
HTTP endpoints for calendar operations and status management.
"""

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.services.calendar_connection_service import (
    CalendarConnectionError,
    get_calendar_status,
    get_user_calendars,
    get_user_upcoming_events,
    check_user_availability,
    create_user_event,
    calendar_connection_health,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


# Request/Response Models
class AvailabilityRequest(BaseModel):
    start_time: datetime = Field(..., description="Start time to check")
    end_time: datetime = Field(..., description="End time to check")


class CreateEventRequest(BaseModel):
    summary: str = Field(..., description="Event title")
    start_time: datetime = Field(..., description="Event start time")
    end_time: datetime = Field(..., description="Event end time")
    description: str = Field(default="", description="Event description")
    location: str = Field(default="", description="Event location")


class CalendarStatusResponse(BaseModel):
    connected: bool
    calendars_accessible: int
    can_create_events: bool
    connection_health: str
    health_details: dict


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
            can_create_events=calendar_status.can_create_events,
            connection_health=calendar_status.connection_health,
            health_details=calendar_status.health_details,
        )
        
    except Exception as e:
        logger.error("Error getting calendar status", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get calendar status"
        )


@router.get("/calendars")
async def list_user_calendars(claims: dict = Depends(auth_dependency)):
    """List calendars accessible to user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        calendars = await get_user_calendars(user_id)
        return {"calendars": [cal.to_dict() for cal in calendars]}
        
    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error("Error listing calendars", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list calendars"
        )


@router.get("/events")
async def get_upcoming_events(
    claims: dict = Depends(auth_dependency),
    hours_ahead: int = Query(default=24, description="Hours ahead to look"),
    max_events: int = Query(default=10, description="Maximum events to return")
):
    """Get upcoming events for user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        events = await get_user_upcoming_events(user_id, hours_ahead, max_events)
        return {"events": [event.to_dict() for event in events]}
        
    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error("Error getting events", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get events"
        )


@router.post("/availability")
async def check_availability(
    request: AvailabilityRequest,
    claims: dict = Depends(auth_dependency)
):
    """Check if user is available during specified time."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        availability = await check_user_availability(user_id, request.start_time, request.end_time)
        return availability
        
    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error("Error checking availability", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check availability"
        )


@router.post("/events")
async def create_event(
    request: CreateEventRequest,
    claims: dict = Depends(auth_dependency)
):
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
        
        return {
            "success": True,
            "event": event.to_dict(),
            "message": f"Event '{request.summary}' created successfully"
        }
        
    except CalendarConnectionError as e:
        logger.error("Calendar connection error", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error("Error creating event", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create event"
        )


@router.get("/health")
async def calendar_health_check():
    """Health check for calendar system."""
    try:
        health = await calendar_connection_health()
        return health
    except Exception as e:
        logger.error("Calendar health check failed", error=str(e))
        return {
            "healthy": False,
            "service": "calendar",
            "error": str(e)
        }