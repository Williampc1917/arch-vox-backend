"""Gmail auth route aggregation."""

from fastapi import APIRouter

from app.routes.gmail_auth import health, oauth, status

router = APIRouter(prefix="/auth/gmail", tags=["gmail-auth"])

router.include_router(oauth.router)
router.include_router(status.router)
router.include_router(health.router)
