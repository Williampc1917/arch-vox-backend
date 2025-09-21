"""
Gmail API response models.
Used by routes for output formatting.
Mirrors the pattern established in calendar_response.py
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GmailMessageResponse(BaseModel):
    """Response model for Gmail messages."""

    id: str = Field(..., description="Message ID")
    thread_id: str = Field(..., description="Thread ID")
    subject: str = Field(..., description="Message subject")
    sender: dict[str, str] = Field(..., description="Sender info (name, email)")
    recipient: dict[str, str] = Field(..., description="Primary recipient info")
    cc: list[dict[str, str]] = Field(default_factory=list, description="CC recipients")
    bcc: list[dict[str, str]] = Field(default_factory=list, description="BCC recipients")
    snippet: str = Field(..., description="Message snippet/preview")
    body_text: str = Field(default="", description="Plain text body")
    body_html: str | None = Field(None, description="HTML body")
    attachments: list[dict[str, Any]] = Field(default_factory=list, description="Attachments info")
    labels: list[str] = Field(..., description="Applied label IDs")
    is_unread: bool = Field(..., description="Whether message is unread")
    is_starred: bool = Field(..., description="Whether message is starred")
    is_important: bool = Field(..., description="Whether message is important")
    has_attachments: bool = Field(..., description="Whether message has attachments")
    received_datetime: datetime | None = Field(None, description="When message was received")
    sender_display: str = Field(..., description="Formatted sender display")
    body_preview: str = Field(..., description="Truncated body for preview")
    size_estimate: int = Field(default=0, description="Message size estimate")
    age_description: str = Field(..., description="Human-readable age (e.g., '2 hours ago')")
    priority_level: str = Field(..., description="Message priority (high, medium, normal)")
    is_actionable: bool = Field(..., description="Whether message likely requires action")


class GmailThreadResponse(BaseModel):
    """Response model for Gmail conversation threads."""

    id: str = Field(..., description="Thread ID")
    snippet: str = Field(..., description="Thread snippet")
    subject: str = Field(..., description="Thread subject")
    message_count: int = Field(..., description="Number of messages in thread")
    has_unread: bool = Field(..., description="Whether thread has unread messages")
    participants: list[dict[str, str]] = Field(..., description="Thread participants")
    latest_message: GmailMessageResponse | None = Field(None, description="Most recent message")
    messages: list[GmailMessageResponse] = Field(..., description="All messages in thread")


class GmailLabelResponse(BaseModel):
    """Response model for Gmail labels."""

    id: str = Field(..., description="Label ID")
    name: str = Field(..., description="Label name")
    display_name: str = Field(..., description="User-friendly display name")
    type: str = Field(..., description="Label type (system or user)")
    is_system: bool = Field(..., description="Whether this is a system label")
    messages_total: int = Field(default=0, description="Total messages with this label")
    messages_unread: int = Field(default=0, description="Unread messages with this label")
    threads_total: int = Field(default=0, description="Total threads with this label")
    threads_unread: int = Field(default=0, description="Unread threads with this label")


class GmailStatusResponse(BaseModel):
    """Response for Gmail connection status."""

    connected: bool = Field(..., description="Whether Gmail is connected")
    messages_accessible: int = Field(..., description="Number of accessible messages")
    connection_health: str = Field(..., description="Connection health status")
    can_send_email: bool = Field(..., description="Can user send emails")
    can_read_email: bool = Field(..., description="Can user read emails")
    expires_at: datetime | None = Field(None, description="When tokens expire")
    needs_refresh: bool = Field(default=False, description="Whether tokens need refresh")
    health_details: dict[str, Any] = Field(..., description="Detailed health information")
    quota_remaining: dict[str, Any] | None = Field(None, description="API quota information")


class MessagesListResponse(BaseModel):
    """Response for listing messages."""

    messages: list[GmailMessageResponse] = Field(..., description="List of messages")
    total_found: int = Field(..., description="Total number of messages found")
    query_parameters: dict[str, Any] = Field(..., description="Query parameters used")
    has_more: bool = Field(default=False, description="Whether more messages are available")
    next_page_token: str | None = Field(None, description="Token for next page")


class SearchResultsResponse(BaseModel):
    """Response for message search results."""

    messages: list[GmailMessageResponse] = Field(..., description="Search result messages")
    query: str = Field(..., description="Search query used")
    total_found: int = Field(..., description="Total results found")
    has_more: bool = Field(default=False, description="Whether more results available")
    unread_count: int = Field(..., description="Unread messages in results")
    high_priority_count: int = Field(..., description="High priority messages in results")
    actionable_count: int = Field(..., description="Actionable messages in results")


class SendEmailResponse(BaseModel):
    """Response for sending email."""

    success: bool = Field(..., description="Whether email was sent successfully")
    message_id: str = Field(..., description="ID of sent message")
    thread_id: str | None = Field(None, description="Thread ID if reply")
    message: str = Field(..., description="User-friendly success message")
    recipients: dict[str, list[str]] = Field(..., description="Email recipients breakdown")


class ModifyMessageResponse(BaseModel):
    """Response for modifying message labels."""

    success: bool = Field(..., description="Whether modification succeeded")
    message: GmailMessageResponse = Field(..., description="Updated message")
    changes_made: list[str] = Field(..., description="Labels that were modified")
    message_text: str = Field(..., description="User-friendly success message")


class DeleteMessageResponse(BaseModel):
    """Response for deleting message."""

    success: bool = Field(..., description="Whether deletion succeeded")
    message_id: str = Field(..., description="ID of deleted message")
    message: str = Field(..., description="User-friendly success message")


class LabelsListResponse(BaseModel):
    """Response for listing labels."""

    labels: list[GmailLabelResponse] = Field(..., description="List of Gmail labels")
    system_labels: list[GmailLabelResponse] = Field(..., description="System labels only")
    user_labels: list[GmailLabelResponse] = Field(..., description="User-created labels only")
    total_count: int = Field(..., description="Total number of labels")


class CreateDraftResponse(BaseModel):
    """Response for creating draft."""

    success: bool = Field(..., description="Whether draft creation succeeded")
    draft_id: str = Field(..., description="Created draft ID")
    message: str = Field(..., description="User-friendly success message")
    draft_info: dict[str, Any] = Field(..., description="Draft details")


class GmailHealthResponse(BaseModel):
    """Response for Gmail service health check."""

    healthy: bool = Field(..., description="Overall Gmail service health")
    service: str = Field(default="gmail", description="Service name")
    timestamp: datetime = Field(..., description="Health check timestamp")

    # Component health
    google_gmail_api: dict[str, Any] = Field(..., description="Google Gmail API health")
    oauth_tokens: dict[str, Any] = Field(..., description="OAuth token system health")
    database_connectivity: dict[str, Any] = Field(..., description="Database health")

    # Capabilities
    supported_operations: list[str] = Field(..., description="Supported Gmail operations")
    api_version: str = Field(default="v1", description="Gmail API version")

    # Issues and recommendations
    issues_found: list[str] = Field(default_factory=list, description="Issues detected")
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="Health recommendations"
    )


class GmailMetricsResponse(BaseModel):
    """Response for Gmail system metrics."""

    timestamp: datetime = Field(..., description="Metrics timestamp")

    # User metrics
    total_users: int = Field(..., description="Total active users")
    gmail_connected_users: int = Field(..., description="Users with Gmail connected")
    gmail_connection_rate: float = Field(..., description="Gmail connection rate percentage")

    # Usage metrics
    emails_sent_24h: int | None = Field(None, description="Emails sent in last 24 hours")
    messages_read_24h: int | None = Field(None, description="Messages read in last 24 hours")

    # Health metrics
    healthy_connections: int = Field(..., description="Number of healthy Gmail connections")
    connections_needing_attention: int = Field(default=0, description="Connections with issues")

    # API metrics
    api_success_rate: float | None = Field(None, description="Gmail API success rate")
    average_api_latency_ms: float | None = Field(None, description="Average API response time")


# Voice-optimized response models for AI/Voice assistant
class VoiceInboxSummaryResponse(BaseModel):
    """Voice-optimized inbox summary response."""

    unread_count: int = Field(..., description="Number of unread messages")
    total_recent: int = Field(..., description="Total recent messages checked")
    high_priority_count: int = Field(..., description="High priority unread messages")
    actionable_count: int = Field(..., description="Messages requiring action")
    unread_messages: list[dict[str, Any]] = Field(..., description="Top unread messages for voice")
    voice_summary: str = Field(..., description="Natural language summary for voice response")


class VoiceTodayEmailsResponse(BaseModel):
    """Voice-optimized today's emails response."""

    total_today: int = Field(..., description="Total emails received today")
    unread_today: int = Field(..., description="Unread emails from today")
    important_today: int = Field(..., description="Important emails from today")
    messages: list[dict[str, Any]] = Field(..., description="Today's messages for voice")
    voice_summary: str = Field(..., description="Natural language summary for voice response")


