"""
Tests for health check endpoints.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_endpoint():
    """Test the basic health check endpoint."""
    response = client.get("/healthz")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_readyz_endpoint_all_services_healthy():
    """Test readiness endpoint when all services are healthy."""
    with (
        patch("app.routes.health.redis_ping", return_value=True),
        patch("app.routes.health.check_db", return_value=True),
        patch("app.routes.health.settings.SUPABASE_JWT_SECRET", "test-secret"),
        patch("app.routes.health.settings.VAPI_PRIVATE_KEY", "test-key"),
        patch("app.routes.health.requests.get") as mock_get,
    ):

        # Mock Vapi API response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is True
        assert "checks" in data

        # Check that all services are reported as healthy
        checks = data["checks"]
        assert checks["redis"]["ok"] is True
        assert checks["postgres"]["ok"] is True
        assert checks["supabase_auth"]["ok"] is True
        assert checks["vapi"]["ok"] is True


def test_readyz_endpoint_redis_unhealthy():
    """Test readiness endpoint when Redis is down."""
    with (
        patch("app.routes.health.redis_ping", return_value=False),
        patch("app.routes.health.check_db", return_value=True),
        patch("app.routes.health.settings.SUPABASE_JWT_SECRET", "test-secret"),
        patch("app.routes.health.settings.VAPI_PRIVATE_KEY", "test-key"),
        patch("app.routes.health.requests.get") as mock_get,
    ):

        # Mock Vapi API response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = client.get("/readyz")

        # Should still return 200, but overall_ok should be False
        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["redis"]["ok"] is False


def test_readyz_endpoint_postgres_unhealthy():
    """Test readiness endpoint when Postgres is down."""
    with (
        patch("app.routes.health.redis_ping", return_value=True),
        patch("app.routes.health.check_db", return_value="Connection failed"),
        patch("app.routes.health.settings.SUPABASE_JWT_SECRET", "test-secret"),
        patch("app.routes.health.settings.VAPI_PRIVATE_KEY", "test-key"),
        patch("app.routes.health.requests.get") as mock_get,
    ):

        # Mock Vapi API response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["postgres"]["ok"] is False
        assert data["checks"]["postgres"]["error"] == "Connection failed"


def test_readyz_endpoint_missing_jwt_secret():
    """Test readiness endpoint when JWT secret is missing."""
    with (
        patch("app.routes.health.redis_ping", return_value=True),
        patch("app.routes.health.check_db", return_value=True),
        patch("app.routes.health.settings.SUPABASE_JWT_SECRET", None),
        patch("app.routes.health.settings.VAPI_PRIVATE_KEY", "test-key"),
        patch("app.routes.health.requests.get") as mock_get,
    ):

        # Mock Vapi API response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["supabase_auth"]["ok"] is False
        assert "SUPABASE_JWT_SECRET not set" in data["checks"]["supabase_auth"]["error"]


def test_readyz_includes_latency_metrics():
    """Test that readiness checks include latency metrics."""
    with (
        patch("app.routes.health.redis_ping", return_value=True),
        patch("app.routes.health.check_db", return_value=True),
        patch("app.routes.health.settings.SUPABASE_JWT_SECRET", "test-secret"),
        patch("app.routes.health.settings.VAPI_PRIVATE_KEY", "test-key"),
        patch("app.routes.health.requests.get") as mock_get,
    ):

        # Mock Vapi API response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()

        # Check that latency metrics are included
        checks = data["checks"]
        assert "latency_ms" in checks["redis"]
        assert "latency_ms" in checks["postgres"]
        assert "latency_ms" in checks["vapi"]
        assert isinstance(checks["redis"]["latency_ms"], (int, float))
        assert isinstance(checks["postgres"]["latency_ms"], (int, float))
        assert isinstance(checks["vapi"]["latency_ms"], (int, float))
