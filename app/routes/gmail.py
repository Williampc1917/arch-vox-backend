"""
Gmail API Routes
HTTP endpoints for Gmail operations and message management.
Mirrors the pattern established in calendar.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.gmail_request import (
    CreateDraftRequest,
    GetMessageRequest,
    GetMessagesRequest,
    ModifyMessageRequest,
    SearchMessagesRequest,
    SendEmailRequest,
    VoiceInboxSummaryRequest,
    VoiceTodayEmailsRequest,
)
from app.models.api.gmail_response import (
    CreateDraftResponse,
    DeleteMessageResponse,
    GmailHealthResponse,
    GmailMessageResponse,
    GmailStatusResponse,
    GmailThreadResponse,
    LabelsListResponse,
    MessagesListResponse,
    ModifyMessageResponse,
    SearchResultsResponse,
    SendEmailResponse,
    VoiceInboxSummaryResponse,
    VoiceTodayEmailsResponse,
)
from app.services.gmail_operations_service import (
    GmailConnectionError,
    delete_user_message,
    get_gmail_status,
    get_inbox_summary_for_voice,
    get_today_emails_for_voice,
    get_user_gmail_labels,
    get_user_gmail_message,
    get_user_inbox_messages,
    gmail_connection_health,
    mark_user_message_as_read,
    mark_user_message_as_unread,
    search_user_messages,
    send_user_email,
    star_user_message,
    unstar_user_message,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.get("/status", response_model=GmailStatusResponse)
async def get_gmail_connection_status(claims: dict = Depends(auth_dependency)):
    """Get Gmail connection status for authenticated user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        gmail_status = await get_gmail_status(user_id)

        return GmailStatusResponse(
            connected=gmail_status.connected,
            messages_accessible=gmail_status.messages_accessible,
            connection_health=gmail_status.connection_health,
            can_send_email=gmail_status.can_send_email,
            can_read_email=gmail_status.can_read_email,
            expires_at=gmail_status.expires_at,
            needs_refresh=gmail_status.needs_refresh,
            health_details=gmail_status.health_details,
            quota_remaining=gmail_status.health_details.get("quota_remaining"),
        )

    except Exception as e:
        logger.error("Error getting Gmail status", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Gmail status",
        )


@router.get("/messages", response_model=MessagesListResponse)
async def get_messages(
    claims: dict = Depends(auth_dependency),
    max_results: int = Query(default=10, ge=1, le=100, description="Maximum messages to return"),
    only_unread: bool = Query(default=False, description="Only return unread messages"),
    label_ids: list[str] = Query(default=[], description="Label IDs to filter by"),
    query: str = Query(default=None, description="Gmail search query"),
):
    """Get inbox messages for user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        messages, total_count = await get_user_inbox_messages(user_id, max_results, only_unread)

        # Convert domain models to API response models
        message_responses = [
            GmailMessageResponse(
                id=msg.id,
                thread_id=msg.thread_id,
                subject=msg.subject,
                sender=msg.sender,
                recipient=msg.recipient,
                cc=msg.cc,
                bcc=msg.bcc,
                snippet=msg.snippet,
                body_text=msg.body_text,
                body_html=msg.body_html,
                attachments=msg.attachments,
                labels=msg.label_ids,
                is_unread=msg.is_unread(),
                is_starred=msg.is_starred(),
                is_important=msg.is_important(),
                has_attachments=msg.has_attachments(),
                received_datetime=msg.get_received_datetime(),
                sender_display=msg.get_sender_display(),
                body_preview=msg.get_body_preview(),
                size_estimate=msg.size_estimate,
                age_description=msg.get_age_description(),
                priority_level=msg.get_priority_level(),
                is_actionable=msg.is_actionable(),
            )
            for msg in messages
        ]

        return MessagesListResponse(
            messages=message_responses,
            total_found=total_count,
            query_parameters={
                "max_results": max_results,
                "only_unread": only_unread,
                "label_ids": label_ids,
                "query": query,
            },
            has_more=total_count > len(messages),  # Fixed logic: check if total exceeds returned count
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting messages", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get messages"
        )


@router.get("/messages/{message_id}", response_model=GmailMessageResponse)
async def get_message(message_id: str, claims: dict = Depends(auth_dependency)):
    """Get specific message by ID."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        message = await get_user_gmail_message(user_id, message_id)

        return GmailMessageResponse(
            id=message.id,
            thread_id=message.thread_id,
            subject=message.subject,
            sender=message.sender,
            recipient=message.recipient,
            cc=message.cc,
            bcc=message.bcc,
            snippet=message.snippet,
            body_text=message.body_text,
            body_html=message.body_html,
            attachments=message.attachments,
            labels=message.label_ids,
            is_unread=message.is_unread(),
            is_starred=message.is_starred(),
            is_important=message.is_important(),
            has_attachments=message.has_attachments(),
            received_datetime=message.get_received_datetime(),
            sender_display=message.get_sender_display(),
            body_preview=message.get_body_preview(),
            size_estimate=message.size_estimate,
            age_description=message.get_age_description(),
            priority_level=message.get_priority_level(),
            is_actionable=message.is_actionable(),
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get message"
        )


