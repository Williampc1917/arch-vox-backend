import hashlib
import hmac
import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
VAPI_HEADER = "x-vapi-signature"  # adjust if Vapi uses a different header
VAPI_SECRET = os.getenv("VAPI_WEBHOOK_SECRET", "dev-secret")


def verify_vapi_hmac(raw: bytes, signature: str | None):
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    mac = hmac.new(VAPI_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if mac != signature:
        raise HTTPException(status_code=401, detail="Invalid signature")


@router.post("/voice/webhook")
async def voice_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get(VAPI_HEADER)
    verify_vapi_hmac(raw, sig)

    # (Optional) inspect payload during setup
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    say = (
        "Here are your top three. "
        "One, Sarah — project update. Two, Tom — contract needs signature. "
        "Three, Stripe — receipt processed."
    )
    return {"ok": True, "say": say, "debug": {"received": bool(payload)}}
