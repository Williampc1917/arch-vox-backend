# app/models/domain/gmail_domain.py
"""
Gmail Domain Models
Domain models for Gmail operations and business logic.
Used by services for internal processing and business rules.
PATTERN: Follows the exact same structure as calendar_domain.py
/models/domain/gmail_domain.py
"""

import base64
from datetime import UTC, datetime
from typing import Any


class GmailMessage:
    """Domain model for Gmail messages with business logic."""

    def __init__(self, data: dict):
        self.id = data.get("id")
        self.thread_id = data.get("threadId")
        self.label_ids = data.get("labelIds", [])
        self.snippet = data.get("snippet", "")
        self.size_estimate = data.get("sizeEstimate", 0)
        self.history_id = data.get("historyId")
        self.internal_date = data.get("internalDate")
        self.payload = data.get("payload", {})
        self.raw_data = data

        # Parse email headers and content
        self._parse_headers()
        self._parse_body()

    def _parse_headers(self):
        """Parse email headers from payload."""
        headers = self.payload.get("headers", [])
        self.headers = {h["name"].lower(): h["value"] for h in headers}

        # Extract common headers
        self.subject = self.headers.get("subject", "(No Subject)")
        self.sender = self._parse_email_address(self.headers.get("from", ""))
        self.recipients = self._parse_email_addresses(self.headers.get("to", ""))
        self.recipient = self.recipients[0] if self.recipients else {"name": "", "email": ""}
        self.cc = self._parse_email_addresses(self.headers.get("cc", ""))
        self.bcc = self._parse_email_addresses(self.headers.get("bcc", ""))
        self.reply_to = self._parse_email_address(self.headers.get("reply-to", ""))
        self.date = self.headers.get("date", "")
        self.message_id = self.headers.get("message-id", "")

    def _parse_email_address(self, address_str: str) -> dict[str, str]:
        """Parse email address string into name and email components."""
        if not address_str:
            return {"name": "", "email": ""}

        try:
            # Handle formats like "John Doe <john@example.com>" or "john@example.com"
            if "<" in address_str and ">" in address_str:
                name_part = address_str.split("<")[0].strip().strip('"')
                email_part = address_str.split("<")[1].split(">")[0].strip()
                return {"name": name_part, "email": email_part}
            else:
                return {"name": "", "email": address_str.strip()}
        except Exception:
            return {"name": "", "email": address_str}

    def _parse_email_addresses(self, addresses_str: str) -> list[dict[str, str]]:
        """Parse comma-separated email addresses."""
        if not addresses_str:
            return []

        addresses = []
        for addr in addresses_str.split(","):
            parsed = self._parse_email_address(addr.strip())
            if parsed["email"]:
                addresses.append(parsed)
        return addresses

    def _parse_body(self):
        """Parse email body content from payload."""
        self.body_text = ""
        self.body_html = ""
        self.attachments = []

        if not self.payload:
            return

        # Handle different payload structures
        if self.payload.get("body", {}).get("data"):
            # Simple message with direct body
            self._extract_body_data(self.payload["body"])
        elif self.payload.get("parts"):
            # Multipart message
            self._parse_multipart_body(self.payload["parts"])

    def _parse_multipart_body(self, parts: list):
        """Parse multipart email body."""
        for part in parts:
            mime_type = part.get("mimeType", "")

            if mime_type == "text/plain":
                body_data = part.get("body", {}).get("data")
                if body_data:
                    self.body_text = self._decode_base64_data(body_data)

            elif mime_type == "text/html":
                body_data = part.get("body", {}).get("data")
                if body_data:
                    self.body_html = self._decode_base64_data(body_data)

            elif mime_type.startswith("multipart/"):
                # Nested multipart
                nested_parts = part.get("parts", [])
                self._parse_multipart_body(nested_parts)

            elif part.get("filename"):
                # Attachment
                attachment = {
                    "filename": part.get("filename"),
                    "mime_type": mime_type,
                    "size": part.get("body", {}).get("size", 0),
                    "attachment_id": part.get("body", {}).get("attachmentId"),
                }
                self.attachments.append(attachment)

    def _extract_body_data(self, body: dict):
        """Extract body data from body object."""
        data = body.get("data")
        if data:
            # Assume text/plain by default
            self.body_text = self._decode_base64_data(data)

    def _decode_base64_data(self, data: str) -> str:
        """Decode base64 URL-safe encoded data."""
        try:
            # Gmail uses URL-safe base64 encoding
            decoded_bytes = base64.urlsafe_b64decode(data + "=" * (4 - len(data) % 4))
            return decoded_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def is_unread(self) -> bool:
        """Check if message is unread."""
        return "UNREAD" in self.label_ids

    def is_starred(self) -> bool:
        """Check if message is starred."""
        return "STARRED" in self.label_ids

    def is_important(self) -> bool:
        """Check if message is marked as important."""
        return "IMPORTANT" in self.label_ids

    def is_sent(self) -> bool:
        """Check if message is in sent folder."""
        return "SENT" in self.label_ids

    def is_draft(self) -> bool:
        """Check if message is a draft."""
        return "DRAFT" in self.label_ids

    def is_spam(self) -> bool:
        """Check if message is spam."""
        return "SPAM" in self.label_ids

    def is_trash(self) -> bool:
        """Check if message is in trash."""
        return "TRASH" in self.label_ids

    def has_attachments(self) -> bool:
        """Check if message has attachments."""
        return len(self.attachments) > 0

    def get_sender_display(self) -> str:
        """Get formatted sender display name."""
        if self.sender["name"]:
            return f"{self.sender['name']} <{self.sender['email']}>"
        return self.sender["email"]

    def get_body_preview(self, max_length: int = 150) -> str:
        """Get truncated body text for preview."""
        body = self.body_text or self.snippet
        if len(body) <= max_length:
            return body
        return body[:max_length].rsplit(" ", 1)[0] + "..."

    def get_received_datetime(self) -> datetime | None:
        """Get received datetime from internal date."""
        if self.internal_date:
            try:
                # Internal date is in milliseconds
                timestamp = int(self.internal_date) / 1000
                return datetime.fromtimestamp(timestamp, tz=UTC)
            except (ValueError, OSError):
                pass
        return None

    def get_age_description(self) -> str:
        """Get human-readable age description for voice responses."""
        received_time = self.get_received_datetime()
        if not received_time:
            return "unknown time"

        now = datetime.now(UTC)
        time_diff = now - received_time

        if time_diff.days > 0:
            if time_diff.days == 1:
                return "yesterday"
            elif time_diff.days < 7:
                return f"{time_diff.days} days ago"
            elif time_diff.days < 30:
                weeks = time_diff.days // 7
                return f"{weeks} week{'s' if weeks > 1 else ''} ago"
            else:
                months = time_diff.days // 30
                return f"{months} month{'s' if months > 1 else ''} ago"

        hours = time_diff.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"

        minutes = time_diff.seconds // 60
        if minutes > 0:
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"

        return "just now"

    def get_priority_level(self) -> str:
        """Determine message priority for AI triage."""
        # High priority indicators
        if self.is_important():
            return "high"

        # Check for urgent keywords in subject
        urgent_keywords = ["urgent", "asap", "emergency", "critical", "immediate"]
        subject_lower = self.subject.lower()
        if any(keyword in subject_lower for keyword in urgent_keywords):
            return "high"

        # Medium priority - unread and recent
        if self.is_unread():
            received = self.get_received_datetime()
            if received:
                hours_old = (datetime.now(UTC) - received).total_seconds() / 3600
                if hours_old < 24:  # Less than 24 hours old
                    return "medium"

        return "normal"

    def is_actionable(self) -> bool:
        """Check if message likely requires action."""
        action_keywords = [
            "please",
            "request",
            "need",
            "required",
            "deadline",
            "due",
            "action",
            "respond",
            "reply",
            "confirm",
            "approve",
            "review",
        ]

        text_to_check = f"{self.subject} {self.get_body_preview(300)}".lower()
        return any(keyword in text_to_check for keyword in action_keywords)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "sender": self.sender,
            "recipient": self.recipient,
            "cc": self.cc,
            "bcc": self.bcc,
            "snippet": self.snippet,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "attachments": self.attachments,
            "labels": self.label_ids,
            "is_unread": self.is_unread(),
            "is_starred": self.is_starred(),
            "is_important": self.is_important(),
            "has_attachments": self.has_attachments(),
            "received_datetime": self.get_received_datetime(),
            "sender_display": self.get_sender_display(),
            "body_preview": self.get_body_preview(),
            "size_estimate": self.size_estimate,
            "date": self.date,
            "age_description": self.get_age_description(),
            "priority_level": self.get_priority_level(),
            "is_actionable": self.is_actionable(),
        }