@router.post("/search", response_model=SearchResultsResponse)
async def search_messages(request: SearchMessagesRequest, claims: dict = Depends(auth_dependency)):
    """Search messages with Gmail query syntax."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        search_result = await search_user_messages(user_id, request.query, request.max_results)

        # Convert domain models to API response models
        message_responses = [
            GmailMessageResponse(
                id=msg.id,
                thread_id=msg.thread_id,
                subject=msg.subject,
                sender=msg.sender,
                recipient=msg.recipient,
                cc=msg.cc,
                bcc=msg.bcc,
                snippet=msg.snippet,
                body_text=msg.body_text,
                body_html=msg.body_html,
                attachments=msg.attachments,
                labels=msg.label_ids,
                is_unread=msg.is_unread(),
                is_starred=msg.is_starred(),
                is_important=msg.is_important(),
                has_attachments=msg.has_attachments(),
                received_datetime=msg.get_received_datetime(),
                sender_display=msg.get_sender_display(),
                body_preview=msg.get_body_preview(),
                size_estimate=msg.size_estimate,
                age_description=msg.get_age_description(),
                priority_level=msg.get_priority_level(),
                is_actionable=msg.is_actionable(),
            )
            for msg in search_result.messages
        ]

        return SearchResultsResponse(
            messages=message_responses,
            query=search_result.query,
            total_found=search_result.total_found,
            has_more=search_result.has_more,
            unread_count=search_result.get_unread_count(),
            high_priority_count=len(search_result.get_high_priority_messages()),
            actionable_count=len(search_result.get_actionable_messages()),
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error", user_id=user_id, query=request.query, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error searching messages", user_id=user_id, query=request.query, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search messages"
        )


@router.post("/send", response_model=SendEmailResponse)
async def send_email(request: SendEmailRequest, claims: dict = Depends(auth_dependency)):
    """Send an email."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        result = await send_user_email(
            user_id=user_id,
            to=request.to,
            subject=request.subject,
            body=request.body,
            cc=request.cc,
            bcc=request.bcc,
            reply_to_message_id=request.reply_to_message_id,
        )

        return SendEmailResponse(
            success=True,
            message_id=result.get("id", ""),
            thread_id=result.get("threadId"),
            message=f"Email sent successfully to {', '.join(request.to)}",
            recipients={
                "to": request.to,
                "cc": request.cc or [],
                "bcc": request.bcc or [],
            },
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error sending email", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error sending email", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send email"
        )


