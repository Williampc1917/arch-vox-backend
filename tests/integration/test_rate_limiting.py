from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.verify import auth_dependency
from app.middleware.rate_limit_dependencies import rate_limit_user_only
from app.middleware.rate_limit_headers import RateLimitHeadersMiddleware


def test_rate_limit_headers_middleware():
    app = FastAPI()
    app.add_middleware(RateLimitHeadersMiddleware)

    @app.get("/limited")
    async def limited(request: Request):
        request.state.rate_limit_info = {
            "allowed": True,
            "limit": 10,
            "remaining": 9,
            "retry_after": 0,
        }
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/limited")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "9"


def test_rate_limit_dependency_blocks(monkeypatch):
    app = FastAPI()

    def auth_override():
        return {"sub": "user-123"}

    app.dependency_overrides[auth_dependency] = auth_override

    async def fake_check_user_rate_limit(user_id: str, limit: int | None = None):
        return False, {
            "allowed": False,
            "limit": 5,
            "remaining": 0,
            "retry_after": 7,
        }

    async def fake_audit_event(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.middleware.rate_limit_dependencies.rate_limiter.check_user_rate_limit",
        fake_check_user_rate_limit,
    )
    monkeypatch.setattr(
        "app.utils.audit_helpers.audit_security_event",
        fake_audit_event,
    )
    monkeypatch.setattr(
        "app.middleware.rate_limit_dependencies.settings.RATE_LIMIT_ENABLED",
        True,
    )

    @app.get("/limited")
    async def limited(
        request: Request,
        _rate: None = Depends(rate_limit_user_only),
    ):
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/limited")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
