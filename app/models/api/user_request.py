# app/models/api/user_request.py
from typing import Literal

from pydantic import BaseModel, Field


class OnboardingProfileUpdateRequest(BaseModel):
    """Request body for updating onboarding profile step."""

    display_name: str = Field(..., min_length=1, max_length=100)


# NEW: Email Style Selection Models
class EmailStyleSelectionRequest(BaseModel):
    """Request for selecting casual/professional email style."""

    style_type: Literal["casual", "professional"]


class CustomEmailStyleRequest(BaseModel):
    """Request for creating custom email style from examples."""

    email_examples: list[str] = Field(
        ..., min_items=3, max_items=3, description="Three full email examples (subject + body)"
    )
