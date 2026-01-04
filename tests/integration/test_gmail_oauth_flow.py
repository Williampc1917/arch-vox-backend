import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.gmail_auth import router as gmail_auth_router
from app.services.gmail.auth_service import GmailConnectionError


def _create_app(apply_auth_override):
    app = FastAPI()
    apply_auth_override(app)
    app.include_router(gmail_auth_router)
    return app


def test_get_oauth_url_success(monkeypatch, apply_auth_override):
    async def fake_start_gmail_oauth(user_id: str):
        return "https://example.com/oauth", "state-123"

    monkeypatch.setattr("app.routes.gmail_auth.oauth.start_gmail_oauth", fake_start_gmail_oauth)

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.get("/auth/gmail/url")
    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_url"] == "https://example.com/oauth"
    assert payload["state"] == "state-123"
    assert payload["total_scopes"] == 6


def test_callback_redirect_success_non_prod(monkeypatch, apply_auth_override, fake_redis):
    async def fake_state_owner(state: str):
        return "user-123"

    monkeypatch.setattr("app.routes.gmail_auth.oauth.get_oauth_state_owner", fake_state_owner)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.fast_redis", fake_redis)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.environment", "development")

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.get("/auth/gmail/callback", params={"code": "abc", "state": "state-123"})
    assert response.status_code == 200


def test_callback_redirect_unknown_state(monkeypatch, apply_auth_override, fake_redis):
    async def fake_state_owner(state: str):
        return None

    monkeypatch.setattr("app.routes.gmail_auth.oauth.get_oauth_state_owner", fake_state_owner)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.fast_redis", fake_redis)
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.environment", "development")

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.get("/auth/gmail/callback", params={"code": "abc", "state": "state-123"})
    assert response.status_code == 400


def test_retrieve_oauth_data_success(monkeypatch, apply_auth_override, fake_redis):
    oauth_data = {
        "code": "abc",
        "state": "state-123",
        "user_id": "user-123",
        "timestamp": "2024-01-01T00:00:00Z",
        "expires_at": 0,
    }
    fake_redis.store["oauth_callback_data:state-123"] = json.dumps(oauth_data)

    monkeypatch.setattr("app.routes.gmail_auth.oauth.fast_redis", fake_redis)

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.get("/auth/gmail/callback/retrieve/state-123")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "abc"
    assert payload["state"] == "state-123"
    assert "oauth_callback_data:state-123" not in fake_redis.store


def test_post_callback_success(monkeypatch, apply_auth_override):
    async def fake_complete_gmail_oauth(user_id: str, code: str, state: str):
        return {"success": True, "onboarding_completed": True, "onboarding_step": "completed"}

    monkeypatch.setattr(
        "app.routes.gmail_auth.oauth.complete_gmail_oauth", fake_complete_gmail_oauth
    )
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.VIP_BACKFILL_ENABLED", False)

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.post("/auth/gmail/callback", json={"code": "abc", "state": "state-123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["gmail_connected"] is True


def test_post_callback_invalid_state(monkeypatch, apply_auth_override):
    async def fake_complete_gmail_oauth(user_id: str, code: str, state: str):
        raise GmailConnectionError("bad state", error_code="invalid_state")

    monkeypatch.setattr(
        "app.routes.gmail_auth.oauth.complete_gmail_oauth", fake_complete_gmail_oauth
    )
    monkeypatch.setattr("app.routes.gmail_auth.oauth.settings.VIP_BACKFILL_ENABLED", False)

    app = _create_app(apply_auth_override)
    client = TestClient(app)

    response = client.post("/auth/gmail/callback", json={"code": "abc", "state": "state-123"})
    assert response.status_code == 401
