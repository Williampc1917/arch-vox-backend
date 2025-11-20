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
    3. GET /onboarding/email-style - Get 3-profile status
    4. POST /onboarding/email-style/custom - Create 3 custom styles
    5. POST /onboarding/email-style/skip - Skip style creation (Gmail still required)
    6. POST /onboarding/complete - Mark onboarding as finished
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.verify import auth_dependency
from app.infrastructure.observability.logging import get_logger
from app.models.api.user_request import (
    CustomEmailStyleRequest,
    OnboardingProfileUpdateRequest,
)
from app.models.api.user_response import (
    CustomEmailStyleResponse,
    EmailStyleSkipResponse,
    EmailStyleStatusResponse,
    OnboardingCompleteResponse,
    OnboardingProfileUpdateResponse,
    OnboardingStatusResponse,
)
from app.services.onboarding_service import (
    OnboardingServiceError,
    complete_onboarding,
    get_onboarding_status,
    skip_email_style_step,
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
        email_style_skipped=profile.email_style_skipped,
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
    request_obj: Request,
    claims: dict = Depends(auth_dependency),
):
    """
    Update user profile during onboarding.
    
    Args:
        request: Profile update data (display_name)
        request_obj: FastAPI request object for headers
        claims: JWT claims from auth
    
    Returns:
        OnboardingProfileUpdateResponse: Success status and next step
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token: missing user ID")

    # Extract auto-detected timezone from iOS (header or default to UTC)
    timezone = request_obj.headers.get("X-Timezone", "UTC")

    # Service call with auto-detected timezone
    profile = await update_profile_name(
        user_id=user_id,
        display_name=request.display_name,
        timezone=timezone,
    )

    if not profile:
        raise HTTPException(400, "Profile update failed...")

    return OnboardingProfileUpdateResponse(
        success=True,
        next_step="gmail",
        message=f"Profile updated! Welcome, {profile.display_name}.",
    )


@router.get("/email-style", response_model=EmailStyleStatusResponse)
async def get_email_style_status(claims: dict = Depends(auth_dependency)):
    """
    Get current 3-profile email style status.

    Returns:
        EmailStyleStatusResponse: Status of all 3 profiles (professional, casual, friendly)
    
    Raises:
        401: Invalid authentication token
        400: User not on email_style step
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
        styles_created=step_status["styles_created"],
        all_styles_complete=step_status["all_styles_complete"],
        can_advance=step_status["can_advance"],
        rate_limit_info=step_status.get("rate_limit_info"),
    )

    logger.info(
        "Email style status retrieved",
        user_id=user_id,
        styles_created=step_status["styles_created"],
        all_complete=step_status["all_styles_complete"],
    )

    return response