class GmailThread:
    """Domain model for Gmail conversation threads."""

    def __init__(self, data: dict):
        self.id = data.get("id")
        self.snippet = data.get("snippet", "")
        self.history_id = data.get("historyId")
        self.messages = []

        # Parse messages in thread
        for msg_data in data.get("messages", []):
            message = GmailMessage(msg_data)
            self.messages.append(message)

    def get_latest_message(self) -> GmailMessage | None:
        """Get the most recent message in the thread."""
        if not self.messages:
            return None

        # Sort by received time, most recent first
        sorted_messages = sorted(
            self.messages,
            key=lambda m: m.get_received_datetime() or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return sorted_messages[0]

    def get_participants(self) -> list[dict[str, str]]:
        """Get all unique participants in the thread."""
        participants = set()
        for message in self.messages:
            participants.add((message.sender["email"], message.sender["name"]))
            if message.recipient["email"]:
                participants.add((message.recipient["email"], message.recipient["name"]))
            for cc in message.cc:
                participants.add((cc["email"], cc["name"]))

        return [{"email": email, "name": name} for email, name in participants]

    def has_unread_messages(self) -> bool:
        """Check if thread has any unread messages."""
        return any(msg.is_unread() for msg in self.messages)

    def get_message_count(self) -> int:
        """Get total number of messages in thread."""
        return len(self.messages)

    def get_subject(self) -> str:
        """Get thread subject from latest message."""
        latest = self.get_latest_message()
        return latest.subject if latest else "(No Subject)"

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        latest_message = self.get_latest_message()
        return {
            "id": self.id,
            "snippet": self.snippet,
            "subject": self.get_subject(),
            "message_count": self.get_message_count(),
            "has_unread": self.has_unread_messages(),
            "participants": self.get_participants(),
            "latest_message": latest_message.to_dict() if latest_message else None,
            "messages": [msg.to_dict() for msg in self.messages],
        }


class GmailLabel:
    """Domain model for Gmail labels with business logic."""

    def __init__(self, data: dict):
        self.id = data.get("id")
        self.name = data.get("name", "")
        self.type = data.get("type", "user")  # "system" or "user"
        self.messages_total = data.get("messagesTotal", 0)
        self.messages_unread = data.get("messagesUnread", 0)
        self.threads_total = data.get("threadsTotal", 0)
        self.threads_unread = data.get("threadsUnread", 0)
        self.label_list_visibility = data.get("labelListVisibility", "labelShow")
        self.message_list_visibility = data.get("messageListVisibility", "show")
        self.raw_data = data

    def is_system_label(self) -> bool:
        """Check if this is a system label."""
        return self.type == "system"

    def is_user_label(self) -> bool:
        """Check if this is a user-created label."""
        return self.type == "user"

    def is_inbox(self) -> bool:
        """Check if this is the inbox label."""
        return self.id == "INBOX"

    def is_unread(self) -> bool:
        """Check if this is the unread label."""
        return self.id == "UNREAD"

    def is_sent(self) -> bool:
        """Check if this is the sent label."""
        return self.id == "SENT"

    def is_draft(self) -> bool:
        """Check if this is the draft label."""
        return self.id == "DRAFT"

    def is_trash(self) -> bool:
        """Check if this is the trash label."""
        return self.id == "TRASH"

    def is_spam(self) -> bool:
        """Check if this is the spam label."""
        return self.id == "SPAM"

    def is_starred(self) -> bool:
        """Check if this is the starred label."""
        return self.id == "STARRED"

    def is_important(self) -> bool:
        """Check if this is the important label."""
        return self.id == "IMPORTANT"

    def get_display_name(self) -> str:
        """Get user-friendly display name."""
        # Map system labels to user-friendly names
        system_label_names = {
            "INBOX": "Inbox",
            "UNREAD": "Unread",
            "SENT": "Sent",
            "DRAFT": "Drafts",
            "TRASH": "Trash",
            "SPAM": "Spam",
            "STARRED": "Starred",
            "IMPORTANT": "Important",
        }

        return system_label_names.get(self.id, self.name)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.get_display_name(),
            "type": self.type,
            "is_system": self.is_system_label(),
            "messages_total": self.messages_total,
            "messages_unread": self.messages_unread,
            "threads_total": self.threads_total,
            "threads_unread": self.threads_unread,
            "label_list_visibility": self.label_list_visibility,
            "message_list_visibility": self.message_list_visibility,
        }


