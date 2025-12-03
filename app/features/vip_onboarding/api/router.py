"""
VIP onboarding routes.

This router replaces the old placeholder under app/routes and lives in
the feature package so all HTTP endpoints related to VIP onboarding stay
in one place.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/onboarding/vips", tags=["onboarding-vips"])


@router.get("/status")
async def get_vip_onboarding_status() -> dict:
    """Temporary endpoint illustrating where VIP status will live."""
    return {"detail": "VIP onboarding endpoints will be added soon."}


@router.get("/")
async def list_vip_candidates() -> dict:
    """Temporary endpoint illustrating where VIP data will be served."""
    return {"vips": [], "detail": "VIP candidate list not implemented yet."}

