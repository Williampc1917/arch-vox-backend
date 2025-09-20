# models/api/oauth_response.py
"""
Updated OAuth API response models with Gmail + Calendar support.
ENHANCED: Now includes Calendar connection status and permissions.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ServiceStatus(BaseModel):
    """Status for individual Google service (Gmail or Calendar)."""

    connected: bool = Field(..., description="Whether service is connected")
    permissions_valid: bool = Field(default=False, description="Whether permissions are sufficient")
    scopes: list[str] = Field(default_factory=list, description="Granted scopes for this service")
    health_status: str = Field(default="unknown", description="Health status of service connection")
    last_used: datetime | None = Field(default=None, description="Last time service was accessed")


class GoogleServicesAuthURLResponse(BaseModel):
    """Response containing Google OAuth URL for Gmail + Calendar access."""

    auth_url: str = Field(..., description="Google OAuth authorization URL")
    state: str = Field(..., description="OAuth state parameter")
    requested_services: list[str] = Field(
        default=["Gmail", "Calendar"], description="Services that will be connected"
    )
    total_scopes: int = Field(..., description="Total number of permission scopes requested")


class GoogleServicesAuthStatusResponse(BaseModel):
    """Response for Gmail + Calendar connection status."""

    connected: bool = Field(..., description="Whether any Google services are connected")
    provider: Literal["google"] = "google"

    # Service-specific status
    gmail: ServiceStatus = Field(..., description="Gmail connection status")
    calendar: ServiceStatus = Field(..., description="Calendar connection status")

    # Overall token information
    expires_at: datetime | None = Field(default=None, description="When tokens expire")
    needs_refresh: bool = Field(default=False, description="Whether tokens need refresh")

    # Health summary
    overall_health: str = Field(default="unknown", description="Overall connection health")
    services_connected: int = Field(..., description="Number of connected services")
    total_services: int = Field(default=2, description="Total available services")


class GoogleServicesAuthCallbackResponse(BaseModel):
    """Response after Google OAuth callback for Gmail + Calendar."""

    success: bool = Field(..., description="Whether connection was successful")
    message: str = Field(..., description="User-friendly status message")

    # Connected services summary
    services_connected: list[str] = Field(..., description="Successfully connected services")
    services_failed: list[str] = Field(
        default_factory=list, description="Services that failed to connect"
    )

    # Next steps
    next_step: Literal["completed", "partial_setup"] | None = Field(
        default=None, description="Next step in onboarding process"
    )

    # Connection details
    gmail_connected: bool = Field(..., description="Whether Gmail was connected")
    calendar_connected: bool = Field(default=False, description="Whether Calendar was connected")

    # Recommendations if partial connection
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Recommendations if connection is incomplete"
    )


class ServiceHealthDetails(BaseModel):
    """Detailed health information for a service."""

    status: str = Field(..., description="Health status")
    message: str = Field(..., description="Health status message")
    permissions: dict[str, bool] = Field(default_factory=dict, description="Permission breakdown")
    scopes_granted: list[str] = Field(default_factory=list, description="Currently granted scopes")
    scopes_missing: list[str] = Field(default_factory=list, description="Missing required scopes")
    last_checked: datetime | None = Field(default=None, description="Last health check time")


class GoogleServicesHealthResponse(BaseModel):
    """Comprehensive health response for Google services."""

    overall_healthy: bool = Field(..., description="Overall system health")
    timestamp: datetime = Field(..., description="Health check timestamp")

    # Individual service health
    gmail_health: ServiceHealthDetails = Field(..., description="Gmail service health")
    calendar_health: ServiceHealthDetails = Field(..., description="Calendar service health")

    # System components
    oauth_system_healthy: bool = Field(..., description="OAuth system health")
    token_system_healthy: bool = Field(..., description="Token management health")

    # Metrics
    total_scopes_configured: int = Field(..., description="Total configured OAuth scopes")
    services_available: int = Field(default=2, description="Number of available services")

    # Issues and recommendations
    issues_found: list[str] = Field(default_factory=list, description="Issues detected")
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Recommendations for resolving issues"
    )


class OAuthPermissionValidationResponse(BaseModel):
    """Response for OAuth permission validation."""

    valid: bool = Field(..., description="Whether all permissions are valid")
    user_id: str = Field(..., description="User ID")

    # Service validation
    gmail_valid: bool = Field(..., description="Whether Gmail permissions are valid")
    calendar_valid: bool = Field(..., description="Whether Calendar permissions are valid")

    # Detailed permission breakdown
    gmail_permissions: dict[str, bool] = Field(..., description="Gmail permission details")
    calendar_permissions: dict[str, bool] = Field(..., description="Calendar permission details")

    # Missing permissions
    missing_gmail_permissions: list[str] = Field(default_factory=list)
    missing_calendar_permissions: list[str] = Field(default_factory=list)

    # Token health
    token_health: dict[str, Any] = Field(..., description="Token health status")

    # Actionable recommendations
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Actionable recommendations for user"
    )


class GoogleServicesMetricsResponse(BaseModel):
    """System metrics for Google services OAuth."""

    timestamp: datetime = Field(..., description="Metrics timestamp")

    # User metrics
    total_users: int = Field(..., description="Total active users")
    gmail_connected_users: int = Field(..., description="Users with Gmail connected")
    calendar_connected_users: int = Field(default=0, description="Users with Calendar connected")
    both_services_users: int = Field(default=0, description="Users with both services connected")

    # Connection rates
    gmail_connection_rate: float = Field(..., description="Gmail connection rate percentage")
    calendar_connection_rate: float = Field(
        default=0.0, description="Calendar connection rate percentage"
    )
    full_connection_rate: float = Field(
        default=0.0, description="Both services connection rate percentage"
    )

    # Health metrics
    healthy_connections: int = Field(..., description="Number of healthy connections")
    connections_needing_attention: int = Field(
        default=0, description="Connections needing attention"
    )

    # System health
    oauth_system_healthy: bool = Field(..., description="OAuth system health")
    average_token_health_score: float = Field(default=0.0, description="Average token health score")

    # Scope metrics
    total_scopes_configured: int = Field(..., description="Total OAuth scopes configured")
    gmail_scopes_count: int = Field(..., description="Gmail scopes count")
    calendar_scopes_count: int = Field(..., description="Calendar scopes count")


# Updated existing models to maintain compatibility
class GmailAuthURLResponse(GoogleServicesAuthURLResponse):
    """Legacy Gmail-only auth URL response (deprecated - use GoogleServicesAuthURLResponse)."""

    pass


class GmailAuthStatusResponse(BaseModel):
    """Legacy Gmail-only status response (deprecated - use GoogleServicesAuthStatusResponse)."""

    connected: bool = Field(..., description="Whether Gmail is connected")
    provider: Literal["google"] = "google"
    scope: str | None = None
    expires_at: datetime | None = None
    needs_refresh: bool = False


class GmailAuthCallbackResponse(BaseModel):
    """Legacy Gmail-only callback response (deprecated - use GoogleServicesAuthCallbackResponse)."""

    success: bool
    message: str
    gmail_connected: bool
    next_step: (
        Literal["stay_on_gmail", "redirect_to_main_app", "go_to_profile_step", "completed"] | None
    ) = None
    onboarding_completed: bool = Field(
        default=False, description="Whether onboarding is now complete"
    )
