"""
Gmail API request models.
Used by routes for input validation.
Mirrors the pattern established in calendar_request.py
"""

from pydantic import BaseModel, Field


class GetMessagesRequest(BaseModel):
    """Request for getting Gmail messages with filters."""

    max_results: int = Field(default=10, ge=1, le=100, description="Maximum messages to return (1-100)")
    label_ids: list[str] | None = Field(
        default=None, description="Label IDs to filter by (e.g., ['INBOX', 'UNREAD'])"
    )
    query: str | None = Field(
        default=None, description="Gmail search query (e.g., 'is:unread subject:meeting')"
    )
    include_spam_trash: bool = Field(default=False, description="Include spam and trash messages")
    only_unread: bool = Field(default=False, description="Only return unread messages")


class GetMessageRequest(BaseModel):
    """Request for getting a specific message by ID."""

    message_id: str = Field(..., description="Gmail message ID")
    format: str = Field(default="full", description="Message format (full, metadata, minimal, raw)")


class SendEmailRequest(BaseModel):
    """Request for sending an email."""

    to: list[str] = Field(..., min_items=1, description="Recipient email addresses")
    subject: str = Field(..., min_length=1, max_length=200, description="Email subject")
    body: str = Field(..., min_length=1, description="Email body (plain text)")
    cc: list[str] | None = Field(default=None, description="CC recipients")
    bcc: list[str] | None = Field(default=None, description="BCC recipients")
    reply_to: str | None = Field(default=None, description="Reply-to address")
    reply_to_message_id: str | None = Field(default=None, description="Message ID to reply to")


class SearchMessagesRequest(BaseModel):
    """Request for searching messages with Gmail query syntax."""

    query: str = Field(..., min_length=1, description="Gmail search query")
    max_results: int = Field(default=10, ge=1, le=100, description="Maximum results to return")


class ModifyMessageRequest(BaseModel):
    """Request for modifying message labels."""

    message_id: str = Field(..., description="Gmail message ID")
    add_labels: list[str] | None = Field(default=None, description="Labels to add")
    remove_labels: list[str] | None = Field(default=None, description="Labels to remove")


class MarkAsReadRequest(BaseModel):
    """Request for marking message as read."""

    message_id: str = Field(..., description="Gmail message ID")


class MarkAsUnreadRequest(BaseModel):
    """Request for marking message as unread."""

    message_id: str = Field(..., description="Gmail message ID")


class StarMessageRequest(BaseModel):
    """Request for starring a message."""

    message_id: str = Field(..., description="Gmail message ID")


class UnstarMessageRequest(BaseModel):
    """Request for unstarring a message."""

    message_id: str = Field(..., description="Gmail message ID")


class DeleteMessageRequest(BaseModel):
    """Request for deleting a message."""

    message_id: str = Field(..., description="Gmail message ID")


class CreateDraftRequest(BaseModel):
    """Request for creating a draft message."""

    to: list[str] = Field(..., min_items=1, description="Recipient email addresses")
    subject: str = Field(..., min_length=1, max_length=200, description="Email subject")
    body: str = Field(..., min_length=1, description="Email body (plain text)")
    cc: list[str] | None = Field(default=None, description="CC recipients")
    bcc: list[str] | None = Field(default=None, description="BCC recipients")


class GetThreadRequest(BaseModel):
    """Request for getting a conversation thread."""

    thread_id: str = Field(..., description="Gmail thread ID")
    format: str = Field(default="full", description="Message format for thread messages")


# Voice-optimized request models for AI/Voice assistant use
class VoiceInboxSummaryRequest(BaseModel):
    """Request for voice-optimized inbox summary."""

    max_unread: int = Field(default=5, ge=1, le=10, description="Max unread messages to include")
    include_preview: bool = Field(default=True, description="Include message previews for voice")


class VoiceTodayEmailsRequest(BaseModel):
    """Request for today's emails optimized for voice."""

    max_results: int = Field(default=10, ge=1, le=20, description="Max today's emails to return")
    only_important: bool = Field(default=False, description="Only important emails from today")