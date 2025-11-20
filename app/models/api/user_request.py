# app/models/api/user_request.py
# app/models/api/user_request.py
from pydantic import BaseModel, Field


class OnboardingProfileUpdateRequest(BaseModel):
    """Request body for updating onboarding profile step."""

    display_name: str = Field(..., min_length=1, max_length=100)


# 3-Profile Email Style Request (replaces old list-based request)
class CustomEmailStyleRequest(BaseModel):
    """Request for creating 3 email styles from labeled examples."""
    
    professional_email: str = Field(
        ..., 
        min_length=50,
        description="Professional email example (subject + body)"
    )
    casual_email: str = Field(
        ..., 
        min_length=50,
        description="Casual email example (subject + body)"
    )
    friendly_email: str = Field(
        ..., 
        min_length=50,
        description="Friendly email example (subject + body)"
    )