@router.post("/email-style/custom", response_model=CustomEmailStyleResponse)
async def create_custom_email_style(
    request: CustomEmailStyleRequest, claims: dict = Depends(auth_dependency)
):
    """
    Create 3 custom email styles from labeled examples.
    Includes rate limiting and OpenAI integration.

    Args:
        request: Labeled emails (professional_email, casual_email, friendly_email)
        claims: JWT claims from auth

    Returns:
        CustomEmailStyleResponse: 3 style profiles with grades
    
    Raises:
        401: Invalid authentication token
        500: Style creation failed
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        # Prepare labeled emails dict from request
        labeled_emails = {
            "professional": request.professional_email,
            "casual": request.casual_email,
            "friendly": request.friendly_email,
        }

        # Create 3 custom styles (includes rate limiting + OpenAI)
        from app.services.email_style_service import create_custom_email_style

        result = await create_custom_email_style(user_id, labeled_emails)

        # Handle rate limiting
        if not result["success"] and result.get("error") == "rate_limit_exceeded":
            response = CustomEmailStyleResponse(
                success=False,
                style_profiles=None,
                extraction_grades=None,
                error_message=result["message"],
                rate_limit_info=result.get("rate_limit_info"),
                next_step=None,
            )

            logger.warning(
                "3-profile creation blocked by rate limit",
                user_id=user_id,
                rate_limit_info=result.get("rate_limit_info"),
            )

            return response

        # Handle extraction failure
        if not result["success"]:
            response = CustomEmailStyleResponse(
                success=False,
                style_profiles=None,
                extraction_grades=None,
                error_message=result.get("message", "3-profile style creation failed"),
                rate_limit_info=None,
                next_step=None,
            )

            logger.warning(
                "3-profile style creation failed", user_id=user_id, error=result.get("error")
            )

            return response

        # Success - complete email style selection in onboarding
        from app.services.onboarding_service import complete_email_style_selection

        selection_profile = await complete_email_style_selection(
            user_id, "custom", result["style_profiles"]
        )

        completed_profile = None
        next_step = "email_style"

        if selection_profile and selection_profile.onboarding_completed:
            completed_profile = selection_profile
            next_step = "completed"
            logger.info(
                "3-profile selection stored for user already marked completed",
                user_id=user_id,
            )
        else:
            # Complete onboarding now that all 3 styles are created
            try:
                completed_profile = await complete_onboarding(user_id)
                if completed_profile:
                    next_step = "completed"
                    logger.info(
                        "Onboarding completed after 3-profile creation",
                        user_id=user_id,
                        extraction_grades=result.get("extraction_grades"),
                    )
                else:
                    logger.warning(
                        "3 profiles created but onboarding completion failed",
                        user_id=user_id,
                    )
            except OnboardingServiceError as e:
                logger.warning(
                    "Failed onboarding completion after 3-profile creation",
                    user_id=user_id,
                    error=str(e),
                    recoverable=e.recoverable,
                )
            except Exception as e:
                logger.error(
                    "Unexpected error completing onboarding after 3-profile creation",
                    user_id=user_id,
                    error=str(e),
                )

        response = CustomEmailStyleResponse(
            success=True,
            style_profiles=result["style_profiles"],  # All 3 profiles
            extraction_grades=result.get("extraction_grades"),  # Grades per profile
            error_message=None,
            rate_limit_info=None,
            next_step=next_step,
        )

        logger.info(
            "3 custom email styles created successfully",
            user_id=user_id,
            extraction_grades=result.get("extraction_grades"),
            next_step=next_step,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error creating 3 custom email styles",
            user_id=user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create 3 custom email styles",
        )


@router.post("/email-style/skip", response_model=EmailStyleSkipResponse)
async def skip_email_style(claims: dict = Depends(auth_dependency)):
    """
    Skip email style creation while still allowing onboarding completion.
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    try:
        profile = await skip_email_style_step(user_id)
    except OnboardingServiceError as e:
        logger.warning(
            "Failed to skip email style step",
            user_id=user_id,
            error=str(e),
            recoverable=e.recoverable,
        )
        status_code = (
            status.HTTP_400_BAD_REQUEST if getattr(e, "recoverable", True) else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        raise HTTPException(status_code=status_code, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error skipping email style step", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to skip email style step",
        ) from e

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to skip email style step",
        )

    response = EmailStyleSkipResponse(
        success=True,
        message="Email style selection skipped. You can create custom styles later in settings.",
        user_profile=profile,
    )

    logger.info(
        "Email style step skipped by user",
        user_id=user_id,
        onboarding_step=profile.onboarding_step,
        onboarding_completed=profile.onboarding_completed,
    )

    return response


@router.post("/complete", response_model=OnboardingCompleteResponse)
async def complete(claims: dict = Depends(auth_dependency)):
    """
    Mark onboarding as completed.

    Prerequisites:
        - User must be on 'email_style' onboarding step
        - User must have gmail_connected = true
        - User must have all 3 email styles created

    Returns:
        OnboardingCompleteResponse: Success status and updated user profile
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.error("No user ID in JWT claims", claims=claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing user ID"
        )

    # Service layer call (includes email style validation)
    profile = await complete_onboarding(user_id)

    if not profile:
        logger.warning("Onboarding completion failed - prerequisites not met", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete onboarding. Please ensure you have connected Gmail and created all 3 email styles.",
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
        step_transition="email_style → completed",
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
            "GET /onboarding/email-style",
            "POST /onboarding/email-style/custom",
            "POST /onboarding/email-style/skip",
            "POST /onboarding/complete",
        ],
        "email_style_mode": "3-profile",
    }


@router.get("/email-style/health")
async def email_style_health():
    """
    Lightweight health ping for email style services.
    Public endpoint - no auth required.
    """
    return {
        "status": "ok",
        "service": "email_style_system",
        "mode": "3-profile",
        "components": {
            "openai": "not checked",
            "style_service": "not checked",
            "rate_limiter": "not checked",
        },
        "message": "For detailed diagnostics, use authenticated monitoring endpoints.",
    }
