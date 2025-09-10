from pydantic import BaseModel, Field


class OnboardingProfileUpdateRequest(BaseModel):
    """Request body for updating onboarding profile step."""

    display_name: str = Field(..., min_length=1, max_length=100)
