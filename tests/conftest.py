import pytest

from app.auth.verify import auth_dependency


@pytest.fixture
def auth_override():
    def _override():
        return {"sub": "user-123"}

    return _override


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set_with_ttl(self, key: str, value: str, ttl_s: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def apply_auth_override(auth_override):
    def _apply(app):
        app.dependency_overrides[auth_dependency] = auth_override

    return _apply
