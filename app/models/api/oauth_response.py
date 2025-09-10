# models/api/oauth_response.py
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GmailAuthURLResponse(BaseModel):
    """Response containing Gmail OAuth URL."""

    auth_url: str = Field(..., description="Gmail OAuth authorization URL")
    state: str = Field(..., description="OAuth state parameter")


class GmailAuthStatusResponse(BaseModel):
    """Response for Gmail connection status."""

    connected: bool = Field(..., description="Whether Gmail is connected")
    provider: Literal["google"] = "google"
    scope: str | None = None
    expires_at: datetime | None = None
    needs_refresh: bool = False


class GmailAuthCallbackResponse(BaseModel):
    """Response after Gmail OAuth callback."""

    success: bool
    message: str
    gmail_connected: bool
    next_step: Literal["completed"] | None = None
