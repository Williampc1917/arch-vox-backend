# app/models/api/user_response.py
from datetime import datetime
from typing import (
    Any,  # Add Any if it's missing
    Literal,
)

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

    step: Literal["start", "profile", "gmail", "completed"]
    onboarding_completed: bool
    gmail_connected: bool
    timezone: str


class OnboardingProfileUpdateResponse(BaseModel):
    """Response for PUT /onboarding/profile"""

    success: bool
    next_step: Literal["gmail"]
    message: str


class OnboardingCompleteResponse(BaseModel):
    """Response for POST /onboarding/complete"""

    success: bool
    message: str
    user_profile: UserProfile


# Add these to the END of app/models/api/user_response.py


class EmailStyleStatusResponse(BaseModel):
    """Response for GET /onboarding/email-style"""

    current_step: str
    style_selected: str | None
    available_options: list[dict[str, Any]]
    can_advance: bool
    rate_limit_info: dict[str, Any] | None


class EmailStyleSelectionResponse(BaseModel):
    """Response for PUT /onboarding/email-style (casual/professional)"""

    success: bool
    style_type: str
    next_step: Literal["completed"]
    message: str


class CustomEmailStyleResponse(BaseModel):
    """Response for POST /onboarding/email-style/custom"""

    success: bool
    style_profile: dict[str, Any] | None
    extraction_grade: str | None
    error_message: str | None
    rate_limit_info: dict[str, Any] | None
    next_step: Literal["completed"] | None
