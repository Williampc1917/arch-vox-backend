from datetime import datetime
from typing import Literal

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
