# models/domain/oauth_domain.py
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class OAuthToken(BaseModel):
    """Domain model for OAuth tokens (decrypted)."""

    user_id: str
    provider: Literal["google"] = "google"
    access_token: str  # decrypted
    refresh_token: str | None = None  # decrypted
    scope: str
    expires_at: datetime | None = None
    updated_at: datetime

    def is_expired(self) -> bool:
        """Check if access token is expired."""
        if not self.expires_at:
            return False
        return datetime.utcnow() >= self.expires_at

    def needs_refresh(self, buffer_minutes: int = 5) -> bool:
        """Check if token should be refreshed soon."""
        if not self.expires_at:
            return False
        buffer_time = datetime.utcnow() + timedelta(minutes=buffer_minutes)
        return buffer_time >= self.expires_at
