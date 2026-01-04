"""Debug route aggregation."""

from fastapi import APIRouter

from app.routes.debug import health

router = APIRouter()

router.include_router(health.router)
