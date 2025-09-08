"""
User models for the voice Gmail assistant.
Pydantic models that match the database schema for user-related data.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BaseUser(BaseModel):
    """Core user fields from the database."""

    id: str = Field(..., description="User UUID from Supabase Auth")
    email: str = Field(..., description="User's email address")
    display_name: str | None = Field(None, description="User's display name")
    is_active: bool = Field(True, description="Whether the user account is active")
    created_at: datetime = Field(..., description="When the user was created")
    updated_at: datetime = Field(..., description="When the user was last updated")


class UserSettings(BaseModel):
    """User preferences and settings."""

    user_id: str = Field(..., description="Reference to user ID")
    voice_preferences: dict[str, Any] = Field(
        default_factory=lambda: {"tone": "professional", "speed": "normal"},
        description="Voice preferences stored as JSONB",
    )
    updated_at: datetime = Field(..., description="When settings were last updated")


class Plan(BaseModel):
    """Subscription plan details."""

    name: str = Field(..., description="Plan name (e.g., 'free', 'pro')")
    max_daily_requests: int = Field(..., description="Maximum requests per day")


class UserProfile(BaseModel):
    """Complete user profile combining user, settings, and plan data."""

    user_id: str = Field(..., description="User UUID")
    email: str = Field(..., description="User's email address")
    display_name: str | None = Field(None, description="User's display name")
    is_active: bool = Field(True, description="Whether the user account is active")
    voice_preferences: dict[str, Any] = Field(
        default_factory=lambda: {"tone": "professional", "speed": "normal"},
        description="Voice preferences",
    )
    plan: Plan = Field(..., description="User's subscription plan")
    created_at: datetime = Field(..., description="When the user was created")
    updated_at: datetime = Field(..., description="When the user was last updated")


class UserProfileResponse(BaseModel):
    """API response format for the /me endpoint."""

    profile: UserProfile = Field(..., description="Complete user profile data")
    auth: dict[str, Any] = Field(..., description="JWT authentication metadata")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
