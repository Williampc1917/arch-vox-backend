"""
Tests for health check endpoints.
"""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import health

app = FastAPI()
app.include_router(health.router)
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
        patch("app.routes.health.fast_redis.ping", new=AsyncMock(return_value=True)),
        patch(
            "app.routes.health.db_health_check",
            new=AsyncMock(
                return_value={
                    "healthy": True,
                    "pool_stats": {
                        "pool_size": 5,
                        "pool_available": 5,
                        "pool_utilization_percent": 0,
                    },
                }
            ),
        ),
        patch("app.routes.health.settings.SUPABASE_DB_URL", "postgres://test"),
        patch("app.routes.health.settings.UPSTASH_REDIS_REST_URL", "https://redis"),
    ):

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is True
        assert "checks" in data

        # Check that all services are reported as healthy
        checks = data["checks"]
        assert checks["redis"]["ok"] is True
        assert checks["database"]["ok"] is True
        assert checks["configuration"]["ok"] is True


def test_readyz_endpoint_redis_unhealthy():
    """Test readiness endpoint when Redis is down."""
    with (
        patch("app.routes.health.fast_redis.ping", new=AsyncMock(return_value=False)),
        patch(
            "app.routes.health.db_health_check",
            new=AsyncMock(return_value={"healthy": True}),
        ),
        patch("app.routes.health.settings.SUPABASE_DB_URL", "postgres://test"),
        patch("app.routes.health.settings.UPSTASH_REDIS_REST_URL", "https://redis"),
    ):

        response = client.get("/readyz")

        # Should still return 200, but overall_ok should be False
        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["redis"]["ok"] is False


def test_readyz_endpoint_postgres_unhealthy():
    """Test readiness endpoint when Postgres is down."""
    with (
        patch("app.routes.health.fast_redis.ping", new=AsyncMock(return_value=True)),
        patch(
            "app.routes.health.db_health_check",
            new=AsyncMock(
                return_value={
                    "healthy": False,
                    "error": "Connection failed",
                    "error_type": "OperationalError",
                }
            ),
        ),
        patch("app.routes.health.settings.SUPABASE_DB_URL", "postgres://test"),
        patch("app.routes.health.settings.UPSTASH_REDIS_REST_URL", "https://redis"),
    ):

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["database"]["ok"] is False
        assert data["checks"]["database"]["error"] == "Connection failed"


def test_readyz_endpoint_missing_config():
    """Test readiness endpoint when required config is missing."""
    with (
        patch("app.routes.health.fast_redis.ping", new=AsyncMock(return_value=True)),
        patch("app.routes.health.db_health_check", new=AsyncMock(return_value={"healthy": True})),
        patch("app.routes.health.settings.SUPABASE_DB_URL", ""),
        patch("app.routes.health.settings.UPSTASH_REDIS_REST_URL", ""),
    ):

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_ok"] is False
        assert data["checks"]["configuration"]["ok"] is False
        assert "SUPABASE_DB_URL not set" in data["checks"]["configuration"]["issues"]
        assert "UPSTASH_REDIS_REST_URL not set" in data["checks"]["configuration"]["issues"]


def test_readyz_includes_latency_metrics():
    """Test that readiness checks include latency metrics."""
    with (
        patch("app.routes.health.fast_redis.ping", new=AsyncMock(return_value=True)),
        patch("app.routes.health.db_health_check", new=AsyncMock(return_value={"healthy": True})),
        patch("app.routes.health.settings.SUPABASE_DB_URL", "postgres://test"),
        patch("app.routes.health.settings.UPSTASH_REDIS_REST_URL", "https://redis"),
    ):

        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()

        # Check that latency metrics are included
        checks = data["checks"]
        assert "latency_ms" in checks["redis"]
        assert "latency_ms" in checks["database"]
        assert isinstance(checks["redis"]["latency_ms"], int | float)
        assert isinstance(checks["database"]["latency_ms"], int | float)