@router.post("/messages/{message_id}/read", response_model=ModifyMessageResponse)
async def mark_as_read(message_id: str, claims: dict = Depends(auth_dependency)):
    """Mark message as read."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        updated_message = await mark_user_message_as_read(user_id, message_id)

        message_response = GmailMessageResponse(
            id=updated_message.id,
            thread_id=updated_message.thread_id,
            subject=updated_message.subject,
            sender=updated_message.sender,
            recipient=updated_message.recipient,
            cc=updated_message.cc,
            bcc=updated_message.bcc,
            snippet=updated_message.snippet,
            body_text=updated_message.body_text,
            body_html=updated_message.body_html,
            attachments=updated_message.attachments,
            labels=updated_message.label_ids,
            is_unread=updated_message.is_unread(),
            is_starred=updated_message.is_starred(),
            is_important=updated_message.is_important(),
            has_attachments=updated_message.has_attachments(),
            received_datetime=updated_message.get_received_datetime(),
            sender_display=updated_message.get_sender_display(),
            body_preview=updated_message.get_body_preview(),
            size_estimate=updated_message.size_estimate,
            age_description=updated_message.get_age_description(),
            priority_level=updated_message.get_priority_level(),
            is_actionable=updated_message.is_actionable(),
        )

        return ModifyMessageResponse(
            success=True,
            message=message_response,
            changes_made=["marked as read"],
            message_text="Message marked as read successfully",
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error marking as read", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error marking message as read", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mark message as read"
        )


@router.post("/messages/{message_id}/unread", response_model=ModifyMessageResponse)
async def mark_as_unread(message_id: str, claims: dict = Depends(auth_dependency)):
    """Mark message as unread."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        updated_message = await mark_user_message_as_unread(user_id, message_id)

        message_response = GmailMessageResponse(
            id=updated_message.id,
            thread_id=updated_message.thread_id,
            subject=updated_message.subject,
            sender=updated_message.sender,
            recipient=updated_message.recipient,
            cc=updated_message.cc,
            bcc=updated_message.bcc,
            snippet=updated_message.snippet,
            body_text=updated_message.body_text,
            body_html=updated_message.body_html,
            attachments=updated_message.attachments,
            labels=updated_message.label_ids,
            is_unread=updated_message.is_unread(),
            is_starred=updated_message.is_starred(),
            is_important=updated_message.is_important(),
            has_attachments=updated_message.has_attachments(),
            received_datetime=updated_message.get_received_datetime(),
            sender_display=updated_message.get_sender_display(),
            body_preview=updated_message.get_body_preview(),
            size_estimate=updated_message.size_estimate,
            age_description=updated_message.get_age_description(),
            priority_level=updated_message.get_priority_level(),
            is_actionable=updated_message.is_actionable(),
        )

        return ModifyMessageResponse(
            success=True,
            message=message_response,
            changes_made=["marked as unread"],
            message_text="Message marked as unread successfully",
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error marking as unread", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error marking message as unread", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mark message as unread"
        )


@router.post("/messages/{message_id}/star", response_model=ModifyMessageResponse)
async def star_message(message_id: str, claims: dict = Depends(auth_dependency)):
    """Star a message."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        updated_message = await star_user_message(user_id, message_id)

        message_response = GmailMessageResponse(
            id=updated_message.id,
            thread_id=updated_message.thread_id,
            subject=updated_message.subject,
            sender=updated_message.sender,
            recipient=updated_message.recipient,
            cc=updated_message.cc,
            bcc=updated_message.bcc,
            snippet=updated_message.snippet,
            body_text=updated_message.body_text,
            body_html=updated_message.body_html,
            attachments=updated_message.attachments,
            labels=updated_message.label_ids,
            is_unread=updated_message.is_unread(),
            is_starred=updated_message.is_starred(),
            is_important=updated_message.is_important(),
            has_attachments=updated_message.has_attachments(),
            received_datetime=updated_message.get_received_datetime(),
            sender_display=updated_message.get_sender_display(),
            body_preview=updated_message.get_body_preview(),
            size_estimate=updated_message.size_estimate,
            age_description=updated_message.get_age_description(),
            priority_level=updated_message.get_priority_level(),
            is_actionable=updated_message.is_actionable(),
        )

        return ModifyMessageResponse(
            success=True,
            message=message_response,
            changes_made=["starred"],
            message_text="Message starred successfully",
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error starring message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error starring message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to star message"
        )


@router.delete("/messages/{message_id}/star", response_model=ModifyMessageResponse)
async def unstar_message(message_id: str, claims: dict = Depends(auth_dependency)):
    """Unstar a message."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        updated_message = await unstar_user_message(user_id, message_id)

        message_response = GmailMessageResponse(
            id=updated_message.id,
            thread_id=updated_message.thread_id,
            subject=updated_message.subject,
            sender=updated_message.sender,
            recipient=updated_message.recipient,
            cc=updated_message.cc,
            bcc=updated_message.bcc,
            snippet=updated_message.snippet,
            body_text=updated_message.body_text,
            body_html=updated_message.body_html,
            attachments=updated_message.attachments,
            labels=updated_message.label_ids,
            is_unread=updated_message.is_unread(),
            is_starred=updated_message.is_starred(),
            is_important=updated_message.is_important(),
            has_attachments=updated_message.has_attachments(),
            received_datetime=updated_message.get_received_datetime(),
            sender_display=updated_message.get_sender_display(),
            body_preview=updated_message.get_body_preview(),
            size_estimate=updated_message.size_estimate,
            age_description=updated_message.get_age_description(),
            priority_level=updated_message.get_priority_level(),
            is_actionable=updated_message.is_actionable(),
        )

        return ModifyMessageResponse(
            success=True,
            message=message_response,
            changes_made=["unstarred"],
            message_text="Message unstarred successfully",
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error unstarring message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error unstarring message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unstar message"
        )


