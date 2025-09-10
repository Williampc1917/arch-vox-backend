"""
onboarding.py
-------------
Purpose:
    API endpoints for user onboarding flow management.
    Handles onboarding status, profile updates, and completion.

Architecture:
    - API layer: Handles HTTP concerns, validation, auth
    - Service layer: Returns domain models (UserProfile)
    - API layer: Converts domain models → HTTP response models

Usage:
    1. GET /onboarding/status - Check current onboarding state
    2. PUT /onboarding/profile - Update display name (timezone auto-detected)
    3. POST /onboarding/complete - Mark onboarding as finished
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.user_request import OnboardingProfileUpdateRequest
from app.models.api.user_response import (
    OnboardingCompleteResponse,
    OnboardingProfileUpdateResponse,
    OnboardingStatusResponse,
)
from app.services.onboarding_service import (
    complete_onboarding,
    get_onboarding_status,
    update_profile_name,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
logger = get_logger(__name__)


@router.get("/status", response_model=OnboardingStatusResponse)
async def get_status(claims: dict = Depends(auth_dependency)):
    """
    Get current onboarding status for the authenticated user.

    Returns:
        OnboardingStatusResponse: Current step, completion status, Gmail connection

    Raises:
        401: Invalid authentication token
        404: User profile not found
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Service layer returns domain model
    profile = await get_onboarding_status(user_id)
    if not profile:
        logger.warning("User profile not found for onboarding status", user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    # Convert domain model → API response model
    response = OnboardingStatusResponse(
        step=profile.onboarding_step,
        onboarding_completed=profile.onboarding_completed,
        gmail_connected=profile.gmail_connected,
        timezone=profile.timezone,
    )

    logger.info(
        "Onboarding status retrieved",
        user_id=user_id,
        step=profile.onboarding_step,
        completed=profile.onboarding_completed,
    )

    return response


@router.put("/profile", response_model=OnboardingProfileUpdateResponse)
async def update_profile(
    request: OnboardingProfileUpdateRequest,
    request_obj: Request,  # ← Add this to access headers
    claims: dict = Depends(auth_dependency),
):
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token: missing user ID")

    # Extract auto-detected timezone from iOS (header or default to UTC)
    timezone = request_obj.headers.get("X-Timezone", "UTC")

    # Service call with auto-detected timezone
    profile = await update_profile_name(
        user_id=user_id,
        display_name=request.display_name,
        timezone=timezone,  # ← iOS auto-detected or UTC fallback
    )

    if not profile:
        raise HTTPException(400, "Profile update failed...")

    return OnboardingProfileUpdateResponse(
        success=True,
        next_step="gmail",
        message=f"Profile updated! Welcome, {profile.display_name}.",
    )


@router.post("/complete", response_model=OnboardingCompleteResponse)
async def complete(claims: dict = Depends(auth_dependency)):
    """
    Mark onboarding as completed.

    Prerequisites:
        - User must be on 'gmail' onboarding step
        - User must have gmail_connected = true

    Returns:
        OnboardingCompleteResponse: Success status and updated user profile

    Raises:
        400: Completion failed (prerequisites not met)
        401: Invalid authentication token
        404: User profile not found
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Service layer call
    profile = await complete_onboarding(user_id)

    if not profile:
        logger.warning("Onboarding completion failed - prerequisites not met", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete onboarding. Please ensure you are on the Gmail step and have connected your Gmail account.",
        )

    # Convert domain model → API response model
    response = OnboardingCompleteResponse(
        success=True,
        message="Congratulations! Onboarding completed successfully. You can now use all voice features.",
        user_profile=profile,  # Include full profile for iOS app
    )

    logger.info(
        "Onboarding completed successfully",
        user_id=user_id,
        user_email=profile.email,
        step_transition="gmail → completed",
    )

    return response


# Health check endpoint for onboarding system
@router.get("/health")
async def onboarding_health():
    """
    Simple health check for onboarding endpoints.
    Public endpoint - no auth required.
    """
    return {
        "status": "ok",
        "service": "onboarding",
        "endpoints": [
            "GET /onboarding/status",
            "PUT /onboarding/profile",
            "POST /onboarding/complete",
        ],
    }
