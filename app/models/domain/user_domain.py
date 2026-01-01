# app/models/domain/user_domain.py - UPDATE THIS LINE

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Plan(BaseModel):
    """Domain model for subscription plan."""

    name: str
    max_daily_requests: int


class UserProfile(BaseModel):
    """Merged user profile (users + settings + plan)."""

    model_config = ConfigDict(extra="allow")

    user_id: str
    email: str
    display_name: str | None
    is_active: bool

    # UPDATED: Add email_style to onboarding steps
    timezone: str = "UTC"
    onboarding_completed: bool = False
    gmail_connected: bool = False
    onboarding_step: Literal["start", "profile", "gmail", "email_style", "vip_selection", "completed"] = "start"
    # Legacy compatibility field (frontend expects "step")
    step: str | None = None
    email_style_skipped: bool = False

    voice_preferences: dict[str, Any]
    plan: Plan
    created_at: datetime
    updated_at: datetime

    # Gmail health fields (optional, populated by user service)
    gmail_connection_health: str | None = None
    gmail_health_details: dict[str, Any] | None = None
    gmail_needs_attention: bool = False
    gmail_token_expires_at: datetime | None = None
    gmail_last_refresh_attempt: datetime | None = None
    gmail_needs_refresh: bool = False

    # Calendar connection status
    calendar_connected: bool = False

    # Runtime calendar health (not stored in DB)
    calendar_connection_health: str | None = None
    calendar_health_details: dict[str, Any] | None = None
    calendar_needs_attention: bool = False
    calendar_calendars_accessible: int = 0  # From live API call
    calendar_can_create_events: bool = False  # From live API call
