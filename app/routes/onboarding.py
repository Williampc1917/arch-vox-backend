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
from app.models.api.user_request import (
    CustomEmailStyleRequest,  # NEW
    EmailStyleSelectionRequest,  # NEW
    OnboardingProfileUpdateRequest,
)
from app.models.api.user_response import (
    CustomEmailStyleResponse,
    EmailStyleSelectionResponse,  # NEW
    EmailStyleStatusResponse,  # NEW
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


# UPDATE existing complete endpoint to require email_style step
@router.post("/complete", response_model=OnboardingCompleteResponse)
async def complete(claims: dict = Depends(auth_dependency)):
    """
    Mark onboarding as completed.

    Prerequisites:
        - User must be on 'email_style' onboarding step  # UPDATED
        - User must have gmail_connected = true
        - User must have selected an email style          # NEW REQUIREMENT

    Returns:
        OnboardingCompleteResponse: Success status and updated user profile
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Service layer call (now includes email style validation)
    profile = await complete_onboarding(user_id)

    if not profile:
        logger.warning("Onboarding completion failed - prerequisites not met", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete onboarding. Please ensure you have connected Gmail and selected an email style.",
        )

    response = OnboardingCompleteResponse(
        success=True,
        message="Congratulations! Onboarding completed successfully. You can now use all voice features.",
        user_profile=profile,
    )

    logger.info(
        "Onboarding completed successfully",
        user_id=user_id,
        user_email=profile.email,
        step_transition="email_style → completed",  # UPDATED
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


# NEW ENDPOINTS FOR EMAIL STYLE STEP


@router.get("/email-style", response_model=EmailStyleStatusResponse)
async def get_email_style_status(claims: dict = Depends(auth_dependency)):
    """
    Get current email style step status and available options.

    Returns:
        EmailStyleStatusResponse: Current selection status and options
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Get email style step status from onboarding service
    from app.services.onboarding_service import get_email_style_step_status

    step_status = await get_email_style_step_status(user_id)

    if "error" in step_status:
        logger.warning(
            "Email style status check failed", user_id=user_id, error=step_status["error"]
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=step_status["error"])

    response = EmailStyleStatusResponse(
        current_step=step_status["current_step"],
        style_selected=step_status["style_selected"],
        available_options=list(step_status["available_options"].values()),
        can_advance=step_status["can_advance"],
        rate_limit_info=step_status.get("rate_limit_info"),
    )

    logger.info(
        "Email style status retrieved",
        user_id=user_id,
        style_selected=step_status["style_selected"],
        can_advance=step_status["can_advance"],
    )

    return response


@router.put("/email-style", response_model=EmailStyleSelectionResponse)
async def select_email_style(
    request: EmailStyleSelectionRequest, claims: dict = Depends(auth_dependency)
):
    """
    Select casual or professional email style.
    No rate limiting needed for predefined styles.

    Returns:
        EmailStyleSelectionResponse: Selection result
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        # Select predefined email style
        from app.services.email_style_service import select_predefined_email_style

        result = await select_predefined_email_style(user_id, request.style_type)

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("message", "Failed to select email style"),
            )

        # Complete email style selection in onboarding
        from app.services.onboarding_service import complete_email_style_selection

        await complete_email_style_selection(user_id, result["style_type"], result["style_profile"])

        response = EmailStyleSelectionResponse(
            success=True,
            style_type=result["style_type"],
            next_step="completed",
            message=result["message"],
        )

        logger.info(
            "Email style selected successfully", user_id=user_id, style_type=request.style_type
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error selecting email style",
            user_id=user_id,
            style_type=request.style_type,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to select email style"
        )


@router.post("/email-style/custom", response_model=CustomEmailStyleResponse)
async def create_custom_email_style(
    request: CustomEmailStyleRequest, claims: dict = Depends(auth_dependency)
):
    """
    Create custom email style from 3 email examples.
    Includes rate limiting and OpenAI integration.

    Returns:
        CustomEmailStyleResponse: Custom style creation result
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        # Create custom email style (includes rate limiting + OpenAI)
        from app.services.email_style_service import create_custom_email_style

        result = await create_custom_email_style(user_id, request.email_examples)

        # Handle rate limiting
        if not result["success"] and result.get("error") == "rate_limit_exceeded":
            response = CustomEmailStyleResponse(
                success=False,
                style_profile=None,
                extraction_grade=None,
                error_message=result["message"],
                rate_limit_info=result.get("rate_limit_info"),
                next_step=None,
            )

            logger.warning(
                "Custom email style blocked by rate limit",
                user_id=user_id,
                rate_limit_info=result.get("rate_limit_info"),
            )

            return response

        # Handle extraction failure
        if not result["success"]:
            response = CustomEmailStyleResponse(
                success=False,
                style_profile=None,
                extraction_grade=None,
                error_message=result.get("message", "Custom style creation failed"),
                rate_limit_info=None,
                next_step=None,
            )

            logger.warning(
                "Custom email style creation failed", user_id=user_id, error=result.get("error")
            )

            return response

        # Success - complete email style selection in onboarding
        from app.services.onboarding_service import complete_email_style_selection

        await complete_email_style_selection(user_id, result["style_type"], result["style_profile"])

        response = CustomEmailStyleResponse(
            success=True,
            style_profile=result["style_profile"],
            extraction_grade=result.get("extraction_grade"),
            error_message=None,
            rate_limit_info=None,
            next_step="completed",
        )

        logger.info(
            "Custom email style created successfully",
            user_id=user_id,
            extraction_grade=result.get("extraction_grade"),
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error creating custom email style",
            user_id=user_id,
            email_count=len(request.email_examples),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create custom email style",
        )
