import pytest

from app.services.gmail.google_client import GoogleGmailService


@pytest.mark.asyncio
async def test_gmail_metadata_headers_requested(httpx_mock):
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

    await service.list_messages(
        "token",
        max_results=1,
        message_format="metadata",
        metadata_headers=["List-Id", "Auto-Submitted"],
    )
    await service.close()

    requests = httpx_mock.get_requests()
    message_requests = [req for req in requests if "/messages/msg-1" in str(req.url)]
    assert message_requests

    params = message_requests[0].url.params
    assert params.get("format") == "metadata"
    assert params.getlist("metadataHeaders") == ["List-Id", "Auto-Submitted"]
