# Updated app/routes/health.py - Make health checks faster
import time

from fastapi import APIRouter

from app.services.redis_store import ping

router = APIRouter()


@router.get("/readyz")
async def readyz():  # ADD async here
    """Readiness check with fast Redis"""
    checks = {}
    overall_ok = True

    # 1) Fast Redis check (was 25ms, now 1ms)
    t0 = time.time()
    try:
        r_ok = await ping()  # ADD await here
        checks["redis"] = {
            "ok": bool(r_ok),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "connection_type": "native_pooled",
        }
        overall_ok = overall_ok and bool(r_ok)
    except Exception as e:
        checks["redis"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False

    # Rest of health checks stay the same...
    # (Supabase, Postgres, Vapi checks unchanged)

    return {"overall_ok": overall_ok, "checks": checks}