@router.delete("/messages/{message_id}", response_model=DeleteMessageResponse)
async def delete_message(message_id: str, claims: dict = Depends(auth_dependency)):
    """Delete a message (move to trash)."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        success = await delete_user_message(user_id, message_id)

        if success:
            return DeleteMessageResponse(
                success=True,
                message_id=message_id,
                message="Message deleted successfully",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to delete message",
            )

    except GmailConnectionError as e:
        logger.error("Gmail connection error deleting message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error deleting message", user_id=user_id, message_id=message_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete message"
        )


@router.get("/labels", response_model=LabelsListResponse)
async def get_labels(claims: dict = Depends(auth_dependency)):
    """Get Gmail labels for user."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        labels = await get_user_gmail_labels(user_id)

        # Convert domain models to API response models
        label_responses = [
            {
                "id": label.id,
                "name": label.name,
                "display_name": label.get_display_name(),
                "type": label.type,
                "is_system": label.is_system_label(),
                "messages_total": label.messages_total,
                "messages_unread": label.messages_unread,
                "threads_total": label.threads_total,
                "threads_unread": label.threads_unread,
            }
            for label in labels
        ]

        # Separate system and user labels
        system_labels = [label for label in label_responses if label["is_system"]]
        user_labels = [label for label in label_responses if not label["is_system"]]

        return LabelsListResponse(
            labels=label_responses,
            system_labels=system_labels,
            user_labels=user_labels,
            total_count=len(label_responses),
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error getting labels", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting labels", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get labels"
        )


# Voice-optimized endpoints for AI assistant
@router.get("/voice/inbox-summary", response_model=VoiceInboxSummaryResponse)
async def get_voice_inbox_summary(claims: dict = Depends(auth_dependency)):
    """Get voice-optimized inbox summary for AI assistant."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        summary = await get_inbox_summary_for_voice(user_id)

        return VoiceInboxSummaryResponse(
            unread_count=summary["unread_count"],
            total_recent=summary["total_recent"],
            high_priority_count=summary["high_priority_count"],
            actionable_count=summary["actionable_count"],
            unread_messages=summary["unread_messages"],
            voice_summary=summary["voice_summary"],
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error getting voice summary", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting voice inbox summary", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to get inbox summary"
        )


@router.get("/voice/today", response_model=VoiceTodayEmailsResponse)
async def get_voice_today_emails(claims: dict = Depends(auth_dependency)):
    """Get today's emails optimized for voice responses."""
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        today_summary = await get_today_emails_for_voice(user_id)

        return VoiceTodayEmailsResponse(
            total_today=today_summary["total_today"],
            unread_today=today_summary["unread_today"],
            important_today=today_summary["important_today"],
            messages=today_summary["messages"],
            voice_summary=today_summary["voice_summary"],
        )

    except GmailConnectionError as e:
        logger.error("Gmail connection error getting today's emails", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error getting today's emails", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to get today's emails"
        )


@router.get("/health", response_model=GmailHealthResponse)
async def gmail_health_check():
    """Health check for Gmail system."""
    try:
        from datetime import datetime

        health = await gmail_connection_health()

        # Structure the health response according to our API model
        return GmailHealthResponse(
            healthy=health.get("healthy", False),
            service="gmail",
            timestamp=datetime.now(),
            google_gmail_api={
                "healthy": health.get("gmail_api_connectivity") == "ok",
                "connectivity": health.get("gmail_api_connectivity", "unknown"),
            },
            oauth_tokens={
                "healthy": True,  # Would need more detailed check
                "system_operational": True,
            },
            database_connectivity={
                "healthy": health.get("database_connectivity") == "ok",
                "status": health.get("database_connectivity", "unknown"),
            },
            supported_operations=health.get(
                "capabilities",
                [
                    "get_connection_status",
                    "get_inbox_messages",
                    "get_message_by_id",
                    "search_messages",
                    "send_email",
                    "mark_as_read",
                    "star_message",
                    "delete_message",
                    "get_labels",
                ],
            ),
            api_version="v1",
            issues_found=[],  # Would be populated based on health check results
            recommendations=[],  # Would be generated based on issues found
        )

    except Exception as e:
        logger.error("Gmail health check failed", error=str(e))
        return GmailHealthResponse(
            healthy=False,
            service="gmail",
            timestamp=datetime.now(),
            google_gmail_api={"healthy": False, "error": str(e)},
            oauth_tokens={"healthy": False, "error": "Health check failed"},
            database_connectivity={"healthy": False, "error": str(e)},
            supported_operations=[],
            api_version="v1",
            issues_found=[f"Health check failed: {str(e)}"],
            recommendations=[{"priority": "high", "action": "Check service logs"}],
        )