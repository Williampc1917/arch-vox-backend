# models/api/oauth_request.py
from pydantic import BaseModel, Field


class GmailAuthCallbackRequest(BaseModel):
    """Request for Gmail OAuth callback."""

    code: str = Field(..., description="Authorization code from OAuth flow")
    state: str = Field(..., description="OAuth state for security validation")
