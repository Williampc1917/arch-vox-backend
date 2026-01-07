"""Debug route aggregation."""

from fastapi import APIRouter

from app.routes.debug import health, vip_metrics

router = APIRouter()

router.include_router(health.router)
router.include_router(vip_metrics.router)
