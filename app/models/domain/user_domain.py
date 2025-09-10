from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class Plan(BaseModel):
    """Domain model for subscription plan."""

    name: str
    max_daily_requests: int


class UserProfile(BaseModel):
    """Merged user profile (users + settings + plan)."""

    user_id: str
    email: str
    display_name: str | None
    is_active: bool

    # ADD THESE ONBOARDING FIELDS:
    timezone: str = "UTC"
    onboarding_completed: bool = False
    gmail_connected: bool = False
    onboarding_step: Literal["start", "profile", "gmail", "completed"] = "start"

    voice_preferences: dict[str, Any]
    plan: Plan
    created_at: datetime
    updated_at: datetime
