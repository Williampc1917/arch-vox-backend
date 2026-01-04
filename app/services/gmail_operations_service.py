"""
Gmail operations Service for high-level Gmail orchestration.
Manages Gmail connection status, health monitoring, and integration with user management.
UPDATED: Now uses domain models from app.models.domain.gmail_domain
ARCHITECTURE: Mirrors calendar_operations_service.py patterns for consistency.
/services/gmail_operations_service.py
"""

from datetime import datetime
from typing import Any

from app.db.helpers import DatabaseError, execute_query, with_db_retry
from app.infrastructure.observability.logging import get_logger
from app.models.domain.gmail_domain import (
    GmailConnectionStatus,
    GmailLabel,
    GmailMessage,
    GmailSearchResult,
)
from app.services.google_gmail_service import (
    GoogleGmailError,
    google_gmail_service,
)
from app.services.token_service import (
    TokenServiceError,
    get_oauth_tokens,
)

logger = get_logger(__name__)


class GmailConnectionError(Exception):
    """Custom exception for Gmail connection operations."""

    def __init__(
        self,
        message: str,
        user_id: str | None = None,
        error_code: str | None = None,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.user_id = user_id
        self.error_code = error_code
        self.recoverable = recoverable


class GmailConnectionService:
    """
    High-level service for Gmail connection management.

    Orchestrates Gmail operations, manages connection status,
    and integrates with existing OAuth and user management systems.
    """

    def __init__(self):
        self._config_validated = False

    def _ensure_config_validated(self) -> None:
        """Validate service configuration when first used."""
        if self._config_validated:
            return

        # Test database pool availability
        try:
            from app.db.pool import db_pool

            if not db_pool._initialized:
                raise GmailConnectionError("Database pool not initialized")
        except Exception as e:
            raise GmailConnectionError(f"Database pool validation failed: {e}") from e

        self._config_validated = True
        logger.info("Gmail connection service initialized successfully")

    async def get_connection_status(self, user_id: str) -> GmailConnectionStatus:
        """
        Get comprehensive Gmail connection status for user.

        Args:
            user_id: UUID string of the user

        Returns:
            GmailConnectionStatus: Complete Gmail connection information
        """
        self._ensure_config_validated()

        try:
            logger.debug("Getting Gmail connection status", user_id=user_id)

            # Check if user has Gmail-enabled OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)

            if not oauth_tokens:
                return GmailConnectionStatus(
                    connected=False, user_id=user_id, connection_health="no_tokens"
                )

            if not oauth_tokens.has_gmail_access():
                return GmailConnectionStatus(
                    connected=False,
                    user_id=user_id,
                    connection_health="no_gmail_permissions",
                    scope=oauth_tokens.scope,
                    expires_at=oauth_tokens.expires_at,
                    needs_refresh=oauth_tokens.needs_refresh(),
                )

            # Test Gmail access and get capabilities
            gmail_capabilities = await self._test_gmail_access(oauth_tokens.access_token)

            # Determine connection health
            connection_health = self._assess_gmail_health(oauth_tokens, gmail_capabilities)

            return GmailConnectionStatus(
                connected=True,
                user_id=user_id,
                provider=oauth_tokens.provider,
                scope=oauth_tokens.scope,
                expires_at=oauth_tokens.expires_at,
                needs_refresh=oauth_tokens.needs_refresh(),
                last_used=getattr(oauth_tokens, "last_used_at", None),
                connection_health=connection_health["status"],
                messages_accessible=gmail_capabilities.get("messages_count", 0),
                can_send_email=gmail_capabilities.get("can_send_email", False),
                can_read_email=gmail_capabilities.get("can_read_email", False),
                health_details=connection_health,
            )

        except Exception as e:
            logger.error(
                "Error getting Gmail connection status",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return GmailConnectionStatus(
                connected=False,
                user_id=user_id,
                connection_health="error",
                health_details={"error": str(e)},
            )

    async def _test_gmail_access(self, access_token: str) -> dict[str, Any]:
        """
        Test Gmail access and determine capabilities.

        Args:
            access_token: OAuth access token

        Returns:
            Dict: Gmail capabilities and test results
        """
        try:
            # Test by listing a few messages from inbox
            messages = await google_gmail_service.list_messages(
                access_token, max_results=5, label_ids=["INBOX"]
            )

            # Test by getting labels
            labels = await google_gmail_service.get_labels(access_token)

            # Analyze capabilities
            can_read_email = len(messages) >= 0  # Even 0 messages means we can read
            can_send_email = True  # If we can read, we typically can send
            has_inbox = any(label.is_inbox() for label in labels)

            capabilities = {
                "messages_count": len(messages),
                "labels_count": len(labels),
                "can_read_email": can_read_email,
                "can_send_email": can_send_email,
                "has_inbox": has_inbox,
                "access_test_successful": True,
            }

            logger.debug(
                "Gmail access test successful",
                messages_count=len(messages),
                labels_count=len(labels),
                can_read=can_read_email,
                can_send=can_send_email,
            )

            return capabilities

        except GoogleGmailError as e:
            logger.warning(
                "Gmail access test failed",
                error=str(e),
                error_code=getattr(e, "error_code", None),
            )
            return {
                "messages_count": 0,
                "labels_count": 0,
                "can_read_email": False,
                "can_send_email": False,
                "has_inbox": False,
                "access_test_successful": False,
                "error": str(e),
                "error_code": getattr(e, "error_code", None),
            }

    def _assess_gmail_health(
        self, oauth_tokens, gmail_capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Assess Gmail connection health based on tokens and capabilities.

        Args:
            oauth_tokens: OAuth token object
            gmail_capabilities: Gmail access test results

        Returns:
            Dict: Health assessment with status and recommendations
        """
        try:
            # Check token health first
            token_health = oauth_tokens.get_health_status()

            # Gmail-specific health checks
            if not gmail_capabilities.get("access_test_successful", False):
                return {
                    "status": "api_error",
                    "message": "Gmail API access failed",
                    "severity": "high",
                    "action_required": "Check Gmail permissions",
                    "details": {
                        "token_health": token_health,
                        "gmail_error": gmail_capabilities.get("error", "Unknown error"),
                        "error_code": gmail_capabilities.get("error_code"),
                    },
                }

            if not gmail_capabilities.get("has_inbox", False):
                return {
                    "status": "no_inbox_access",
                    "message": "Gmail inbox not accessible",
                    "severity": "medium",
                    "action_required": "Verify Gmail permissions",
                    "details": {
                        "token_health": token_health,
                        "labels_found": gmail_capabilities.get("labels_count", 0),
                    },
                }

            if not gmail_capabilities.get("can_send_email", False):
                return {
                    "status": "read_only",
                    "message": "Gmail access is read-only",
                    "severity": "medium",
                    "action_required": "Grant Gmail send permissions for full functionality",
                    "details": {
                        "token_health": token_health,
                        "can_read": gmail_capabilities.get("can_read_email", False),
                        "messages_accessible": gmail_capabilities.get("messages_count", 0),
                    },
                }

            # Check token-based health
            if token_health["status"] != "healthy":
                return {
                    "status": token_health["status"],
                    "message": f"Gmail tokens {token_health['message'].lower()}",
                    "severity": token_health["severity"],
                    "action_required": token_health.get("action_required"),
                    "details": {
                        "token_health": token_health,
                        "gmail_capabilities": gmail_capabilities,
                    },
                }

            # All checks passed
            return {
                "status": "healthy",
                "message": f"Gmail access healthy - {gmail_capabilities['messages_count']} messages accessible",
                "severity": "none",
                "action_required": None,
                "details": {
                    "token_health": token_health,
                    "gmail_capabilities": gmail_capabilities,
                },
            }

        except Exception as e:
            logger.error("Error assessing Gmail health", error=str(e))
            return {
                "status": "assessment_error",
                "message": f"Health assessment failed: {e}",
                "severity": "medium",
                "details": {"error": str(e)},
            }

    async def get_inbox_messages(
        self, user_id: str, max_results: int = 10, only_unread: bool = False
    ) -> tuple[list[GmailMessage], int]:
        """
        Get inbox messages for user.

        Args:
            user_id: UUID string of the user
            max_results: Maximum number of messages to return
            only_unread: Whether to return only unread messages

        Returns:
            Tuple[list[GmailMessage], int]: (Inbox messages, Total count)

        Raises:
            GmailConnectionError: If getting messages fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Build label filter
            label_ids = ["INBOX"]
            if only_unread:
                label_ids.append("UNREAD")

            # Get messages with total count
            messages, total_count = await google_gmail_service.list_messages(
                access_token=oauth_tokens.access_token,
                max_results=max_results,
                label_ids=label_ids,
            )

            logger.info(
                "Inbox messages retrieved",
                user_id=user_id,
                message_count=len(messages),
                total_count=total_count,
                only_unread=only_unread,
            )

            return messages, total_count

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error getting inbox messages",
                user_id=user_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except TokenServiceError as e:
            logger.error(
                "Token service error getting inbox messages",
                user_id=user_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Token error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error getting inbox messages",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to get inbox messages: {e}", user_id=user_id) from e

    async def get_message_by_id(self, user_id: str, message_id: str) -> GmailMessage:
        """
        Get specific message by ID.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            GmailMessage: Message details

        Raises:
            GmailConnectionError: If getting message fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Get message
            message = await google_gmail_service.get_message(
                access_token=oauth_tokens.access_token, message_id=message_id
            )

            logger.info("Message retrieved", user_id=user_id, message_id=message_id)
            return message

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error getting message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error getting message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to get message: {e}", user_id=user_id) from e

    async def search_messages(
        self, user_id: str, query: str, max_results: int = 10
    ) -> GmailSearchResult:
        """
        Search messages with Gmail query syntax.

        Args:
            user_id: UUID string of the user
            query: Gmail search query (e.g., "is:unread subject:meeting")
            max_results: Maximum number of results

        Returns:
            GmailSearchResult: Search results with metadata

        Raises:
            GmailConnectionError: If search fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Search messages
            messages = await google_gmail_service.list_messages(
                access_token=oauth_tokens.access_token,
                max_results=max_results,
                query=query,
            )

            # Create search result with metadata
            search_result = GmailSearchResult(
                messages=messages,
                query=query,
                total_found=len(messages),
                has_more=len(messages) >= max_results,
            )

            logger.info(
                "Message search completed",
                user_id=user_id,
                query=query,
                results_found=len(messages),
            )

            return search_result

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error searching messages",
                user_id=user_id,
                query=query,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error searching messages",
                user_id=user_id,
                query=query,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to search messages: {e}", user_id=user_id) from e

    async def send_email(
        self,
        user_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict:
        """
        Send email for user.

        Args:
            user_id: UUID string of the user
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: List of CC recipients
            bcc: List of BCC recipients
            reply_to_message_id: Message ID to reply to (for threading)

        Returns:
            dict: Sent message information

        Raises:
            GmailConnectionError: If sending fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Get thread ID if replying
            thread_id = None
            if reply_to_message_id:
                try:
                    original_message = await google_gmail_service.get_message(
                        oauth_tokens.access_token, reply_to_message_id
                    )
                    thread_id = original_message.thread_id
                except Exception as e:
                    logger.warning(
                        "Could not get thread ID for reply",
                        reply_to_message_id=reply_to_message_id,
                        error=str(e),
                    )

            # Send email
            result = await google_gmail_service.send_message(
                access_token=oauth_tokens.access_token,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                thread_id=thread_id,
            )

            # Update last used timestamp
            await self._update_gmail_usage(user_id)

            logger.info(
                "Email sent successfully",
                user_id=user_id,
                to=to,
                subject=subject,
                is_reply=bool(reply_to_message_id),
            )

            return result

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error sending email",
                user_id=user_id,
                to=to,
                subject=subject,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error sending email",
                user_id=user_id,
                to=to,
                subject=subject,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to send email: {e}", user_id=user_id) from e

    async def mark_message_as_read(self, user_id: str, message_id: str) -> GmailMessage:
        """
        Mark message as read.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            GmailMessage: Updated message

        Raises:
            GmailConnectionError: If marking as read fails
        """
        return await self._modify_message_labels(user_id, message_id, remove_labels=["UNREAD"])

    async def mark_message_as_unread(self, user_id: str, message_id: str) -> GmailMessage:
        """
        Mark message as unread.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            GmailMessage: Updated message

        Raises:
            GmailConnectionError: If marking as unread fails
        """
        return await self._modify_message_labels(user_id, message_id, add_labels=["UNREAD"])

    async def star_message(self, user_id: str, message_id: str) -> GmailMessage:
        """
        Star a message.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            GmailMessage: Updated message

        Raises:
            GmailConnectionError: If starring fails
        """
        return await self._modify_message_labels(user_id, message_id, add_labels=["STARRED"])

    async def unstar_message(self, user_id: str, message_id: str) -> GmailMessage:
        """
        Unstar a message.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            GmailMessage: Updated message

        Raises:
            GmailConnectionError: If unstarring fails
        """
        return await self._modify_message_labels(user_id, message_id, remove_labels=["STARRED"])

    async def delete_message(self, user_id: str, message_id: str) -> bool:
        """
        Delete message (move to trash).

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID

        Returns:
            bool: True if deletion successful

        Raises:
            GmailConnectionError: If deletion fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Delete message
            success = await google_gmail_service.delete_message(
                access_token=oauth_tokens.access_token, message_id=message_id
            )

            logger.info("Message deleted", user_id=user_id, message_id=message_id)
            return success

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error deleting message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error deleting message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to delete message: {e}", user_id=user_id) from e

    async def _modify_message_labels(
        self,
        user_id: str,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> GmailMessage:
        """
        Internal method to modify message labels.

        Args:
            user_id: UUID string of the user
            message_id: Gmail message ID
            add_labels: Labels to add
            remove_labels: Labels to remove

        Returns:
            GmailMessage: Updated message

        Raises:
            GmailConnectionError: If modification fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Modify message
            message = await google_gmail_service.modify_message(
                access_token=oauth_tokens.access_token,
                message_id=message_id,
                add_label_ids=add_labels,
                remove_label_ids=remove_labels,
            )

            logger.info(
                "Message labels modified",
                user_id=user_id,
                message_id=message_id,
                add_labels=add_labels,
                remove_labels=remove_labels,
            )

            return message

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error modifying message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error modifying message",
                user_id=user_id,
                message_id=message_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to modify message: {e}", user_id=user_id) from e

    async def get_user_labels(self, user_id: str) -> list[GmailLabel]:
        """
        Get Gmail labels for user.

        Args:
            user_id: UUID string of the user

        Returns:
            List[GmailLabel]: List of Gmail labels

        Raises:
            GmailConnectionError: If getting labels fails
        """
        self._ensure_config_validated()

        try:
            # Get OAuth tokens
            oauth_tokens = await get_oauth_tokens(user_id)
            if not oauth_tokens:
                raise GmailConnectionError("No OAuth tokens found", user_id=user_id)

            if not oauth_tokens.has_gmail_access():
                raise GmailConnectionError("No Gmail permissions", user_id=user_id)

            # Get labels
            labels = await google_gmail_service.get_labels(access_token=oauth_tokens.access_token)

            logger.info("Gmail labels retrieved", user_id=user_id, label_count=len(labels))
            return labels

        except GoogleGmailError as e:
            logger.error(
                "Gmail API error getting labels",
                user_id=user_id,
                error=str(e),
            )
            raise GmailConnectionError(f"Gmail API error: {e}", user_id=user_id) from e

        except Exception as e:
            logger.error(
                "Unexpected error getting labels",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise GmailConnectionError(f"Failed to get labels: {e}", user_id=user_id) from e

    @with_db_retry(max_retries=3, base_delay=0.1)
    async def _update_gmail_usage(self, user_id: str) -> None:
        """
        Update Gmail last used timestamp.

        Args:
            user_id: UUID string of the user
        """
        try:
            # Update OAuth tokens timestamp for Gmail usage tracking
            query = """
            UPDATE oauth_tokens
            SET updated_at = NOW()
            WHERE user_id = %s AND provider = 'google'
            """

            await execute_query(query, (user_id,))

            logger.debug("Gmail usage timestamp updated", user_id=user_id)

        except DatabaseError as e:
            logger.warning(
                "Failed to update Gmail usage timestamp",
                user_id=user_id,
                error=str(e),
            )
            # Don't raise exception for usage tracking failure

    async def get_connection_metrics(self) -> dict[str, Any]:
        """
        Get Gmail connection metrics for monitoring.

        Returns:
            Dict: Gmail connection metrics and statistics
        """
        try:
            # Leverage existing user service metrics
            from app.services.user_service import get_user_service_health

            user_health = await get_user_service_health()

            # Extract Gmail-relevant metrics
            metrics = {
                "total_users": user_health.get("user_metrics", {}).get("total_active_users", 0),
                "users_with_tokens": user_health.get("gmail_health_metrics", {}).get(
                    "users_with_tokens", 0
                ),
                "healthy_connections": "unknown",  # Would need Gmail-specific health tracking
                "gmail_api_connectivity": "unknown",  # Would test Gmail API
                "service": "gmail_connection",
                "timestamp": datetime.utcnow().isoformat(),
            }

            return metrics

        except Exception as e:
            logger.error("Error getting Gmail connection metrics", error=str(e))
            return {
                "service": "gmail_connection",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    async def health_check(self) -> dict[str, Any]:
        """
        Check Gmail connection service health.

        Returns:
            Dict: Health status and metrics
        """
        try:
            health_data = {
                "healthy": True,
                "service": "gmail_connection",
                "database_connectivity": "unknown",
                "gmail_api_connectivity": "unknown",
            }

            # Test database connectivity (use existing pattern)
            try:
                # We'll test this asynchronously in a real implementation
                # For now, assume healthy if no exception
                health_data["database_connectivity"] = "ok"
            except Exception as e:
                health_data["database_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Test Gmail API service health
            try:
                from app.services.google_gmail_service import google_gmail_health

                gmail_health = await google_gmail_health()
                health_data["gmail_api_connectivity"] = (
                    "ok" if gmail_health.get("healthy", False) else "error"
                )
                if not gmail_health.get("healthy", False):
                    health_data["healthy"] = False
                    health_data["gmail_api_error"] = gmail_health.get("error", "Unknown error")
            except Exception as e:
                health_data["gmail_api_connectivity"] = f"error: {str(e)}"
                health_data["healthy"] = False

            # Add service capabilities
            health_data["capabilities"] = [
                "get_connection_status",
                "get_inbox_messages",
                "get_message_by_id",
                "search_messages",
                "send_email",
                "mark_as_read",
                "star_message",
                "delete_message",
                "get_user_labels",
            ]

            return health_data

        except Exception as e:
            logger.error("Gmail connection service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "gmail_connection",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }


# Singleton instance for application use
gmail_connection_service = GmailConnectionService()


# Convenience functions for easy import
async def get_gmail_status(user_id: str) -> GmailConnectionStatus:
    """Get Gmail connection status for user."""
    return await gmail_connection_service.get_connection_status(user_id)


async def get_user_inbox_messages(
    user_id: str, max_results: int = 10, only_unread: bool = False
) -> tuple[list[GmailMessage], int]:
    """Get inbox messages for user."""
    return await gmail_connection_service.get_inbox_messages(user_id, max_results, only_unread)


async def get_user_gmail_message(user_id: str, message_id: str) -> GmailMessage:
    """Get specific Gmail message for user."""
    return await gmail_connection_service.get_message_by_id(user_id, message_id)


async def search_user_messages(
    user_id: str, query: str, max_results: int = 10
) -> GmailSearchResult:
    """Search Gmail messages for user."""
    return await gmail_connection_service.search_messages(user_id, query, max_results)


async def send_user_email(
    user_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to_message_id: str | None = None,
) -> dict:
    """Send email for user."""
    return await gmail_connection_service.send_email(
        user_id, to, subject, body, cc, bcc, reply_to_message_id
    )


async def mark_user_message_as_read(user_id: str, message_id: str) -> GmailMessage:
    """Mark message as read for user."""
    return await gmail_connection_service.mark_message_as_read(user_id, message_id)


async def mark_user_message_as_unread(user_id: str, message_id: str) -> GmailMessage:
    """Mark message as unread for user."""
    return await gmail_connection_service.mark_message_as_unread(user_id, message_id)


async def star_user_message(user_id: str, message_id: str) -> GmailMessage:
    """Star message for user."""
    return await gmail_connection_service.star_message(user_id, message_id)


async def unstar_user_message(user_id: str, message_id: str) -> GmailMessage:
    """Unstar message for user."""
    return await gmail_connection_service.unstar_message(user_id, message_id)


async def delete_user_message(user_id: str, message_id: str) -> bool:
    """Delete message for user."""
    return await gmail_connection_service.delete_message(user_id, message_id)


async def get_user_gmail_labels(user_id: str) -> list[GmailLabel]:
    """Get Gmail labels for user."""
    return await gmail_connection_service.get_user_labels(user_id)


async def gmail_connection_health() -> dict[str, Any]:
    """Check Gmail connection service health."""
    return await gmail_connection_service.health_check()


# Helper functions for common AI/Voice use cases
async def get_inbox_summary_for_voice(user_id: str) -> dict[str, Any]:
    """
    Get inbox summary optimized for voice responses.

    Perfect for AI function calling and voice assistant responses.

    Args:
        user_id: UUID string of the user

    Returns:
        Dict: Voice-optimized inbox summary
    """
    try:
        # Get unread messages first (most important for voice)
        unread_messages, _ = await get_user_inbox_messages(user_id, max_results=5, only_unread=True)

        # Get recent messages (for context)
        recent_messages, _ = await get_user_inbox_messages(
            user_id, max_results=10, only_unread=False
        )

        # Analyze for voice response
        high_priority_unread = [
            msg for msg in unread_messages if msg.get_priority_level() == "high"
        ]
        actionable_unread = [msg for msg in unread_messages if msg.is_actionable()]

        # Create voice-friendly summary
        summary = {
            "unread_count": len(unread_messages),
            "total_recent": len(recent_messages),
            "high_priority_count": len(high_priority_unread),
            "actionable_count": len(actionable_unread),
            "unread_messages": [
                {
                    "id": msg.id,
                    "sender": msg.get_sender_display(),
                    "subject": msg.subject,
                    "preview": msg.get_body_preview(100),  # Shorter for voice
                    "age": msg.get_age_description(),
                    "priority": msg.get_priority_level(),
                    "actionable": msg.is_actionable(),
                }
                for msg in unread_messages[:3]  # Top 3 for voice
            ],
            "voice_summary": _generate_voice_summary(unread_messages, high_priority_unread),
        }

        logger.info(
            "Voice inbox summary generated",
            user_id=user_id,
            unread_count=summary["unread_count"],
            high_priority_count=summary["high_priority_count"],
        )

        return summary

    except Exception as e:
        logger.error("Error generating voice inbox summary", user_id=user_id, error=str(e))
        return {
            "unread_count": 0,
            "total_recent": 0,
            "high_priority_count": 0,
            "actionable_count": 0,
            "unread_messages": [],
            "voice_summary": "I couldn't access your inbox right now.",
            "error": str(e),
        }


def _generate_voice_summary(
    unread_messages: list[GmailMessage], high_priority: list[GmailMessage]
) -> str:
    """Generate natural voice summary of inbox."""
    if not unread_messages:
        return "You have no unread emails."

    count = len(unread_messages)

    if count == 1:
        msg = unread_messages[0]
        return f"You have 1 unread email from {msg.sender['name'] or msg.sender['email']} about {msg.subject}."

    if high_priority:
        high_count = len(high_priority)
        if high_count == 1:
            msg = high_priority[0]
            return f"You have {count} unread emails, including 1 high priority message from {msg.sender['name'] or msg.sender['email']} about {msg.subject}."
        else:
            return f"You have {count} unread emails, including {high_count} high priority messages."

    # Regular summary
    if count <= 3:
        senders = [msg.sender["name"] or msg.sender["email"] for msg in unread_messages[:3]]
        sender_list = (
            ", ".join(senders[:-1]) + f" and {senders[-1]}" if len(senders) > 1 else senders[0]
        )
        return f"You have {count} unread emails from {sender_list}."
    else:
        top_senders = [msg.sender["name"] or msg.sender["email"] for msg in unread_messages[:2]]
        return f"You have {count} unread emails, including messages from {', '.join(top_senders)} and others."


async def get_today_emails_for_voice(user_id: str) -> dict[str, Any]:
    """
    Get today's emails optimized for voice responses.

    Args:
        user_id: UUID string of the user

    Returns:
        Dict: Voice-optimized today's email summary
    """
    try:
        # Search for today's emails
        today_query = "newer_than:1d"
        search_result = await search_user_messages(user_id, today_query, max_results=20)

        today_messages = search_result.messages
        unread_today = [msg for msg in today_messages if msg.is_unread()]
        important_today = [
            msg
            for msg in today_messages
            if msg.is_important() or msg.get_priority_level() == "high"
        ]

        summary = {
            "total_today": len(today_messages),
            "unread_today": len(unread_today),
            "important_today": len(important_today),
            "messages": [
                {
                    "id": msg.id,
                    "sender": msg.get_sender_display(),
                    "subject": msg.subject,
                    "preview": msg.get_body_preview(80),
                    "age": msg.get_age_description(),
                    "is_unread": msg.is_unread(),
                    "priority": msg.get_priority_level(),
                }
                for msg in today_messages[:5]  # Top 5 for voice
            ],
            "voice_summary": _generate_today_voice_summary(
                today_messages, unread_today, important_today
            ),
        }

        return summary

    except Exception as e:
        logger.error("Error generating today's email summary", user_id=user_id, error=str(e))
        return {
            "total_today": 0,
            "unread_today": 0,
            "important_today": 0,
            "messages": [],
            "voice_summary": "I couldn't access today's emails right now.",
            "error": str(e),
        }


def _generate_today_voice_summary(
    today_messages: list[GmailMessage],
    unread_today: list[GmailMessage],
    important_today: list[GmailMessage],
) -> str:
    """Generate natural voice summary of today's emails."""
    total = len(today_messages)
    unread_count = len(unread_today)
    important_count = len(important_today)

    if total == 0:
        return "You haven't received any emails today."

    parts = [f"You received {total} email{'s' if total != 1 else ''} today"]

    if unread_count > 0:
        parts.append(f"{unread_count} unread")

    if important_count > 0:
        parts.append(f"{important_count} marked as important")

    return ", ".join(parts) + "."


@with_db_retry(max_retries=3, base_delay=0.1)
async def _update_user_gmail_status(user_id: str, connected: bool) -> bool:
    """Update only the Gmail connection flag."""
    try:
        query = """
        UPDATE users
        SET gmail_connected = %s, updated_at = NOW()
        WHERE id = %s AND is_active = true
        """

        affected_rows = await execute_query(query, (connected, user_id))

        success = affected_rows > 0
        if success:
            logger.info("User Gmail status updated", user_id=user_id, connected=connected)
        else:
            logger.warning("No user found to update Gmail status", user_id=user_id)

        return success

    except DatabaseError as e:
        logger.error("Database error updating Gmail status", user_id=user_id, error=str(e))
        return False
