"""
# app/services/onboarding_service.py
Onboarding service for managing user onboarding flow.
Handles onboarding status, profile updates, and completion with Gmail integration.
REFACTORED: Now supports 3-profile email style system.

Service layer returns domain models only - API layer handles HTTP concerns.
"""

from typing import Any

from app.db.helpers import (
    DatabaseError,
    execute_query,
    fetch_one,
    set_email_style_skipped,
    with_db_retry,
)
from app.infrastructure.observability.logging import get_logger
from app.models.domain.user_domain import UserProfile
from app.services.core.user_service import get_user_profile

logger = get_logger(__name__)


class OnboardingServiceError(Exception):
    """Custom exception for onboarding service operations."""

    def __init__(self, message: str, user_id: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.user_id = user_id
        self.recoverable = recoverable


async def get_onboarding_status(user_id: str) -> UserProfile | None:
    """
    Get current onboarding status for a user.

    Args:
        user_id: UUID string of the user

    Returns:
        UserProfile domain model if user found, None if not found

    Note:
        Reuses existing get_user_profile() function. API layer will extract
        onboarding fields (step, completed, gmail_connected, timezone).
    """
    try:
        # Reuse existing user profile function - no code duplication
        profile = await get_user_profile(user_id)

        if profile:
            logger.info(
                "Onboarding status retrieved",
                user_id=user_id,
                step=profile.onboarding_step,
                gmail_connected=profile.gmail_connected,
            )
        else:
            logger.warning("User not found for onboarding status", user_id=user_id)

        return profile

    except Exception as e:
        logger.error("Error getting onboarding status", user_id=user_id, error=str(e))
        return None


@with_db_retry(max_retries=3, base_delay=0.1)
async def update_profile_name(
    user_id: str, display_name: str, timezone: str = "UTC"
) -> UserProfile | None:
    """
    Update user's display name and advance onboarding to 'gmail' step.

    Args:
        user_id: UUID string of the user
        display_name: User's display name from input
        timezone: Auto-detected timezone from iOS (optional, defaults to UTC)

    Returns:
        Updated UserProfile domain model if successful, None if failed

    Raises:
        OnboardingServiceError: If update fails due to system errors

    Note:
        Only updates display_name during onboarding. Timezone is auto-detected
        by iOS and stored but not required as user input.
        Advances to 'gmail' step to prepare for Gmail connection.
    """
    await _ensure_onboarding_mutation_allowed(user_id, "update_profile_name")

    try:
        query = """
        UPDATE users
        SET
            display_name = %s,
            timezone = %s,
            onboarding_step = CASE
                WHEN onboarding_step IN ('start', 'gmail') THEN 'gmail'
                ELSE onboarding_step
            END,
            updated_at = NOW()
        WHERE
            id = %s
            AND is_active = true
        """

        # Use database pool helper function
        affected_rows = await execute_query(query, (display_name, timezone, user_id))

        # Check if any rows were updated
        if affected_rows == 0:
            logger.warning(
                "Profile update failed - user not found or inactive",
                user_id=user_id,
                current_step="unknown",
            )
            return None

        logger.info(
            "Profile name updated successfully",
            user_id=user_id,
            display_name=display_name,
            timezone=timezone,
            step_transition="start → gmail",
        )

        # Return updated user profile (domain model)
        return await get_user_profile(user_id)

    except OnboardingServiceError:
        raise
    except DatabaseError as e:
        logger.error("Database error updating profile name", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Database error updating profile: {e}", user_id=user_id
        ) from e
    except Exception as e:
        logger.error("Unexpected error updating profile name", user_id=user_id, error=str(e))
        raise OnboardingServiceError(f"Profile update failed: {e}", user_id=user_id) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def complete_onboarding(user_id: str) -> UserProfile | None:
    """
    Mark onboarding as completed and advance to 'completed' step.

    Args:
        user_id: UUID string of the user

    Returns:
        Updated UserProfile domain model if successful, None if failed

    Raises:
        OnboardingServiceError: If completion fails due to system errors

    Prerequisites:
        - User must be on 'vip_selection' step
        - User must have gmail_connected = true
        - User must have completed VIP selection

    Note:
        This function is called after VIP selection is saved.
        Email styles are optional (can be skipped).
    """
    try:
        # First, validate prerequisites with detailed logging
        profile = await get_user_profile(user_id)
        if not profile:
            logger.warning("Onboarding completion failed - user not found", user_id=user_id)
            raise OnboardingServiceError("User not found", user_id=user_id)

        # Allow idempotent completion calls once user already finished onboarding
        if profile.onboarding_step == "completed" and profile.onboarding_completed:
            logger.info(
                "Onboarding completion request ignored - already completed",
                user_id=user_id,
            )
            return profile

        # Validate current onboarding step
        if profile.onboarding_step != "vip_selection":
            logger.warning(
                "Onboarding completion failed - invalid step",
                user_id=user_id,
                current_step=profile.onboarding_step,
                required_step="vip_selection",
            )
            raise OnboardingServiceError(
                f"Invalid onboarding step: {profile.onboarding_step}", user_id=user_id
            )

        # Validate Gmail connection (CRITICAL REQUIREMENT)
        if not profile.gmail_connected:
            logger.warning(
                "Onboarding completion failed - Gmail not connected",
                user_id=user_id,
                gmail_connected=profile.gmail_connected,
                onboarding_step=profile.onboarding_step,
            )
            raise OnboardingServiceError("Gmail not connected", user_id=user_id)

        # Additional validation: Check if Gmail tokens actually exist
        gmail_connection_valid = await _validate_gmail_connection(user_id)
        if not gmail_connection_valid:
            logger.warning(
                "Onboarding completion failed - Gmail connection invalid (no tokens found)",
                user_id=user_id,
            )
            # Fix inconsistent state: user marked as connected but no tokens
            await _fix_gmail_connection_state(user_id)
            raise OnboardingServiceError("Gmail connection invalid", user_id=user_id)

        # Email styles are optional - user may have skipped this step
        # VIP selection is MANDATORY - validate user has selected VIPs
        vip_selection_valid = await _validate_vip_selection(user_id)
        if not vip_selection_valid:
            logger.warning(
                "Onboarding completion failed - VIP selection required",
                user_id=user_id,
            )
            raise OnboardingServiceError(
                "VIP selection is required to complete onboarding", user_id=user_id
            )

        # Check Calendar permissions from OAuth tokens
        calendar_connected = await _check_calendar_permissions(user_id)

        # All prerequisites met - proceed with completion
        query = """
        UPDATE users
        SET
            onboarding_completed = true,
            onboarding_step = 'completed',
            calendar_connected = %s,
            updated_at = NOW()
        WHERE
            id = %s
            AND onboarding_step = 'vip_selection'
            AND gmail_connected = true
            AND is_active = true
        """

        # Use database pool helper function
        affected_rows = await execute_query(query, (calendar_connected, user_id))

        # Check if any rows were updated
        if affected_rows == 0:
            logger.error(
                "Onboarding completion failed - database update failed despite validation",
                user_id=user_id,
            )
            raise OnboardingServiceError("Database update failed", user_id=user_id)

        skip_flag_cleared = await set_email_style_skipped(user_id, False)
        if not skip_flag_cleared:
            logger.warning("Failed to clear email style skip flag", user_id=user_id)

        logger.info(
            "Onboarding completed successfully",
            user_id=user_id,
            step_transition="vip_selection → completed",
            gmail_connected=True,
            calendar_connected=calendar_connected,
            email_style_skipped=profile.email_style_skipped,
        )

        # Return updated user profile (domain model)
        return await get_user_profile(user_id)

    except OnboardingServiceError:
        raise  # Re-raise onboarding service errors
    except DatabaseError as e:
        logger.error("Database error completing onboarding", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Database error completing onboarding: {e}", user_id=user_id
        ) from e
    except Exception as e:
        logger.error("Unexpected error completing onboarding", user_id=user_id, error=str(e))
        raise OnboardingServiceError(f"Onboarding completion failed: {e}", user_id=user_id) from e


async def skip_email_style_step(user_id: str) -> UserProfile | None:
    """
    Allow user to skip email style creation and advance to vip_selection.
    VIP selection remains mandatory even when email styles are skipped.
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            logger.warning("Email style skip failed - user not found", user_id=user_id)
            raise OnboardingServiceError("User not found", user_id=user_id)

        # Idempotent: if already completed, ensure skip flag is set and return profile
        if profile.onboarding_step == "completed" and profile.onboarding_completed:
            flag_updated = await set_email_style_skipped(user_id, True)
            if not flag_updated:
                logger.warning(
                    "Email style skip flag update failed for already-completed user",
                    user_id=user_id,
                )

            logger.info(
                "Email style skip request ignored - onboarding already completed",
                user_id=user_id,
            )
            return profile

        # Allow skipping from vip_selection step as well (in case user goes back)
        if profile.onboarding_step not in ("email_style", "vip_selection"):
            logger.warning(
                "Email style skip failed - invalid step",
                user_id=user_id,
                current_step=profile.onboarding_step,
            )
            raise OnboardingServiceError(
                f"Cannot skip from '{profile.onboarding_step}' step", user_id=user_id
            )

        if not profile.gmail_connected:
            logger.warning(
                "Email style skip failed - Gmail not connected",
                user_id=user_id,
            )
            raise OnboardingServiceError("Gmail not connected", user_id=user_id)

        gmail_connection_valid = await _validate_gmail_connection(user_id)
        if not gmail_connection_valid:
            logger.warning(
                "Email style skip failed - Gmail connection invalid",
                user_id=user_id,
            )
            await _fix_gmail_connection_state(user_id)
            raise OnboardingServiceError("Gmail connection invalid", user_id=user_id)

        affected_rows = await _persist_email_style_skip(user_id)
        if affected_rows == 0:
            logger.error(
                "Email style skip failed - database update returned 0 rows",
                user_id=user_id,
            )
            raise OnboardingServiceError("Failed to skip email style step", user_id=user_id)

        skip_flag_updated = await set_email_style_skipped(user_id, True)
        if not skip_flag_updated:
            logger.warning(
                "Email style skip flag update failed",
                user_id=user_id,
            )

        logger.info(
            "Email style step skipped - advancing to vip_selection",
            user_id=user_id,
            step_transition="email_style → vip_selection",
            gmail_connected=True,
            email_style_skipped=True,
        )

        return await get_user_profile(user_id)

    except OnboardingServiceError:
        raise
    except Exception as e:
        logger.error("Unexpected error skipping email style step", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Failed to skip email style step: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def _persist_email_style_skip(user_id: str) -> int:
    """Advance to vip_selection when skipping email styles."""
    query = """
    UPDATE users
    SET
        onboarding_step = 'vip_selection',
        updated_at = NOW()
    WHERE
        id = %s
        AND onboarding_step = 'email_style'
        AND is_active = true
    """

    return await execute_query(query, (user_id,))


@with_db_retry(max_retries=3, base_delay=0.1)
async def _validate_gmail_connection(user_id: str) -> bool:
    """
    Validate that Gmail connection actually exists (tokens in database).

    Args:
        user_id: UUID string of the user

    Returns:
        bool: True if Gmail tokens exist, False otherwise

    Raises:
        OnboardingServiceError: If validation fails due to system errors
    """
    try:
        query = "SELECT 1 FROM oauth_tokens WHERE user_id = %s AND provider = 'google'"

        # Use database pool helper function
        row = await fetch_one(query, (user_id,))
        has_tokens = row is not None

        logger.debug("Gmail connection validation", user_id=user_id, has_tokens=has_tokens)

        return has_tokens

    except DatabaseError as e:
        logger.error("Database error validating Gmail connection", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Database error validating Gmail connection: {e}", user_id=user_id
        ) from e
    except Exception as e:
        logger.error("Error validating Gmail connection", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Gmail connection validation failed: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def _fix_gmail_connection_state(user_id: str) -> None:
    """
    Fix inconsistent state where user is marked as Gmail connected but has no tokens.

    Args:
        user_id: UUID string of the user

    Raises:
        OnboardingServiceError: If fix fails due to system errors
    """
    try:
        query = """
        UPDATE users
        SET gmail_connected = false, updated_at = NOW()
        WHERE id = %s
        """

        # Use database pool helper function
        await execute_query(query, (user_id,))

        logger.info(
            "Fixed Gmail connection state inconsistency",
            user_id=user_id,
            action="set_gmail_connected_false",
        )

    except DatabaseError as e:
        logger.error("Database error fixing Gmail connection state", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Database error fixing Gmail state: {e}", user_id=user_id
        ) from e
    except Exception as e:
        logger.error("Error fixing Gmail connection state", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Failed to fix Gmail connection state: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def _validate_vip_selection(user_id: str) -> bool:
    """
    Validate that user has selected at least one VIP.

    Args:
        user_id: UUID string of the user

    Returns:
        bool: True if user has selected VIPs, False otherwise

    Raises:
        OnboardingServiceError: If validation fails due to system errors
    """
    try:
        query = """
            SELECT COUNT(*) as vip_count
            FROM vip_list
            WHERE user_id = %s
        """

        row = await fetch_one(query, (user_id,))
        vip_count = row["vip_count"] if row else 0

        has_vips = vip_count > 0

        logger.debug(
            "VIP selection validation",
            user_id=user_id,
            vip_count=vip_count,
            has_vips=has_vips,
        )

        return has_vips

    except DatabaseError as e:
        logger.error("Database error validating VIP selection", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Database error validating VIP selection: {e}", user_id=user_id
        ) from e
    except Exception as e:
        logger.error("Error validating VIP selection", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"VIP selection validation failed: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def _check_calendar_permissions(user_id: str) -> bool:
    """
    Check if user has Calendar permissions from OAuth tokens.

    Args:
        user_id: UUID string of the user

    Returns:
        bool: True if Calendar permissions exist, False otherwise

    Raises:
        OnboardingServiceError: If check fails due to system errors
    """
    try:
        # Get OAuth tokens for the user
        from app.services.core.token_service import get_oauth_tokens

        oauth_tokens = await get_oauth_tokens(user_id)
        if not oauth_tokens:
            logger.debug("No OAuth tokens found for calendar permission check", user_id=user_id)
            return False

        # Check if tokens have calendar access
        has_calendar_access = oauth_tokens.has_calendar_access()

        logger.debug(
            "Calendar permission check completed",
            user_id=user_id,
            has_calendar_access=has_calendar_access,
            scope_preview=oauth_tokens.scope[:50] + "..." if oauth_tokens.scope else None,
        )

        return has_calendar_access

    except Exception as e:
        logger.error("Error checking calendar permissions", user_id=user_id, error=str(e))
        # Don't raise exception - just return False to allow onboarding completion
        # Calendar is optional, Gmail is required
        return False


async def get_onboarding_completion_requirements(user_id: str) -> dict:
    """
    Get detailed requirements for onboarding completion.

    Args:
        user_id: UUID string of the user

    Returns:
        dict: Detailed completion requirements and current status
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            return {"can_complete": False, "reason": "User not found", "requirements": {}}

        if profile.email_style_skipped and profile.onboarding_completed:
            logger.info(
                "Completion requirements already satisfied via skip",
                user_id=user_id,
            )
            return {
                "can_complete": True,
                "reason": None,
                "requirements": {
                    "email_style_skipped": {
                        "required": False,
                        "current": True,
                        "satisfied": True,
                    }
                },
                "user_profile": {
                    "onboarding_step": profile.onboarding_step,
                    "gmail_connected": profile.gmail_connected,
                    "onboarding_completed": profile.onboarding_completed,
                },
            }

        # Check each requirement
        requirements = {
            "correct_step": {
                "required": "vip_selection",
                "current": profile.onboarding_step,
                "satisfied": profile.onboarding_step == "vip_selection",
            },
            "gmail_connected": {
                "required": True,
                "current": profile.gmail_connected,
                "satisfied": profile.gmail_connected,
            },
            "gmail_tokens_exist": {
                "required": True,
                "current": None,
                "satisfied": False,
            },
            "vip_selection_completed": {
                "required": True,
                "current": None,
                "satisfied": False,
            },
        }

        # Validate Gmail tokens exist
        try:
            has_tokens = await _validate_gmail_connection(user_id)
            requirements["gmail_tokens_exist"]["current"] = has_tokens
            requirements["gmail_tokens_exist"]["satisfied"] = has_tokens
        except OnboardingServiceError:
            requirements["gmail_tokens_exist"]["current"] = False
            requirements["gmail_tokens_exist"]["satisfied"] = False

        # Validate VIP selection completed (MANDATORY)
        try:
            has_vips = await _validate_vip_selection(user_id)
            requirements["vip_selection_completed"]["current"] = has_vips
            requirements["vip_selection_completed"]["satisfied"] = has_vips
        except OnboardingServiceError:
            requirements["vip_selection_completed"]["current"] = False
            requirements["vip_selection_completed"]["satisfied"] = False

        # Email styles are optional (can be skipped), so no validation needed

        # Overall completion eligibility
        can_complete = all(req["satisfied"] for req in requirements.values())

        # Determine blocking reason if any
        blocking_reason = None
        if not can_complete:
            if not requirements["correct_step"]["satisfied"]:
                blocking_reason = f"Must be on 'vip_selection' onboarding step (currently on '{profile.onboarding_step}')"
            elif not requirements["gmail_connected"]["satisfied"]:
                blocking_reason = "Gmail account must be connected"
            elif not requirements["gmail_tokens_exist"]["satisfied"]:
                blocking_reason = "Gmail connection is invalid - please reconnect Gmail"
            elif not requirements["vip_selection_completed"]["satisfied"]:
                blocking_reason = "VIP selection must be completed (select 1-20 important contacts)"

        return {
            "can_complete": can_complete,
            "reason": blocking_reason,
            "requirements": requirements,
            "user_profile": {
                "onboarding_step": profile.onboarding_step,
                "gmail_connected": profile.gmail_connected,
                "onboarding_completed": profile.onboarding_completed,
            },
        }

    except Exception as e:
        logger.error(
            "Error checking onboarding completion requirements", user_id=user_id, error=str(e)
        )
        return {
            "can_complete": False,
            "reason": f"Error checking requirements: {str(e)}",
            "requirements": {},
        }


async def validate_onboarding_transition(user_id: str, target_step: str) -> bool:
    """
    Validate if a user can transition to the target onboarding step.

    Args:
        user_id: UUID string of the user
        target_step: The step to transition to

    Returns:
        True if transition is valid, False otherwise

    Note:
        Helper function for validation logic. Can be used by API endpoints
        for additional validation before calling update functions.
        Includes Gmail connection validation and all 3 Email Styles validation.
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            logger.warning("Cannot validate transition - user not found", user_id=user_id)
            return False

        current_step = profile.onboarding_step

        # Valid transitions with email_style and vip_selection steps
        valid_transitions = {
            "start": ["gmail"],
            "gmail": ["email_style"],
            "email_style": ["vip_selection"],
            "vip_selection": ["completed"],
            "completed": [],
        }

        is_valid = target_step in valid_transitions.get(current_step, [])

        # Additional validation for completion step
        if target_step == "completed" and is_valid:
            # Check Gmail connection + All 3 Email Styles requirements
            requirements = await get_onboarding_completion_requirements(user_id)
            is_valid = requirements["can_complete"]

            if not is_valid:
                logger.warning(
                    "Onboarding transition to 'completed' blocked by requirements",
                    user_id=user_id,
                    blocking_reason=requirements["reason"],
                    current_step=current_step,
                    requirements_status=requirements.get("requirements", {}),
                )

        # Additional validation for email_style step
        if target_step == "email_style" and is_valid:
            # Ensure Gmail is properly connected before allowing email_style step
            if not profile.gmail_connected:
                logger.warning(
                    "Onboarding transition to 'email_style' blocked - Gmail not connected",
                    user_id=user_id,
                    current_step=current_step,
                    gmail_connected=profile.gmail_connected,
                )
                is_valid = False

            # Double-check Gmail tokens exist (prevent inconsistent state)
            if is_valid:
                try:
                    gmail_tokens_valid = await _validate_gmail_connection(user_id)
                    if not gmail_tokens_valid:
                        logger.warning(
                            "Onboarding transition to 'email_style' blocked - Gmail tokens invalid",
                            user_id=user_id,
                            current_step=current_step,
                        )
                        is_valid = False
                except Exception as token_error:
                    logger.error(
                        "Error validating Gmail tokens for email_style transition",
                        user_id=user_id,
                        error=str(token_error),
                    )
                    is_valid = False

        if not is_valid:
            logger.warning(
                "Invalid onboarding transition attempted",
                user_id=user_id,
                current_step=current_step,
                target_step=target_step,
                valid_options=valid_transitions.get(current_step, []),
                gmail_connected=profile.gmail_connected if profile else None,
            )
        else:
            logger.debug(
                "Onboarding transition validated",
                user_id=user_id,
                current_step=current_step,
                target_step=target_step,
            )

        return is_valid

    except Exception as e:
        logger.error(
            "Error validating onboarding transition",
            user_id=user_id,
            target_step=target_step,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


async def handle_gmail_connection_failure(user_id: str, error_details: str) -> dict:
    """
    Handle Gmail connection failure during onboarding.

    Args:
        user_id: UUID string of the user
        error_details: Details about the connection failure

    Returns:
        dict: Recovery instructions and next steps
    """
    try:
        logger.warning(
            "Handling Gmail connection failure during onboarding",
            user_id=user_id,
            error_details=error_details,
        )

        profile = await get_user_profile(user_id)
        if not profile:
            return {"success": False, "message": "User not found", "next_steps": []}

        # Provide appropriate guidance based on current state
        if profile.onboarding_step == "gmail":
            return {
                "success": True,
                "message": "Gmail connection failed, but you can try again.",
                "next_steps": [
                    "Tap 'Connect Gmail' to retry the connection",
                    "Make sure you grant all required permissions",
                    "Check your internet connection",
                    "Contact support if the problem persists",
                ],
                "can_retry": True,
                "current_step": "gmail",
            }
        else:
            return {
                "success": True,
                "message": "Please complete your profile setup first.",
                "next_steps": [
                    "Complete your profile information",
                    "Then try connecting Gmail again",
                ],
                "can_retry": False,
                "current_step": profile.onboarding_step,
            }

    except Exception as e:
        logger.error("Error handling Gmail connection failure", user_id=user_id, error=str(e))
        return {
            "success": False,
            "message": "An error occurred while handling the connection failure",
            "next_steps": ["Please try again later or contact support"],
        }


# Email style step management functions
@with_db_retry(max_retries=3, base_delay=0.1)
async def advance_to_email_style_step(user_id: str) -> UserProfile | None:
    """
    Advance user to email_style step after Gmail connection.
    Called automatically when Gmail OAuth completes successfully.
    """
    await _ensure_onboarding_mutation_allowed(user_id, "advance_to_email_style_step")

    try:
        query = """
        UPDATE users
        SET
            onboarding_step = 'email_style',
            updated_at = NOW()
        WHERE
            id = %s
            AND onboarding_step = 'gmail'
            AND gmail_connected = true
            AND is_active = true
        """

        affected_rows = await execute_query(query, (user_id,))

        if affected_rows == 0:
            logger.warning("Cannot advance to email_style - user not ready", user_id=user_id)
            return None

        logger.info(
            "Advanced to email_style step", user_id=user_id, step_transition="gmail → email_style"
        )

        return await get_user_profile(user_id)

    except OnboardingServiceError:
        raise
    except Exception as e:
        logger.error("Error advancing to email_style step", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Failed to advance to email_style: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def advance_to_vip_selection_step(user_id: str) -> UserProfile | None:
    """
    Advance user to vip_selection step after email style completion.
    Called after email styles are created or skipped.
    """
    await _ensure_onboarding_mutation_allowed(user_id, "advance_to_vip_selection_step")

    try:
        query = """
        UPDATE users
        SET
            onboarding_step = 'vip_selection',
            updated_at = NOW()
        WHERE
            id = %s
            AND onboarding_step = 'email_style'
            AND gmail_connected = true
            AND is_active = true
        """

        affected_rows = await execute_query(query, (user_id,))

        if affected_rows == 0:
            logger.warning("Cannot advance to vip_selection - user not ready", user_id=user_id)
            return None

        logger.info(
            "Advanced to vip_selection step",
            user_id=user_id,
            step_transition="email_style → vip_selection",
        )

        return await get_user_profile(user_id)

    except OnboardingServiceError:
        raise
    except Exception as e:
        logger.error("Error advancing to vip_selection step", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Failed to advance to vip_selection: {e}", user_id=user_id
        ) from e


@with_db_retry(max_retries=3, base_delay=0.1)
async def complete_email_style_selection(
    user_id: str, style_type: str, style_profiles: dict[str, Any]
) -> UserProfile | None:
    """
    Complete email style selection - validates all 3 profiles exist.
    Called after successful 3-profile creation.

    Args:
        user_id: UUID string of the user
        style_type: "custom" (always custom for 3-profile system)
        style_profiles: All 3 profiles {"professional": {...}, "casual": {...}, "friendly": {...}}
    """
    try:
        # Validate all 3 profiles exist
        required_styles = ["professional", "casual", "friendly"]
        for style in required_styles:
            if style not in style_profiles:
                logger.error(
                    f"Missing {style} profile in email style selection",
                    user_id=user_id,
                    provided_styles=list(style_profiles.keys()),
                )
                raise OnboardingServiceError(f"Missing {style} profile", user_id=user_id)

        logger.info(
            "Email style selection completed - all 3 profiles created",
            user_id=user_id,
            style_type=style_type,
            profiles=list(style_profiles.keys()),
            ready_for_completion=True,
        )

        return await get_user_profile(user_id)

    except Exception as e:
        logger.error("Error completing email style selection", user_id=user_id, error=str(e))
        raise OnboardingServiceError(
            f"Failed to complete email style selection: {e}", user_id=user_id
        ) from e


async def get_email_style_step_status(user_id: str) -> dict[str, Any]:
    """
    Get current 3-profile email style step status for a user.

    Returns:
        dict with:
        - current_step: "email_style"
        - styles_created: {"professional": bool, "casual": bool, "friendly": bool}
        - all_styles_complete: bool
        - can_advance: bool
        - rate_limit_info: dict or None
    """
    try:
        # Get user profile
        profile = await get_user_profile(user_id)
        if not profile:
            return {"error": "User not found"}

        # Allow users who already completed onboarding to fetch their final styles
        allowed_email_style_steps = {"email_style", "completed"}
        if profile.onboarding_step not in allowed_email_style_steps:
            return {
                "error": f"User not on email_style step (currently on {profile.onboarding_step})",
                "current_step": profile.onboarding_step,
            }

        # Get 3-profile status
        from app.services.email_style.service import get_email_style_selection_options

        options_data = await get_email_style_selection_options(user_id)

        return {
            "current_step": profile.onboarding_step,
            "styles_created": options_data["styles_created"],
            "all_styles_complete": options_data["all_styles_complete"],
            "can_advance": options_data["can_advance"],
            "rate_limit_info": options_data.get("rate_limit_info"),
        }

    except Exception as e:
        logger.error("Error getting email style step status", user_id=user_id, error=str(e))
        return {"error": f"Failed to get email style status: {e}"}


async def _ensure_onboarding_mutation_allowed(user_id: str, action: str) -> None:
    """
    Guardrail to prevent onboarding transitions once a user has completed onboarding.
    """
    try:
        profile = await get_user_profile(user_id)
    except Exception:
        # If profile lookup fails, allow caller to handle via existing error paths
        return

    if profile and profile.onboarding_step == "completed":
        logger.error(
            "Blocked onboarding mutation on completed user",
            user_id=user_id,
            action=action,
        )
        raise OnboardingServiceError(
            "Onboarding already completed", user_id=user_id, recoverable=False
        )
