# app/routes/health.py - Updated with database pool health
"""
Updated health check endpoints with database pool monitoring.
"""

import time

from fastapi import APIRouter

from app.db.pool import db_health_check
from app.services.redis_store import ping

router = APIRouter()


@router.get("/healthz")
async def healthz():
    """Basic health check - always returns 200 if app is running."""
    return {"status": "ok", "service": "voice-gmail-assistant"}


# Replace the readyz endpoint in app/routes/health.py


@router.get("/readyz")
async def readyz():
    """
    Readiness check with all dependencies including database pool.
    """
    checks = {}
    overall_ok = True

    # 1) Redis health check
    t0 = time.time()
    try:
        redis_ok = await ping()
        checks["redis"] = {
            "ok": bool(redis_ok),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "connection_type": "native_pooled",
        }
        overall_ok = overall_ok and bool(redis_ok)
    except Exception as e:
        checks["redis"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False

    # 2) Database pool health check - FIXED
    t0 = time.time()
    try:
        db_health = await db_health_check()

        # Extract key information safely
        is_healthy = db_health.get("healthy", False)
        latency_ms = round((time.time() - t0) * 1000, 1)

        checks["database"] = {
            "ok": is_healthy,
            "latency_ms": latency_ms,
        }

        # Add pool stats if available
        if "pool_stats" in db_health:
            pool_stats = db_health["pool_stats"]
            checks["database"].update(
                {
                    "pool_size": pool_stats.get("pool_size", 0),
                    "pool_available": pool_stats.get("pool_available", 0),
                    "pool_utilization_percent": pool_stats.get("pool_utilization_percent", 0),
                    "connection_time_ms": db_health.get("connection_time_ms", 0),
                }
            )

        # Add any warnings
        if "warnings" in db_health:
            checks["database"]["warnings"] = db_health["warnings"]

        # Add error info if unhealthy
        if not is_healthy:
            checks["database"]["error"] = db_health.get("error", "Database unhealthy")
            if "error_type" in db_health:
                checks["database"]["error_type"] = db_health["error_type"]

        overall_ok = overall_ok and is_healthy

    except Exception as e:
        checks["database"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": round((time.time() - t0) * 1000, 1),
        }
        overall_ok = False

    # 3) Configuration checks
    from app.config import settings

    config_ok = True
    config_issues = []

    if not settings.SUPABASE_DB_URL:
        config_issues.append("SUPABASE_DB_URL not set")
        config_ok = False

    if not settings.UPSTASH_REDIS_REST_URL:
        config_issues.append("UPSTASH_REDIS_REST_URL not set")
        config_ok = False

    checks["configuration"] = {
        "ok": config_ok,
        "issues": config_issues if config_issues else None,
        "environment": settings.environment,
    }
    overall_ok = overall_ok and config_ok

    return {"overall_ok": overall_ok, "checks": checks, "timestamp": time.time()}


# Add the new database health endpoint
@router.get("/health/database")
async def database_health():
    """Detailed database pool health information."""
    return await db_health_check()


# Add pool stats endpoint for monitoring
@router.get("/health/pool-stats")
async def pool_stats():
    """Get real-time pool statistics."""
    try:
        from app.db.pool import db_pool

        if not db_pool._initialized:
            return {"error": "Pool not initialized", "pool_health": "not_initialized"}

        if not db_pool.pool:
            return {"error": "Pool object not available", "pool_health": "error"}

        # Get stats and handle both object/dict formats
        stats = db_pool.pool.get_stats()

        if hasattr(stats, "pool_size"):
            # Stats is an object
            pool_size = stats.pool_size
            pool_available = stats.pool_available
            requests_waiting = stats.requests_waiting
            requests_num = stats.requests_num
            requests_queued = getattr(stats, "requests_queued", 0)
            requests_errors = getattr(stats, "requests_errors", 0)
            connections_num = getattr(stats, "connections_num", 0)
        elif isinstance(stats, dict):
            # Stats is a dict
            pool_size = stats.get("pool_size", 0)
            pool_available = stats.get("pool_available", 0)
            requests_waiting = stats.get("requests_waiting", 0)
            requests_num = stats.get("requests_num", 0)
            requests_queued = stats.get("requests_queued", 0)
            requests_errors = stats.get("requests_errors", 0)
            connections_num = stats.get("connections_num", 0)
        else:
            return {"error": f"Unexpected stats format: {type(stats)}", "pool_health": "error"}

        # Calculate utilization
        utilization = ((pool_size - pool_available) / pool_size * 100) if pool_size > 0 else 0

        return {
            "pool_health": "healthy",
            "pool_size": pool_size,
            "available_connections": pool_available,
            "active_connections": pool_size - pool_available,
            "utilization_percent": round(utilization, 1),
            "requests_waiting": requests_waiting,
            "total_requests": requests_num,
            "requests_queued": requests_queued,
            "request_errors": requests_errors,
            "connections_created": connections_num,
        }

    except Exception as e:
        return {"error": str(e), "pool_health": "error", "error_type": type(e).__name__}
