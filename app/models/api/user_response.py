# app/models/api/user_response.py
# app/models/api/user_response.py
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.domain.user_domain import UserProfile


class AuthMeta(BaseModel):
    """Auth metadata extracted from JWT claims."""

    user_id: str
    email: str | None = None
    role: str | None = "authenticated"
    aud: str | None = None
    iat: int | None = None
    exp: int | None = None


class UserProfileResponse(BaseModel):
    """API response for /me endpoint."""

    profile: UserProfile = Field(..., description="Complete user profile data")
    auth: AuthMeta = Field(..., description="JWT authentication metadata")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class OnboardingStatusResponse(BaseModel):
    """Response for GET /onboarding/status"""

    step: str  # Changed from Literal to support email_style
    onboarding_completed: bool
    gmail_connected: bool
    timezone: str
    email_style_skipped: bool
    completed_at: datetime | None = Field(
        None, description="Timestamp when onboarding completed (if finished)"
    )


class OnboardingProfileUpdateResponse(BaseModel):
    """Response for PUT /onboarding/profile"""

    success: bool
    next_step: str  # Changed from Literal to be more flexible
    message: str


class OnboardingCompleteResponse(BaseModel):
    """Response for POST /onboarding/complete"""

    success: bool
    message: str
    user_profile: UserProfile


class EmailStyleSkipResponse(BaseModel):
    """Response for POST /onboarding/email-style/skip"""

    success: bool
    message: str
    user_profile: UserProfile
    next_step: str | None = Field(
        None, description="Next onboarding step (completed if skip finishes onboarding)"
    )
    onboarding_completed: bool | None = Field(
        None, description="Whether onboarding is now completed"
    )


# UPDATED: Email Style Status Response (shows 3-profile status)
class EmailStyleStatusResponse(BaseModel):
    """Response for GET /onboarding/email-style"""

    current_step: str
    styles_created: dict[str, bool] = Field(
        ...,
        description="Status of each style: {'professional': true, 'casual': false, 'friendly': true}",
    )
    all_styles_complete: bool = Field(..., description="True if all 3 styles exist")
    can_advance: bool = Field(..., description="True if user can complete onboarding")
    rate_limit_info: dict[str, Any] | None = Field(
        None, description="Rate limiting information for custom style creation"
    )


# UPDATED: Custom Email Style Response (returns 3 profiles)
class CustomEmailStyleResponse(BaseModel):
    """Response for POST /onboarding/email-style/custom"""

    success: bool
    style_profiles: dict[str, Any] | None = Field(
        None,
        description="All 3 style profiles: {'professional': {...}, 'casual': {...}, 'friendly': {...}}",
    )
    extraction_grades: dict[str, str] | None = Field(
        None,
        description="Grade for each profile: {'professional': 'A', 'casual': 'B', 'friendly': 'A'}",
    )
    error_message: str | None = Field(None, description="Error message if extraction failed")
    rate_limit_info: dict[str, Any] | None = Field(
        None, description="Rate limit details if blocked"
    )
    next_step: str | None = Field(None, description="Next onboarding step (usually 'completed')")
