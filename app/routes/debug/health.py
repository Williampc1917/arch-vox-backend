"""Debug-only health detail endpoints."""

from fastapi import APIRouter

from app.db.pool import db_health_check

router = APIRouter()


@router.get("/health/database")
async def database_health():
    """Detailed database pool health information."""
    return await db_health_check()


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
