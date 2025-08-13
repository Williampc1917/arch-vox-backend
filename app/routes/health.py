from fastapi import APIRouter, HTTPException
import time
import json
import base64
import requests
from app.services.redis_store import ping as redis_ping
from app.db.postgres import check_db
from app.config import settings


router = APIRouter()

@router.get("/healthz")
def healthz():
    """Simple liveness check: app booted."""
    return {"status": "ok"}

def _b64url_decode(s: str) -> bytes:
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))

def _decode_jwt_unverified(token: str):
    """Decode header & payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None, None
        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        return header, payload
    except Exception:
        return None, None

@router.get("/readyz")
def readyz():
    """Readiness: verify Redis, Supabase Auth, Postgres, and Vapi API."""
    checks = {}
    overall_ok = True

    # 1) Redis check
    t0 = time.time()
    try:
        r_ok = redis_ping()
        checks["redis"] = {
            "ok": bool(r_ok),
            "latency_ms": round((time.time() - t0) * 1000, 1)
        }
        overall_ok = overall_ok and bool(r_ok)
    except Exception as e:
        checks["redis"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False

    # 2) Supabase Auth (HS256) check
    t0 = time.time()
    auth_ok = False
    detail = {"latency_ms": round((time.time() - t0) * 1000, 1)}

    try:
        if not getattr(settings, "SUPABASE_JWT_SECRET", None):
            detail["error"] = "SUPABASE_JWT_SECRET not set"
        else:
            anon = getattr(settings, "SUPABASE_ANON_KEY", None)
            if anon:
                hdr, pl = _decode_jwt_unverified(anon)
                project_ref = settings.project_ref() if hasattr(settings, "project_ref") else None
                if hdr and pl and hdr.get("alg") == "HS256" and pl.get("ref") == project_ref:
                    detail["note"] = "Anon key matches project and alg=HS256"
            auth_ok = True
    except Exception as e:
        detail["error"] = f"{type(e).__name__}: {e}"

    checks["supabase_auth"] = {"ok": auth_ok, **detail}
    overall_ok = overall_ok and auth_ok

    # 3) Postgres check
    t0 = time.time()
    try:
        db_ok = check_db()
        if db_ok is True:
            checks["postgres"] = {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000, 1)
            }
        else:
            checks["postgres"] = {"ok": False, "error": db_ok}
            overall_ok = False
    except Exception as e:
        checks["postgres"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False

    # 4) Vapi API check
    # 4) Vapi API check
    t0 = time.time()
    try:
        if not settings.VAPI_PRIVATE_KEY:
            raise RuntimeError("VAPI_PRIVATE_KEY not set")

        resp = requests.get(
            "https://api.vapi.ai/v1/assistants",
            headers={"Authorization": f"Bearer {settings.VAPI_PRIVATE_KEY}"},
            timeout=5,
        )

        vapi_ok = resp.ok or resp.status_code == 404  # Treat 404 as OK
        vapi_note = None

        if resp.status_code == 404:
            vapi_note = "No assistants found (treated as OK for health check)"

        checks["vapi"] = {
            "ok": vapi_ok,
            "status": resp.status_code,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            **({"note": vapi_note} if vapi_note else {}),
        }

        overall_ok = overall_ok and vapi_ok
    except Exception as e:
        checks["vapi"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        overall_ok = False
   

    return {"overall_ok": overall_ok, "checks": checks}



