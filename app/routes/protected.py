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
from app.models.user import UserProfileResponse
from app.services.user_service import get_user_profile

router = APIRouter()
logger = get_logger(__name__)


@router.get("/me", response_model=UserProfileResponse)
async def me(claims: dict = Depends(auth_dependency)):
    """
    Return complete user profile from database plus JWT metadata.

    Args:
        claims: JWT claims from auth_dependency (includes sub, email, role, etc.)

    Returns:
        UserProfileResponse with profile data and auth metadata

    Raises:
        404: User not found in database or inactive
        500: Database connection issues
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Get user profile from database
    profile = await get_user_profile(user_id)
    if not profile:
        logger.warning("User profile not found", user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    # Prepare auth metadata from JWT claims
    auth_metadata = {
        "user_id": claims.get("sub"),
        "email": claims.get("email"),
        "role": claims.get("role", "authenticated"),
        "aud": claims.get("aud"),
        "iat": claims.get("iat"),
        "exp": claims.get("exp"),
    }

    logger.info("User profile retrieved successfully", user_id=user_id)

    return UserProfileResponse(profile=profile, auth=auth_metadata)
