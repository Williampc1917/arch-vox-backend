"""
Onboarding service for managing user onboarding flow.
Handles onboarding status, profile updates, and completion.

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
                "Onboarding status retrieved", user_id=user_id, step=profile.onboarding_step
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
    Update user's display name and advance onboarding to 'profile' step.

    Args:
        user_id: UUID string of the user
        display_name: User's display name from input
        timezone: Auto-detected timezone from iOS (optional, defaults to UTC)

    Returns:
        Updated UserProfile domain model if successful, None if failed

    Note:
        Only updates display_name during onboarding. Timezone is auto-detected
        by iOS and stored but not required as user input.
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
                    step_transition="start → profile",
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
        - User must have gmail_connected = true
    """
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
                    logger.warning(
                        "Onboarding completion failed - user not in 'gmail' step or Gmail not connected",
                        user_id=user_id,
                    )
                    return None

                logger.info(
                    "Onboarding completed successfully",
                    user_id=user_id,
                    step_transition="gmail → completed",
                )

                # Return updated user profile (domain model)
                return await get_user_profile(user_id)

    except psycopg.Error as e:
        logger.error("Database error completing onboarding", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error completing onboarding", user_id=user_id, error=str(e))
        return None


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
    """
    try:
        profile = await get_user_profile(user_id)
        if not profile:
            logger.warning("Cannot validate transition - user not found", user_id=user_id)
            return False

        current_step = profile.onboarding_step

        # Valid transitions for simplified 2-step onboarding
        valid_transitions = {
            "start": ["profile"],  # Can update name
            "profile": ["gmail"],  # Can connect Gmail (Phase 3)
            "gmail": ["completed"],  # Can complete onboarding
            "completed": [],  # No further transitions
        }

        is_valid = target_step in valid_transitions.get(current_step, [])

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
