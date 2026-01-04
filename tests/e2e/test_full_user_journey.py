from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.gmail_auth import router as gmail_auth_router


def test_full_oauth_journey(monkeypatch, apply_auth_override, fake_redis):
    async def fake_start_gmail_oauth(user_id: str):
        return "https://example.com/oauth", "state-123"

    async def fake_complete_gmail_oauth(user_id: str, code: str, state: str):
        return {"success": True, "onboarding_completed": True, "onboarding_step": "completed"}

    async def fake_state_owner(state: str):
        return "user-123"

    monkeypatch.setattr("app.routes.gmail_auth.oauth.start_gmail_oauth", fake_start_gmail_oauth)
    monkeypatch.setattr(
        "app.routes.gmail_auth.oauth.complete_gmail_oauth", fake_complete_gmail_oauth
    )
    monkeypatch.setattr("app.routes.gmail_auth.oauth.get_oauth_state_owner", fake_state_owner)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.fast_redis", fake_redis)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.environment", "development")
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.VIP_BACKFILL_ENABLED", False)

    app = FastAPI()
    apply_auth_override(app)
    app.include_router(gmail_auth_router)
    client = TestClient(app)

    url_response = client.get("/auth/gmail/url")
    assert url_response.status_code == 200
    state = url_response.json()["state"]

    callback_response = client.get("/auth/gmail/callback", params={"code": "abc", "state": state})
    assert callback_response.status_code == 200

    retrieve_response = client.get(f"/auth/gmail/callback/retrieve/{state}")
    assert retrieve_response.status_code == 200
    assert retrieve_response.json()["code"] == "abc"

    post_response = client.post("/auth/gmail/callback", json={"code": "abc", "state": state})
    assert post_response.status_code == 200
    assert post_response.json()["success"] is True
