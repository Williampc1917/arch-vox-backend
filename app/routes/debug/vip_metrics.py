"""Debug-only VIP monitoring metrics."""

from fastapi import APIRouter

from app.features.vip_onboarding.services.monitoring_service import (
    vip_monitoring_service,
)

router = APIRouter(prefix="/health", tags=["debug-health"])


@router.get("/vip")
async def vip_metrics() -> dict:
    """Return VIP monitoring metrics for dashboards."""
    return await vip_monitoring_service.get_metrics()
