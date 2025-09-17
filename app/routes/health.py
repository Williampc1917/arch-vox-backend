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

    # 2) Database pool health check
    t0 = time.time()
    try:
        db_health = await db_health_check()
        checks["database"] = {
            "ok": db_health.get("healthy", False),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            **db_health  # Include all pool metrics
        }
        overall_ok = overall_ok and db_health.get("healthy", False)
    except Exception as e:
        checks["database"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
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
        "environment": settings.environment
    }
    overall_ok = overall_ok and config_ok

    return {
        "overall_ok": overall_ok,
        "checks": checks,
        "timestamp": time.time()
    }


@router.get("/health/database")
async def database_health():
    """Detailed database pool health information."""
    return await db_health_check()