class GmailConnectionStatus:
    """Domain model representing Gmail connection status for a user."""

    def __init__(
        self,
        connected: bool,
        user_id: str,
        provider: str = "google",
        scope: str | None = None,
        expires_at: datetime | None = None,
        needs_refresh: bool = False,
        last_used: datetime | None = None,
        connection_health: str = "unknown",
        messages_accessible: int = 0,
        can_send_email: bool = False,
        can_read_email: bool = False,
        health_details: dict[str, Any] | None = None,
    ):
        self.connected = connected
        self.user_id = user_id
        self.provider = provider
        self.scope = scope
        self.expires_at = expires_at
        self.needs_refresh = needs_refresh
        self.last_used = last_used
        self.connection_health = connection_health
        self.messages_accessible = messages_accessible
        self.can_send_email = can_send_email
        self.can_read_email = can_read_email
        self.health_details = health_details or {}

    def is_healthy(self) -> bool:
        """Check if connection is in healthy state."""
        healthy_states = ["healthy", "refresh_scheduled", "expiring_soon"]
        return self.connection_health in healthy_states

    def needs_attention(self) -> bool:
        """Check if connection needs user attention."""
        attention_states = ["expired", "failing", "invalid", "no_tokens", "error"]
        return self.connection_health in attention_states

    def is_functional(self) -> bool:
        """Check if connection can perform basic operations."""
        return self.connected and (self.can_read_email or self.can_send_email)

    def get_capabilities(self) -> dict[str, Any]:
        """Get summary of Gmail capabilities."""
        return {
            "can_read_email": self.can_read_email,
            "can_send_email": self.can_send_email,
            "can_manage_labels": self.connected and self.can_read_email,
            "messages_accessible": self.messages_accessible,
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Get comprehensive health summary."""
        return {
            "status": self.connection_health,
            "is_healthy": self.is_healthy(),
            "needs_attention": self.needs_attention(),
            "is_functional": self.is_functional(),
            "details": self.health_details,
            "token_info": {
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "needs_refresh": self.needs_refresh,
                "last_used": self.last_used.isoformat() if self.last_used else None,
            },
            "capabilities": self.get_capabilities(),
        }

    def time_until_expiry(self) -> int | None:
        """Get minutes until token expiry (None if no expiry or already expired)."""
        if not self.expires_at:
            return None

        now = datetime.now(UTC)
        expires_at = self.expires_at

        # Ensure timezone compatibility
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        elif expires_at.tzinfo != UTC:
            expires_at = expires_at.astimezone(UTC)

        if expires_at <= now:
            return None  # Already expired

        delta = expires_at - now
        return int(delta.total_seconds() / 60)

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Get actionable recommendations based on connection status."""
        recommendations = []

        if not self.connected:
            recommendations.append(
                {
                    "priority": "high",
                    "action": "connect_gmail",
                    "message": "Connect your Gmail to enable email features",
                    "user_action": "Go to Settings > Connect Gmail",
                }
            )
        elif self.needs_attention():
            if self.connection_health == "expired":
                recommendations.append(
                    {
                        "priority": "high",
                        "action": "refresh_tokens",
                        "message": "Gmail access has expired and will be refreshed automatically",
                        "user_action": "No action needed - refresh in progress",
                    }
                )
            elif self.connection_health in ["failing", "invalid"]:
                recommendations.append(
                    {
                        "priority": "high",
                        "action": "reconnect_gmail",
                        "message": "Gmail connection needs to be refreshed",
                        "user_action": "Go to Settings > Reconnect Gmail",
                    }
                )
        elif not self.can_send_email:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "upgrade_permissions",
                    "message": "Gmail access is read-only. Reconnect to enable sending emails",
                    "user_action": "Go to Settings > Reconnect Gmail with full permissions",
                }
            )

        return recommendations

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "connected": self.connected,
            "provider": self.provider,
            "scope": self.scope,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_refresh": self.needs_refresh,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "connection_health": self.connection_health,
            "capabilities": self.get_capabilities(),
            "health_details": self.health_details,
            "health_summary": self.get_health_summary(),
            "time_until_expiry_minutes": self.time_until_expiry(),
            "recommendations": self.get_recommendations(),
        }


class GmailSearchResult:
    """Domain model for Gmail search results with metadata."""

    def __init__(
        self,
        messages: list[GmailMessage],
        query: str,
        total_found: int,
        has_more: bool = False,
        next_page_token: str | None = None,
    ):
        self.messages = messages
        self.query = query
        self.total_found = total_found
        self.has_more = has_more
        self.next_page_token = next_page_token

    def get_unread_count(self) -> int:
        """Get count of unread messages in results."""
        return sum(1 for msg in self.messages if msg.is_unread())

    def get_high_priority_messages(self) -> list[GmailMessage]:
        """Get high priority messages from results."""
        return [msg for msg in self.messages if msg.get_priority_level() == "high"]

    def get_actionable_messages(self) -> list[GmailMessage]:
        """Get messages that likely require action."""
        return [msg for msg in self.messages if msg.is_actionable()]

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "messages": [msg.to_dict() for msg in self.messages],
            "query": self.query,
            "total_found": self.total_found,
            "has_more": self.has_more,
            "next_page_token": self.next_page_token,
            "unread_count": self.get_unread_count(),
            "high_priority_count": len(self.get_high_priority_messages()),
            "actionable_count": len(self.get_actionable_messages()),
        }
