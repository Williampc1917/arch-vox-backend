"""
Onboarding service for managing user onboarding flow.
Handles onboarding status, profile updates, and completion with Gmail integration.

Service layer returns domain models only - API layer handles HTTP concerns.
"""

import psycopg

from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.models.domain.user_domain import UserProfile
from app.services.user_service import get_user_profile

logger = get_logger(__name__)


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

    Note:
        Only updates display_name during onboarding. Timezone is auto-detected
        by iOS and stored but not required as user input.
        Advances to 'gmail' step to prepare for Gmail connection.
    """
    query = """
    UPDATE users
    SET
        display_name = %s,
        timezone = %s,
        onboarding_step = 'gmail',
        updated_at = NOW()
    WHERE
        id = %s
        AND onboarding_step = 'start'
        AND is_active = true
    """

    try:
        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                # Execute the update
                cur.execute(query, (display_name, timezone, user_id))

                # Check if any rows were updated
                if cur.rowcount == 0:
                    logger.warning(
                        "Profile update failed - user not found or not in 'start' step",
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

    except psycopg.Error as e:
        logger.error("Database error updating profile name", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error updating profile name", user_id=user_id, error=str(e))
        return None


async def complete_onboarding(user_id: str) -> UserProfile | None:
    """
    Mark onboarding as completed and advance to 'completed' step.

    Args:
        user_id: UUID string of the user

    Returns:
        Updated UserProfile domain model if successful, None if failed

    Prerequisites:
        - User must be on 'gmail' step
        - User must have gmail_connected = true (ENFORCED via Gmail OAuth integration)

    Note:
        This function now strictly validates Gmail connection before allowing
        onboarding completion, ensuring users have connected Gmail successfully.
    """
    # First, validate prerequisites with detailed logging
    profile = await get_user_profile(user_id)
    if not profile:
        logger.warning("Onboarding completion failed - user not found", user_id=user_id)
        return None

    # Validate current onboarding step
    if profile.onboarding_step != "gmail":
        logger.warning(
            "Onboarding completion failed - invalid step",
            user_id=user_id,
            current_step=profile.onboarding_step,
            required_step="gmail",
        )
        return None

    # Validate Gmail connection (CRITICAL REQUIREMENT)
    if not profile.gmail_connected:
        logger.warning(
            "Onboarding completion failed - Gmail not connected",
            user_id=user_id,
            gmail_connected=profile.gmail_connected,
            onboarding_step=profile.onboarding_step,
        )
        return None

    # Additional validation: Check if Gmail tokens actually exist
    gmail_connection_valid = await _validate_gmail_connection(user_id)
    if not gmail_connection_valid:
        logger.warning(
            "Onboarding completion failed - Gmail connection invalid (no tokens found)",
            user_id=user_id,
        )
        # Fix inconsistent state: user marked as connected but no tokens
        await _fix_gmail_connection_state(user_id)
        return None

    # All prerequisites met - proceed with completion
    query = """
    UPDATE users
    SET
        onboarding_completed = true,
        onboarding_step = 'completed',
        updated_at = NOW()
    WHERE
        id = %s
        AND onboarding_step = 'gmail'
        AND gmail_connected = true
        AND is_active = true
    """

    try:
        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                # Execute the update
                cur.execute(query, (user_id,))

                # Check if any rows were updated
                if cur.rowcount == 0:
                    logger.error(
                        "Onboarding completion failed - database update failed despite validation",
                        user_id=user_id,
                    )
                    return None

                logger.info(
                    "Onboarding completed successfully",
                    user_id=user_id,
                    step_transition="gmail → completed",
                    gmail_connected=True,
                )

                # Return updated user profile (domain model)
                return await get_user_profile(user_id)

    except psycopg.Error as e:
        logger.error("Database error completing onboarding", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error completing onboarding", user_id=user_id, error=str(e))
        return None


async def _validate_gmail_connection(user_id: str) -> bool:
    """
    Validate that Gmail connection actually exists (tokens in database).

    Args:
        user_id: UUID string of the user

    Returns:
        bool: True if Gmail tokens exist, False otherwise
    """
    try:
        query = "SELECT 1 FROM oauth_tokens WHERE user_id = %s AND provider = 'google'"

        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))
                has_tokens = cur.fetchone() is not None

                logger.debug("Gmail connection validation", user_id=user_id, has_tokens=has_tokens)

                return has_tokens

    except Exception as e:
        logger.error("Error validating Gmail connection", user_id=user_id, error=str(e))
        return False


async def _fix_gmail_connection_state(user_id: str) -> None:
    """
    Fix inconsistent state where user is marked as Gmail connected but has no tokens.

    Args:
        user_id: UUID string of the user
    """
    try:
        query = """
        UPDATE users
        SET gmail_connected = false, updated_at = NOW()
        WHERE id = %s
        """

        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))

                logger.info(
                    "Fixed Gmail connection state inconsistency",
                    user_id=user_id,
                    action="set_gmail_connected_false",
                )

    except Exception as e:
        logger.error("Error fixing Gmail connection state", user_id=user_id, error=str(e))


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

        # Check each requirement
        requirements = {
            "correct_step": {
                "required": "gmail",
                "current": profile.onboarding_step,
                "satisfied": profile.onboarding_step == "gmail",
            },
            "gmail_connected": {
                "required": True,
                "current": profile.gmail_connected,
                "satisfied": profile.gmail_connected,
            },
            "gmail_tokens_exist": {
                "required": True,
                "current": None,  # Will be checked below
                "satisfied": False,  # Will be updated below
            },
        }

        # Validate Gmail tokens exist
        has_tokens = await _validate_gmail_connection(user_id)
        requirements["gmail_tokens_exist"]["current"] = has_tokens
        requirements["gmail_tokens_exist"]["satisfied"] = has_tokens

        # Overall completion eligibility
        can_complete = all(req["satisfied"] for req in requirements.values())

        # Determine blocking reason if any
        blocking_reason = None
        if not can_complete:
            if not requirements["correct_step"]["satisfied"]:
                blocking_reason = (
                    f"Must be on 'gmail' onboarding step (currently on '{profile.onboarding_step}')"
                )
            elif not requirements["gmail_connected"]["satisfied"]:
                blocking_reason = "Gmail account must be connected"
            elif not requirements["gmail_tokens_exist"]["satisfied"]:
                blocking_reason = "Gmail connection is invalid - please reconnect Gmail"

        result = {
            "can_complete": can_complete,
            "reason": blocking_reason,
            "requirements": requirements,
            "user_profile": {
                "onboarding_step": profile.onboarding_step,
                "gmail_connected": profile.gmail_connected,
                "onboarding_completed": profile.onboarding_completed,
            },
        }

        logger.debug(
            "Onboarding completion requirements checked",
            user_id=user_id,
            can_complete=can_complete,
            blocking_reason=blocking_reason,
        )

        return result

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
        Now includes Gmail connection validation for completion.
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            logger.warning("Cannot validate transition - user not found", user_id=user_id)
            return False

        current_step = profile.onboarding_step

        # Valid transitions with Gmail integration
        valid_transitions = {
            "start": ["gmail"],  # Updated: skip 'profile', go directly to gmail after name update
            "gmail": ["completed"],  # Can complete only after Gmail connection
            "completed": [],  # No further transitions
        }

        is_valid = target_step in valid_transitions.get(current_step, [])

        # Additional validation for completion step
        if target_step == "completed" and is_valid:
            # Check Gmail connection requirements
            requirements = await get_onboarding_completion_requirements(user_id)
            is_valid = requirements["can_complete"]

            if not is_valid:
                logger.warning(
                    "Onboarding transition to 'completed' blocked by requirements",
                    user_id=user_id,
                    blocking_reason=requirements["reason"],
                )

        if not is_valid:
            logger.warning(
                "Invalid onboarding transition attempted",
                user_id=user_id,
                current_step=current_step,
                target_step=target_step,
                valid_options=valid_transitions.get(current_step, []),
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
        logger.error("Error validating onboarding transition", user_id=user_id, error=str(e))
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
