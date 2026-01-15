# app/routes/health.py - Updated with database pool health
"""
Updated health check endpoints with database pool monitoring.
"""

import time

from fastapi import APIRouter

from app.config import settings
from app.db.neo4j import neo4j_health_check
from app.db.pool import db_health_check
from app.services.infrastructure.redis_client import fast_redis

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
        redis_ok = await fast_redis.ping()
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

    # 3) Neo4j health check
    t0 = time.time()
    if settings.NEO4J_SYNC_ENABLED:
        try:
            neo4j_status = await neo4j_health_check()
            is_healthy = neo4j_status.get("healthy", False)
            checks["neo4j"] = {
                "ok": is_healthy,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "database": settings.NEO4J_DATABASE,
            }

            if not is_healthy:
                checks["neo4j"]["error"] = neo4j_status.get("error", "Neo4j unhealthy")
                if "error_type" in neo4j_status:
                    checks["neo4j"]["error_type"] = neo4j_status["error_type"]

            overall_ok = overall_ok and is_healthy
        except Exception as e:
            checks["neo4j"] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": round((time.time() - t0) * 1000, 1),
            }
            overall_ok = False
    else:
        checks["neo4j"] = {"ok": True, "status": "disabled"}

    # 4) Configuration checks

    config_ok = True
    config_issues = []

    if not settings.SUPABASE_DB_URL:
        config_issues.append("SUPABASE_DB_URL not set")
        config_ok = False

    if not settings.UPSTASH_REDIS_REST_URL:
        config_issues.append("UPSTASH_REDIS_REST_URL not set")
        config_ok = False

    if settings.NEO4J_SYNC_ENABLED:
        if not settings.NEO4J_URI:
            config_issues.append("NEO4J_URI not set")
            config_ok = False
        if not settings.NEO4J_PASSWORD:
            config_issues.append("NEO4J_PASSWORD not set")
            config_ok = False

    checks["configuration"] = {
        "ok": config_ok,
        "issues": config_issues if config_issues else None,
        "environment": settings.environment,
    }
    overall_ok = overall_ok and config_ok

    return {"overall_ok": overall_ok, "checks": checks, "timestamp": time.time()}
