"""
Audit Helper Utilities - One-line audit logging for endpoints.

These helpers make audit logging super easy with minimal boilerplate.

Usage:
    from app.utils.audit_helpers import audit_pii_access

    # In your endpoint
    await audit_pii_access(
        request=request,
        user_id=user_id,
        action="vips_viewed",
        resource_count=len(vips),
    )

Design Philosophy:
- One line of code = full audit compliance
- Automatically extracts request context (IP, user-agent, request ID)
- Type-safe and auto-complete friendly
- Common patterns pre-built (PII access, data modification, etc.)
"""

from typing import Any

from fastapi import Request

from app.infrastructure.audit.audit_logger import audit_logger


async def audit_pii_access(
    request: Request,
    user_id: str,
    action: str,
    resource_type: str = "user_data",
    resource_id: str | None = None,
    resource_count: int | None = None,
    pii_fields: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    One-line helper for auditing PII access.

    Automatically extracts request context (IP, user-agent, request ID)
    from the request object.

    Args:
        request: FastAPI Request object
        user_id: User who accessed the PII
        action: Action performed (e.g., "vips_viewed", "messages_listed")
        resource_type: Type of resource (default: "user_data")
        resource_id: Specific resource ID
        resource_count: Number of resources accessed
        pii_fields: List of PII field names (e.g., ["display_name", "email"])
        metadata: Additional context

    Returns:
        True if logged successfully

    Examples:
        # Log VIP candidates viewed
        await audit_pii_access(
            request=request,
            user_id=user_id,
            action="vips_viewed",
            resource_type="vip_contacts",
            resource_count=50,
            pii_fields=["display_name"],
        )

        # Log Gmail messages listed
        await audit_pii_access(
            request=request,
            user_id=user_id,
            action="gmail_messages_listed",
            resource_type="gmail_messages",
            resource_count=len(messages),
            pii_fields=["subject", "sender"],
        )

        # Log specific message accessed
        await audit_pii_access(
            request=request,
            user_id=user_id,
            action="gmail_message_viewed",
            resource_type="gmail_message",
            resource_id=message_id,
            pii_fields=["subject", "body", "sender", "recipients"],
        )
    """
    return await audit_logger.log(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_count=resource_count,
        pii_fields=pii_fields,
        ip_address=request.state.ip_address,
        user_agent=request.state.user_agent,
        request_id=request.state.request_id,
        metadata=metadata,
    )


async def audit_data_modification(
    request: Request,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    changes: dict[str, Any] | None = None,
) -> bool:
    """
    One-line helper for auditing data modifications (create, update, delete).

    Args:
        request: FastAPI Request object
        user_id: User who modified the data
        action: Action performed (e.g., "vip_selection_saved", "user_updated")
        resource_type: Type of resource modified
        resource_id: Specific resource ID
        changes: Dict of changes made (before/after values)

    Returns:
        True if logged successfully

    Examples:
        # Log VIP selection saved
        await audit_data_modification(
            request=request,
            user_id=user_id,
            action="vip_selection_saved",
            resource_type="vip_selections",
            changes={"vip_count": len(selected_vips)},
        )

        # Log user profile updated
        await audit_data_modification(
            request=request,
            user_id=user_id,
            action="profile_updated",
            resource_type="user_profile",
            resource_id=user_id,
            changes={"display_name": {"old": old_name, "new": new_name}},
        )
    """
    return await audit_logger.log(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.state.ip_address,
        user_agent=request.state.user_agent,
        request_id=request.state.request_id,
        metadata={"changes": changes} if changes else None,
    )


async def audit_gmail_action(
    request: Request,
    user_id: str,
    action: str,
    message_id: str | None = None,
    message_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    One-line helper specifically for Gmail API actions.

    Args:
        request: FastAPI Request object
        user_id: User who performed Gmail action
        action: Action performed (e.g., "email_sent", "messages_listed")
        message_id: Specific message ID (for single message actions)
        message_count: Number of messages (for bulk actions)
        metadata: Additional context (e.g., {"to": "user@example.com"})

    Returns:
        True if logged successfully

    Examples:
        # Log email sent
        await audit_gmail_action(
            request=request,
            user_id=user_id,
            action="email_sent",
            message_id=message_id,
            metadata={"to": email.to, "subject": email.subject},
        )

        # Log messages listed
        await audit_gmail_action(
            request=request,
            user_id=user_id,
            action="messages_listed",
            message_count=len(messages),
        )
    """
    return await audit_logger.log(
        user_id=user_id,
        action=f"gmail_{action}",
        resource_type="gmail_message" if message_id else "gmail_messages",
        resource_id=message_id,
        resource_count=message_count,
        pii_fields=["subject", "sender", "recipients"],
        ip_address=request.state.ip_address,
        user_agent=request.state.user_agent,
        request_id=request.state.request_id,
        metadata=metadata,
    )


async def audit_security_event(
    request: Request,
    event_type: str,
    severity: str,
    description: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    One-line helper for security events (rate limiting, auth failures, etc.).

    Args:
        request: FastAPI Request object
        event_type: Type of event (e.g., "rate_limit_exceeded", "auth_failed")
        severity: Severity ("low", "medium", "high", "critical")
        description: Human-readable description
        user_id: User involved (None for unauthenticated events)
        metadata: Additional context

    Returns:
        True if logged successfully

    Examples:
        # Log rate limit exceeded
        await audit_security_event(
            request=request,
            event_type="rate_limit_exceeded",
            severity="medium",
            description="User exceeded rate limit on VIP endpoint",
            user_id=user_id,
            metadata={"limit": 60, "endpoint": "/onboarding/vips/"},
        )

        # Log authentication failure
        await audit_security_event(
            request=request,
            event_type="auth_failed",
            severity="high",
            description="Invalid JWT token",
            metadata={"reason": "Token expired"},
        )
    """
    return await audit_logger.log_security_event(
        user_id=user_id,
        event_type=event_type,
        severity=severity,
        description=description,
        ip_address=request.state.ip_address,
        user_agent=request.state.user_agent,
        request_id=request.state.request_id,
        metadata=metadata,
    )
