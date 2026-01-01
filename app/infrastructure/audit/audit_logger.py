"""
AuditLogger - Centralized audit logging for PII access tracking.

This module provides the core audit logging functionality required for:
- Gmail API security compliance
- GDPR compliance (tracking data access)
- Security investigations
- Breach detection

Usage:
    from app.infrastructure.audit import audit_logger

    await audit_logger.log(
        user_id="user-123",
        action="vip_candidates_viewed",
        resource_type="vip_contacts",
        resource_count=50,
        pii_fields=["display_name"],
        ip_address="192.168.1.1",
        user_agent="MyiOSApp/1.0",
        request_id="req-abc123",
    )

Design Principles:
- Write to both database (immutable) and structured logs (searchable)
- Never fail the request if audit logging fails
- Capture comprehensive context for investigations
- Performance-optimized (async, connection pooling)
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.db.pool import db_pool
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class AuditLogger:
    """
    Centralized audit logging service.

    Logs all access to PII and sensitive operations to:
    1. Database (audit_logs table) - Immutable, queryable
    2. Structured logs (stdout) - Real-time monitoring

    Thread-safe and async-ready.
    """

    @staticmethod
    async def log(
        user_id: str | UUID,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_count: int | None = None,
        pii_fields: list[str] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Log an audit event to database and structured logs.

        Args:
            user_id: User who performed the action (required)
            action: Action name (e.g., "vip_candidates_viewed", "gmail_message_sent")
            resource_type: Type of resource (e.g., "vip_contacts", "gmail_messages")
            resource_id: Specific resource ID (e.g., message ID)
            resource_count: Number of resources accessed
            pii_fields: List of PII field names accessed (e.g., ["display_name", "email"])
            ip_address: Client IP address
            user_agent: Client user agent string
            request_id: Request correlation ID for tracing
            metadata: Additional context (JSON-serializable dict)

        Returns:
            True if logged successfully, False if failed (never raises)

        Examples:
            # Log VIP candidates viewed
            await audit_logger.log(
                user_id=user_id,
                action="vip_candidates_viewed",
                resource_type="vip_contacts",
                resource_count=50,
                pii_fields=["display_name", "contact_hash"],
                ip_address=request.state.ip_address,
                user_agent=request.state.user_agent,
                request_id=request.state.request_id,
            )

            # Log Gmail message sent
            await audit_logger.log(
                user_id=user_id,
                action="gmail_message_sent",
                resource_type="gmail_message",
                resource_id=message_id,
                pii_fields=["to", "subject", "body"],
                metadata={"to": email.to, "subject": email.subject},
                ip_address=request.state.ip_address,
                user_agent=request.state.user_agent,
                request_id=request.state.request_id,
            )
        """

        # Convert UUID to string for database storage
        if isinstance(user_id, UUID):
            user_id = str(user_id)

        # Log to structured logs FIRST (fast, synchronous)
        logger.info(
            "Audit event",
            audit_action=action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_count=resource_count,
            pii_fields=pii_fields,
            ip_address=ip_address,
            request_id=request_id,
        )

        # Insert to database for immutable audit trail
        try:
            async with db_pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs (
                        user_id, action, resource_type, resource_id,
                        resource_count, pii_fields, ip_address, user_agent,
                        request_id, metadata, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    resource_count,
                    pii_fields,
                    ip_address,
                    user_agent,
                    request_id,
                    metadata,
                    datetime.now(timezone.utc),
                )

            return True

        except Exception as e:
            # CRITICAL: NEVER fail the request due to audit logging failure
            # But log the error prominently for investigation
            logger.error(
                "CRITICAL: Failed to write audit log to database",
                error=str(e),
                error_type=type(e).__name__,
                action=action,
                user_id=user_id,
                resource_type=resource_type,
                # Include enough context to manually recreate the audit log if needed
                fallback_data={
                    "user_id": user_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "resource_count": resource_count,
                    "pii_fields": pii_fields,
                    "ip_address": ip_address,
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            return False

    @staticmethod
    async def log_pii_access(
        user_id: str | UUID,
        action: str,
        pii_fields: list[str],
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        **kwargs,
    ) -> bool:
        """
        Convenience method specifically for PII access logging.

        Use this when you want to emphasize PII access in the logs.

        Args:
            user_id: User who accessed PII
            action: Action that accessed PII
            pii_fields: List of PII fields accessed (required)
            ip_address: Client IP
            user_agent: Client user agent
            request_id: Request ID
            **kwargs: Additional args passed to log()

        Returns:
            True if logged successfully
        """
        return await AuditLogger.log(
            user_id=user_id,
            action=action,
            pii_fields=pii_fields,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            **kwargs,
        )

    @staticmethod
    async def log_data_deletion(
        user_id: str | UUID,
        resource_type: str,
        deleted_counts: dict[str, int],
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        """
        Log data deletion events (GDPR "right to erasure").

        Args:
            user_id: User whose data was deleted
            resource_type: Type of data deleted (e.g., "vip_data", "all_user_data")
            deleted_counts: Dict of table names and row counts deleted
            ip_address: Client IP
            user_agent: Client user agent
            request_id: Request ID

        Returns:
            True if logged successfully
        """
        return await AuditLogger.log(
            user_id=user_id,
            action=f"{resource_type}_deleted",
            resource_type=resource_type,
            metadata={"deleted_counts": deleted_counts},
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )

    @staticmethod
    async def log_security_event(
        user_id: str | UUID | None,
        event_type: str,
        severity: str,
        description: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Log security events (rate limit exceeded, auth failures, etc.).

        Args:
            user_id: User involved (None if unauthenticated)
            event_type: Type of security event (e.g., "rate_limit_exceeded")
            severity: Severity level ("low", "medium", "high", "critical")
            description: Human-readable description
            ip_address: Client IP
            user_agent: Client user agent
            request_id: Request ID
            metadata: Additional context

        Returns:
            True if logged successfully
        """
        # For unauthenticated events, use a placeholder user_id
        if user_id is None:
            user_id = "00000000-0000-0000-0000-000000000000"

        audit_metadata = metadata or {}
        audit_metadata.update(
            {
                "event_type": event_type,
                "severity": severity,
                "description": description,
            }
        )

        return await AuditLogger.log(
            user_id=user_id,
            action="security_event",
            resource_type="security",
            metadata=audit_metadata,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )


# Global singleton instance
audit_logger = AuditLogger()