class VoiceMessageActionResponse(BaseModel):
    """Response for voice-triggered message actions."""

    success: bool = Field(..., description="Whether action succeeded")
    action_taken: str = Field(..., description="Action that was performed")
    message_info: dict[str, str] = Field(..., description="Info about affected message")
    voice_confirmation: str = Field(..., description="Voice-friendly confirmation message")


# Enhanced models for integration with other services
class CombinedEmailCalendarResponse(BaseModel):
    """Combined response for email + calendar integration."""

    gmail_status: dict[str, Any] = Field(..., description="Gmail connection status")
    calendar_status: dict[str, Any] = Field(..., description="Calendar connection status")

    # Cross-service insights
    meeting_emails_today: int = Field(default=0, description="Emails about today's meetings")
    calendar_conflicts: list[dict[str, Any]] = Field(
        default_factory=list, description="Email-calendar conflicts detected"
    )
    actionable_items: list[dict[str, Any]] = Field(
        default_factory=list, description="Items requiring action across both services"
    )


class SmartTriageResponse(BaseModel):
    """Smart email triage response for AI assistant."""

    priority_emails: list[GmailMessageResponse] = Field(..., description="High priority emails")
    routine_emails: list[GmailMessageResponse] = Field(..., description="Routine emails")
    newsletters_promotions: list[GmailMessageResponse] = Field(
        ..., description="Newsletters/promos"
    )

    # AI insights
    action_recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="AI-generated action recommendations"
    )
    time_to_clear_inbox: str = Field(..., description="Estimated time to clear important items")
    focus_areas: list[str] = Field(..., description="Areas requiring immediate attention")
