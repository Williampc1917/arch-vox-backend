import pytest

from app.services.calendar.google_client import GoogleCalendarService
from app.services.gmail.google_client import GoogleGmailError, GoogleGmailService


@pytest.mark.asyncio
async def test_gmail_list_messages_success(httpx_mock):
    service = GoogleGmailService()

    httpx_mock.add_response(
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
        json={"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1},
        match_querystring=False,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg-1",
        json={
            "id": "msg-1",
            "threadId": "thread-1",
            "labelIds": ["INBOX"],
            "snippet": "hello",
            "payload": {"headers": []},
        },
        match_querystring=False,
    )

    messages, total, _ = await service.list_messages("token", max_results=1)
    await service.close()

    assert total == 1
    assert len(messages) == 1
    assert messages[0].id == "msg-1"


@pytest.mark.asyncio
async def test_gmail_list_messages_error_mapping(httpx_mock):
    service = GoogleGmailService()

    httpx_mock.add_response(
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
        status_code=401,
        json={"error": {"code": 401, "message": "Invalid Credentials"}},
        match_querystring=False,
    )

    with pytest.raises(GoogleGmailError) as exc:
        await service.list_messages("token", max_results=1)

    await service.close()

    assert "authorization" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_calendar_list_events_success(httpx_mock):
    service = GoogleCalendarService()

    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
        json={
            "items": [
                {
                    "id": "event-1",
                    "status": "confirmed",
                    "summary": "Standup",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T10:30:00Z"},
                }
            ]
        },
        match_querystring=False,
    )

    events = await service.list_events("token")
    await service.close()

    assert len(events) == 1
    assert events[0].id == "event-1"
