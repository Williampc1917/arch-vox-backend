"""
Google Gmail API Service for email operations and message management.
Handles Gmail API client initialization, raw API calls, and response parsing.
CLEAN ARCHITECTURE: Pure API client - imports domain models from separate file.
Low-level Gmail API client
/services/google_gmail_service.py
"""

import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.infrastructure.observability.logging import get_logger
from app.models.domain.gmail_domain import GmailMessage, GmailThread, GmailLabel

logger = get_logger(__name__)

# Google Gmail API configuration
GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
GMAIL_USER_ID = "me"  # User's Gmail account

# Request timeouts and retry configuration
REQUEST_TIMEOUT = 30  # seconds (email operations can be slower)
MAX_RETRIES = 3
BACKOFF_FACTOR = 2


class GoogleGmailError(Exception):
    """Custom exception for Google Gmail API errors."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        status_code: int | None = None,
        response_data: dict | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.response_data = response_data or {}


class GoogleGmailService:
    """
    Service for Google Gmail API operations.

    Pure API client that handles HTTP requests, authentication, error handling,
    and retry logic. Domain model creation is handled by importing from gmail_domain.py.
    """

    def __init__(self):
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy for Gmail API."""
        session = requests.Session()

        # Configure retry strategy for Gmail API
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP codes
            allowed_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],  # Gmail operations
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        return session

    def _get_auth_headers(self, access_token: str) -> dict:
        """Get authorization headers for Gmail API requests."""
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _handle_api_response(self, response: requests.Response, operation: str) -> dict:
        """
        Handle and validate Gmail API response.

        Args:
            response: HTTP response from Gmail API
            operation: Operation name for logging

        Returns:
            dict: Parsed response data

        Raises:
            GoogleGmailError: If response contains errors
        """
        logger.debug(
            f"Gmail API {operation} response",
            status_code=response.status_code,
            response_size=len(response.text) if response.text else 0,
        )

        # Handle successful responses
        if response.ok:
            try:
                return response.json() if response.text else {}
            except ValueError as e:
                logger.error(f"Failed to parse Gmail API {operation} response", error=str(e))
                raise GoogleGmailError(f"Invalid response format: {e}") from e

        # Handle API errors
        try:
            error_data = response.json() if response.text else {}
            error_info = error_data.get("error", {})

            error_code = error_info.get("code", "unknown")
            error_message = error_info.get("message", "Unknown Gmail API error")

            logger.error(
                f"Gmail API {operation} failed",
                status_code=response.status_code,
                error_code=error_code,
                error_message=error_message,
            )

            # Map common Gmail API errors
            user_message = self._map_gmail_error(error_code, error_message)

            raise GoogleGmailError(
                user_message,
                error_code=str(error_code),
                status_code=response.status_code,
                response_data=error_data,
            )

        except ValueError:
            # Non-JSON error response
            logger.error(
                f"Gmail API {operation} failed with non-JSON response",
                status_code=response.status_code,
                response_text=response.text[:200] if response.text else "",
            )
            raise GoogleGmailError(
                f"Gmail API error (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from None

    def _map_gmail_error(self, error_code: str, error_message: str) -> str:
        """Map Gmail API error codes to user-friendly messages."""
        error_mappings = {
            "403": "Gmail access denied. Please check permissions.",
            "404": "Email message not found.",
            "400": "Invalid Gmail request format.",
            "401": "Gmail authorization expired. Please reconnect.",
            "429": "Too many Gmail requests. Please try again later.",
            "500": "Gmail service temporarily unavailable.",
        }

        return error_mappings.get(error_code, f"Gmail error: {error_message}")

    async def list_messages(
        self,
        access_token: str,
        max_results: int = 10,
        label_ids: list[str] | None = None,
        query: str | None = None,
        include_spam_trash: bool = False,
        page_token: str | None = None,
    ) -> tuple[list[GmailMessage], int]:
        """
        List messages from user's Gmail.

        Args:
            access_token: Valid OAuth access token
            max_results: Maximum number of messages to return
            label_ids: List of label IDs to filter by (e.g., ["INBOX", "UNREAD"])
            query: Gmail search query (e.g., "is:unread subject:meeting")
            include_spam_trash: Whether to include spam and trash
            page_token: Token for pagination

        Returns:
            Tuple[list[GmailMessage], int]: (List of Gmail messages, Total count)

        Raises:
            GoogleGmailError: If listing messages fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/messages"
            headers = self._get_auth_headers(access_token)

            # Build query parameters
            params = {
                "maxResults": min(max_results, 500),  # Gmail API limit
                "includeSpamTrash": include_spam_trash,
            }

            if label_ids:
                params["labelIds"] = label_ids

            if query:
                params["q"] = query

            if page_token:
                params["pageToken"] = page_token

            logger.info(
                "Listing Gmail messages",
                max_results=max_results,
                label_ids=label_ids,
                query=query,
            )

            response = self._session.get(
                url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "list_messages")

            # Get total count from resultSizeEstimate
            total_count = data.get("resultSizeEstimate", 0)
            
            # Get message IDs and fetch full message details
            message_ids = [msg["id"] for msg in data.get("messages", [])]
            
            if not message_ids:
                logger.info("No messages found")
                return [], total_count

            # Fetch full message details (could be optimized with batch requests)
            messages = []
            for msg_id in message_ids:
                try:
                    message = await self.get_message(access_token, msg_id)
                    messages.append(message)
                except GoogleGmailError as e:
                    logger.warning(f"Failed to get message {msg_id}", error=str(e))
                    continue

            logger.info("Messages listed successfully", message_count=len(messages), total_count=total_count)
            return messages, total_count

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error listing messages", error=str(e))
            raise GoogleGmailError(f"Failed to list messages: {e}") from e

    async def get_message(
        self, access_token: str, message_id: str, format: str = "full"
    ) -> GmailMessage:
        """
        Get a specific message by ID.

        Args:
            access_token: Valid OAuth access token
            message_id: Gmail message ID
            format: Message format ("full", "metadata", "minimal", "raw")

        Returns:
            GmailMessage: Gmail message details

        Raises:
            GoogleGmailError: If getting message fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/messages/{message_id}"
            headers = self._get_auth_headers(access_token)
            params = {"format": format}

            logger.info("Getting Gmail message", message_id=message_id, format=format)

            response = self._session.get(
                url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "get_message")

            # Create domain model from API response
            message = GmailMessage(data)
            logger.info("Message retrieved successfully", message_id=message_id)
            return message

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error getting message", message_id=message_id, error=str(e))
            raise GoogleGmailError(f"Failed to get message: {e}") from e

    async def get_thread(
        self, access_token: str, thread_id: str, format: str = "full"
    ) -> GmailThread:
        """
        Get a specific thread by ID.

        Args:
            access_token: Valid OAuth access token
            thread_id: Gmail thread ID
            format: Message format for thread messages

        Returns:
            GmailThread: Gmail thread with messages

        Raises:
            GoogleGmailError: If getting thread fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/threads/{thread_id}"
            headers = self._get_auth_headers(access_token)
            params = {"format": format}

            logger.info("Getting Gmail thread", thread_id=thread_id, format=format)

            response = self._session.get(
                url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "get_thread")

            # Create domain model from API response
            thread = GmailThread(data)
            logger.info("Thread retrieved successfully", thread_id=thread_id)
            return thread

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error getting thread", thread_id=thread_id, error=str(e))
            raise GoogleGmailError(f"Failed to get thread: {e}") from e

    async def send_message(
        self,
        access_token: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        """
        Send an email message.

        Args:
            access_token: Valid OAuth access token
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: List of CC recipients
            bcc: List of BCC recipients
            reply_to: Reply-to address
            thread_id: Thread ID for replies

        Returns:
            dict: Sent message information

        Raises:
            GoogleGmailError: If sending message fails
        """
        try:
            # Create email message
            msg = MIMEMultipart()
            msg["To"] = ", ".join(to)
            msg["Subject"] = subject
            
            if cc:
                msg["Cc"] = ", ".join(cc)
            if reply_to:
                msg["Reply-To"] = reply_to

            # Add body
            msg.attach(MIMEText(body, "plain"))

            # Encode message
            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

            # Prepare request data
            send_data = {"raw": raw_message}
            if thread_id:
                send_data["threadId"] = thread_id

            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/messages/send"
            headers = self._get_auth_headers(access_token)

            logger.info(
                "Sending Gmail message",
                to=to,
                subject=subject,
                has_cc=bool(cc),
                has_bcc=bool(bcc),
                is_reply=bool(thread_id),
            )

            response = self._session.post(
                url, headers=headers, data=json.dumps(send_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "send_message")

            logger.info("Message sent successfully", message_id=data.get("id"))
            return data

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error sending message", error=str(e))
            raise GoogleGmailError(f"Failed to send message: {e}") from e

    async def modify_message(
        self,
        access_token: str,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> GmailMessage:
        """
        Modify message labels (mark as read/unread, star, etc.).

        Args:
            access_token: Valid OAuth access token
            message_id: Gmail message ID
            add_label_ids: Labels to add (e.g., ["STARRED"])
            remove_label_ids: Labels to remove (e.g., ["UNREAD"])

        Returns:
            GmailMessage: Updated message

        Raises:
            GoogleGmailError: If modifying message fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/messages/{message_id}/modify"
            headers = self._get_auth_headers(access_token)

            modify_data = {}
            if add_label_ids:
                modify_data["addLabelIds"] = add_label_ids
            if remove_label_ids:
                modify_data["removeLabelIds"] = remove_label_ids

            logger.info(
                "Modifying Gmail message",
                message_id=message_id,
                add_labels=add_label_ids,
                remove_labels=remove_label_ids,
            )

            response = self._session.post(
                url, headers=headers, data=json.dumps(modify_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "modify_message")

            # Create domain model from API response
            message = GmailMessage(data)
            logger.info("Message modified successfully", message_id=message_id)
            return message

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error modifying message", message_id=message_id, error=str(e))
            raise GoogleGmailError(f"Failed to modify message: {e}") from e

    async def delete_message(self, access_token: str, message_id: str) -> bool:
        """
        Delete a message (move to trash).

        Args:
            access_token: Valid OAuth access token
            message_id: Gmail message ID

        Returns:
            bool: True if deletion successful

        Raises:
            GoogleGmailError: If deleting message fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/messages/{message_id}/trash"
            headers = self._get_auth_headers(access_token)

            logger.info("Deleting Gmail message", message_id=message_id)

            response = self._session.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
            self._handle_api_response(response, "delete_message")

            logger.info("Message deleted successfully", message_id=message_id)
            return True

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error deleting message", message_id=message_id, error=str(e))
            raise GoogleGmailError(f"Failed to delete message: {e}") from e

    async def get_labels(self, access_token: str) -> list[GmailLabel]:
        """
        Get user's Gmail labels.

        Args:
            access_token: Valid OAuth access token

        Returns:
            list[GmailLabel]: List of Gmail labels

        Raises:
            GoogleGmailError: If getting labels fails
        """
        try:
            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/labels"
            headers = self._get_auth_headers(access_token)

            logger.info("Getting Gmail labels")

            response = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            data = self._handle_api_response(response, "get_labels")

            # Create domain models from API response
            labels = []
            for label_data in data.get("labels", []):
                label = GmailLabel(label_data)
                labels.append(label)

            logger.info("Labels retrieved successfully", label_count=len(labels))
            return labels

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error getting labels", error=str(e))
            raise GoogleGmailError(f"Failed to get labels: {e}") from e

    async def create_draft(
        self,
        access_token: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict:
        """
        Create a draft message.

        Args:
            access_token: Valid OAuth access token
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: List of CC recipients
            bcc: List of BCC recipients

        Returns:
            dict: Created draft information

        Raises:
            GoogleGmailError: If creating draft fails
        """
        try:
            # Create email message
            msg = MIMEMultipart()
            msg["To"] = ", ".join(to)
            msg["Subject"] = subject
            
            if cc:
                msg["Cc"] = ", ".join(cc)

            # Add body
            msg.attach(MIMEText(body, "plain"))

            # Encode message
            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

            # Prepare request data
            draft_data = {
                "message": {
                    "raw": raw_message
                }
            }

            url = f"{GMAIL_API_BASE_URL}/users/{GMAIL_USER_ID}/drafts"
            headers = self._get_auth_headers(access_token)

            logger.info("Creating Gmail draft", to=to, subject=subject)

            response = self._session.post(
                url, headers=headers, data=json.dumps(draft_data), timeout=REQUEST_TIMEOUT
            )
            data = self._handle_api_response(response, "create_draft")

            logger.info("Draft created successfully", draft_id=data.get("id"))
            return data

        except GoogleGmailError:
            raise
        except Exception as e:
            logger.error("Unexpected error creating draft", error=str(e))
            raise GoogleGmailError(f"Failed to create draft: {e}") from e

    def health_check(self) -> dict[str, Any]:
        """
        Check Google Gmail service health.

        Returns:
            Dict: Health status and configuration
        """
        try:
            health_data = {
                "healthy": True,
                "service": "google_gmail",
                "api_base_url": GMAIL_API_BASE_URL,
                "request_timeout": REQUEST_TIMEOUT,
                "max_retries": MAX_RETRIES,
                "supported_operations": [
                    "list_messages",
                    "get_message",
                    "get_thread",
                    "send_message",
                    "modify_message",
                    "delete_message",
                    "get_labels",
                    "create_draft",
                ],
            }

            # Test basic connectivity to Google Gmail API
            try:
                # Simple HEAD request to check API availability
                response = requests.head(GMAIL_API_BASE_URL, timeout=5)
                health_data["api_connectivity"] = (
                    "ok"
                    if response.status_code in [200, 401, 403]
                    else f"error_{response.status_code}"
                )
            except requests.exceptions.RequestException as e:
                health_data["api_connectivity"] = f"error_{type(e).__name__}"
                health_data["healthy"] = False

            return health_data

        except Exception as e:
            logger.error("Google Gmail service health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "google_gmail",
                "error": str(e),
            }


# Singleton instance for application use
google_gmail_service = GoogleGmailService()


# Convenience functions for easy import
async def list_user_messages(
    access_token: str,
    max_results: int = 10,
    label_ids: list[str] | None = None,
    query: str | None = None,
) -> tuple[list[GmailMessage], int]:
    """List messages for user."""
    return await google_gmail_service.list_messages(access_token, max_results, label_ids, query)


async def get_gmail_message(access_token: str, message_id: str) -> GmailMessage:
    """Get specific Gmail message."""
    return await google_gmail_service.get_message(access_token, message_id)


async def get_gmail_thread(access_token: str, thread_id: str) -> GmailThread:
    """Get specific Gmail thread."""
    return await google_gmail_service.get_thread(access_token, thread_id)


async def send_gmail_message(
    access_token: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    """Send Gmail message."""
    return await google_gmail_service.send_message(access_token, to, subject, body, cc, bcc)


async def mark_message_as_read(access_token: str, message_id: str) -> GmailMessage:
    """Mark message as read."""
    return await google_gmail_service.modify_message(
        access_token, message_id, remove_label_ids=["UNREAD"]
    )


async def mark_message_as_unread(access_token: str, message_id: str) -> GmailMessage:
    """Mark message as unread."""
    return await google_gmail_service.modify_message(
        access_token, message_id, add_label_ids=["UNREAD"]
    )


async def star_message(access_token: str, message_id: str) -> GmailMessage:
    """Star a message."""
    return await google_gmail_service.modify_message(
        access_token, message_id, add_label_ids=["STARRED"]
    )


async def unstar_message(access_token: str, message_id: str) -> GmailMessage:
    """Unstar a message."""
    return await google_gmail_service.modify_message(
        access_token, message_id, remove_label_ids=["STARRED"]
    )


async def get_gmail_labels(access_token: str) -> list[GmailLabel]:
    """Get Gmail labels."""
    return await google_gmail_service.get_labels(access_token)


def google_gmail_health() -> dict[str, Any]:
    """Check Google Gmail service health."""
    return google_gmail_service.health_check()