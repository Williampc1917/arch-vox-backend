"""
protected.py
------------
Purpose:
    Defines protected API endpoints that require a valid Supabase Auth JWT for access.

    - Uses the `auth_dependency` from app.auth.verify to secure endpoints.
    - Enhanced `/me` endpoint returns complete user profile from database.

Usage:
    1. Ensure Supabase Auth is configured properly.
    2. Include this router in your `app/main.py` with:
         from app.routes import protected
         app.include_router(protected.router)
    3. Call `/me` with:
         Authorization: Bearer <access_token>
       where <access_token> is from Supabase Auth sign-in.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.user_response import AuthMeta, UserProfileResponse
from app.services.user_service import get_user_profile

router = APIRouter()
logger = get_logger(__name__)


@router.get("/me", response_model=UserProfileResponse)
async def me(claims: dict = Depends(auth_dependency)):
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    profile = await get_user_profile(user_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    auth = AuthMeta(
        user_id=claims.get("sub"),
        email=claims.get("email"),
        role=claims.get("role", "authenticated"),
        aud=claims.get("aud"),
        iat=claims.get("iat"),
        exp=claims.get("exp"),
    )

    return UserProfileResponse(profile=profile, auth=auth)
