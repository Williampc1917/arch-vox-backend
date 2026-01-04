import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import voice


def _make_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_valid_signature(monkeypatch):
    app = FastAPI()
    app.include_router(voice.router)
    client = TestClient(app)

    monkeypatch.setattr("app.routes.voice.VAPI_SECRET", "test-secret")

    payload = {"event": "test"}
    raw = json.dumps(payload).encode("utf-8")
    signature = _make_signature("test-secret", raw)

    response = client.post(
        "/voice/webhook",
        data=raw,
        headers={"x-vapi-signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_webhook_invalid_signature(monkeypatch):
    app = FastAPI()
    app.include_router(voice.router)
    client = TestClient(app)

    monkeypatch.setattr("app.routes.voice.VAPI_SECRET", "test-secret")

    payload = {"event": "test"}
    raw = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/voice/webhook",
        data=raw,
        headers={"x-vapi-signature": "bad", "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_webhook_missing_secret(monkeypatch):
    app = FastAPI()
    app.include_router(voice.router)
    client = TestClient(app)

    monkeypatch.setattr("app.routes.voice.VAPI_SECRET", None)
    monkeypatch.setattr("app.routes.voice.settings.environment", "development")

    payload = {"event": "test"}
    raw = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/voice/webhook",
        data=raw,
        headers={"x-vapi-signature": "any", "Content-Type": "application/json"},
    )

    assert response.status_code == 401
