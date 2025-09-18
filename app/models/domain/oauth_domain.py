# models/domain/oauth_domain.py
"""
Updated OAuth Token Domain Model with Calendar Support.
ENHANCED: Now includes Calendar permissions validation and health checks.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class OAuthToken(BaseModel):
    """Domain model for OAuth tokens (decrypted) with Gmail + Calendar support."""

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
        return datetime.now(UTC) >= self.expires_at

    def needs_refresh(self, buffer_minutes: int = 5) -> bool:
        """Check if token should be refreshed soon."""
        if not self.expires_at:
            return False
        buffer_time = datetime.now(UTC) + timedelta(minutes=buffer_minutes)
        return buffer_time >= self.expires_at

    def has_gmail_access(self) -> bool:
        """Check if token has Gmail API access."""
        gmail_indicators = ["gmail.readonly", "gmail.send", "gmail.compose", "gmail.modify"]
        return any(indicator in self.scope for indicator in gmail_indicators)

    def has_calendar_access(self) -> bool:
        """Check if token has Calendar API access."""
        calendar_indicators = ["calendar.readonly", "calendar.events", "calendar"]
        return any(indicator in self.scope for indicator in calendar_indicators)

    def get_gmail_scopes(self) -> list[str]:
        """Get list of Gmail-specific scopes."""
        scopes = self.scope.split() if self.scope else []
        return [scope for scope in scopes if "gmail" in scope]

    def get_calendar_scopes(self) -> list[str]:
        """Get list of Calendar-specific scopes."""
        scopes = self.scope.split() if self.scope else []
        return [scope for scope in scopes if "calendar" in scope]

    def get_scope_breakdown(self) -> dict:
        """Get detailed breakdown of all granted scopes."""
        gmail_scopes = self.get_gmail_scopes()
        calendar_scopes = self.get_calendar_scopes()
        
        return {
            "gmail": {
                "scopes": gmail_scopes,
                "count": len(gmail_scopes),
                "has_access": len(gmail_scopes) > 0,
                "permissions": {
                    "read": any("readonly" in scope for scope in gmail_scopes),
                    "send": any("send" in scope for scope in gmail_scopes),
                    "compose": any("compose" in scope for scope in gmail_scopes),
                    "modify": any("modify" in scope for scope in gmail_scopes),
                }
            },
            "calendar": {
                "scopes": calendar_scopes,
                "count": len(calendar_scopes),
                "has_access": len(calendar_scopes) > 0,
                "permissions": {
                    "read": any("readonly" in scope for scope in calendar_scopes),
                    "events": any("events" in scope for scope in calendar_scopes),
                    "full": any(scope.endswith("calendar") for scope in calendar_scopes),
                }
            },
            "total_scopes": len(self.scope.split()) if self.scope else 0,
        }

    def validate_required_permissions(self) -> dict:
        """
        Validate that token has all required permissions for triage app.
        
        Returns:
            dict: Validation results with missing permissions
        """
        scope_breakdown = self.get_scope_breakdown()
        
        # Required Gmail permissions
        gmail_requirements = {
            "read": scope_breakdown["gmail"]["permissions"]["read"],
            "send": scope_breakdown["gmail"]["permissions"]["send"],
            "compose": scope_breakdown["gmail"]["permissions"]["compose"],
            "modify": scope_breakdown["gmail"]["permissions"]["modify"],
        }
        
        # Required Calendar permissions
        calendar_requirements = {
            "read": scope_breakdown["calendar"]["permissions"]["read"],
            "events": scope_breakdown["calendar"]["permissions"]["events"],
        }
        
        # Check for missing permissions
        missing_gmail = [perm for perm, has_perm in gmail_requirements.items() if not has_perm]
        missing_calendar = [perm for perm, has_perm in calendar_requirements.items() if not has_perm]
        
        return {
            "valid": len(missing_gmail) == 0 and len(missing_calendar) == 0,
            "gmail_valid": len(missing_gmail) == 0,
            "calendar_valid": len(missing_calendar) == 0,
            "missing_gmail_permissions": missing_gmail,
            "missing_calendar_permissions": missing_calendar,
            "gmail_permissions": gmail_requirements,
            "calendar_permissions": calendar_requirements,
            "scope_breakdown": scope_breakdown,
        }

    def get_health_status(self) -> dict:
        """
        Get comprehensive health status for Gmail + Calendar access.
        
        Returns:
            dict: Health status with recommendations
        """
        now = datetime.now(UTC)
        
        # Basic token health
        is_expired = self.is_expired()
        needs_refresh = self.needs_refresh(buffer_minutes=60)  # 1 hour buffer
        
        # Permission validation
        permission_validation = self.validate_required_permissions()
        
        # Time-based status
        if is_expired:
            status = "expired"
            message = "Access token has expired"
            action_required = "Token refresh required"
            severity = "high"
        elif needs_refresh:
            status = "expiring_soon"
            time_left = (self.expires_at - now).total_seconds() / 60  # minutes
            message = f"Access token expires in {int(time_left)} minutes"
            action_required = "Token refresh recommended"
            severity = "medium"
        elif not permission_validation["valid"]:
            status = "insufficient_permissions"
            missing_services = []
            if not permission_validation["gmail_valid"]:
                missing_services.append("Gmail")
            if not permission_validation["calendar_valid"]:
                missing_services.append("Calendar")
            message = f"Missing required permissions for {', '.join(missing_services)}"
            action_required = "Re-authenticate to grant missing permissions"
            severity = "high"
        else:
            status = "healthy"
            hours_left = (self.expires_at - now).total_seconds() / 3600 if self.expires_at else 0
            message = f"All permissions granted, expires in {int(hours_left)} hours"
            action_required = None
            severity = "none"
        
        return {
            "status": status,
            "message": message,
            "action_required": action_required,
            "severity": severity,
            "details": {
                "is_expired": is_expired,
                "needs_refresh": needs_refresh,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "permission_validation": permission_validation,
                "has_gmail_access": self.has_gmail_access(),
                "has_calendar_access": self.has_calendar_access(),
            }
        }


class EnhancedConnectionStatus:
    """Enhanced connection status for Gmail + Calendar services."""

    def __init__(
        self,
        connected: bool,
        user_id: str,
        provider: str = "google",
        gmail_scope: str | None = None,
        calendar_scope: str | None = None,
        expires_at: datetime | None = None,
        needs_refresh: bool = False,
        last_used: datetime | None = None,
        connection_health: str = "unknown",
        gmail_health: dict | None = None,
        calendar_health: dict | None = None,
    ):
        self.connected = connected
        self.user_id = user_id
        self.provider = provider
        self.gmail_scope = gmail_scope
        self.calendar_scope = calendar_scope
        self.expires_at = expires_at
        self.needs_refresh = needs_refresh
        self.last_used = last_used
        self.connection_health = connection_health
        self.gmail_health = gmail_health or {}
        self.calendar_health = calendar_health or {}

    @property
    def has_gmail(self) -> bool:
        """Check if Gmail access is available."""
        return bool(self.gmail_scope and self.connected)

    @property
    def has_calendar(self) -> bool:
        """Check if Calendar access is available."""
        return bool(self.calendar_scope and self.connected)

    @property
    def is_fully_connected(self) -> bool:
        """Check if both Gmail and Calendar are connected."""
        return self.has_gmail and self.has_calendar

    def get_missing_services(self) -> list[str]:
        """Get list of missing services."""
        missing = []
        if not self.has_gmail:
            missing.append("Gmail")
        if not self.has_calendar:
            missing.append("Calendar")
        return missing

    def get_overall_health(self) -> dict:
        """Get combined health status for both services."""
        gmail_status = self.gmail_health.get("status", "unknown")
        calendar_status = self.calendar_health.get("status", "unknown")
        
        # Determine overall status priority: error > expired > expiring > healthy
        status_priority = {
            "error": 4,
            "expired": 3,
            "insufficient_permissions": 3,
            "expiring_soon": 2,
            "healthy": 1,
            "unknown": 0,
        }
        
        gmail_priority = status_priority.get(gmail_status, 0)
        calendar_priority = status_priority.get(calendar_status, 0)
        
        if gmail_priority >= calendar_priority:
            overall_status = gmail_status
            primary_service = "Gmail"
        else:
            overall_status = calendar_status
            primary_service = "Calendar"
        
        # Determine if action is needed
        needs_attention = overall_status in ["error", "expired", "insufficient_permissions"]
        
        return {
            "overall_status": overall_status,
            "primary_concern": primary_service if needs_attention else None,
            "needs_attention": needs_attention,
            "gmail_status": gmail_status,
            "calendar_status": calendar_status,
            "services_connected": {
                "gmail": self.has_gmail,
                "calendar": self.has_calendar,
                "both": self.is_fully_connected,
            },
            "missing_services": self.get_missing_services(),
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        overall_health = self.get_overall_health()
        
        return {
            "connected": self.connected,
            "provider": self.provider,
            "services": {
                "gmail": {
                    "connected": self.has_gmail,
                    "scope": self.gmail_scope,
                    "health": self.gmail_health,
                },
                "calendar": {
                    "connected": self.has_calendar,
                    "scope": self.calendar_scope,
                    "health": self.calendar_health,
                },
            },
            "overall_health": overall_health,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self.needs_refresh,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "connection_health": self.connection_health,
        }