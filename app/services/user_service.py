"""
User service for database operations.
Handles fetching user profile data from the database.
"""

import psycopg

from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.models.user import Plan, UserProfile

logger = get_logger(__name__)


async def get_user_profile(user_id: str) -> UserProfile | None:
    """
    Fetch complete user profile from database.

    Args:
        user_id: UUID string of the user

    Returns:
        UserProfile if found, None if not found or error
    """
    query = """
    SELECT
        u.id, u.email, u.display_name, u.is_active, u.created_at, u.updated_at,
        us.voice_preferences, us.updated_at as settings_updated_at,
        p.name as plan_name, p.max_daily_requests
    FROM users u
    LEFT JOIN user_settings us ON u.id = us.user_id
    LEFT JOIN user_subscriptions sub ON u.id = sub.user_id
    LEFT JOIN plans p ON sub.plan_name = p.name
    WHERE u.id = %s AND u.is_active = true
    """

    try:
        # Use autocommit for read-only operations
        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))
                row = cur.fetchone()

                if not row:
                    logger.info("User not found or inactive", user_id=user_id)
                    return None

                # Unpack row data
                (
                    id_val,
                    email,
                    display_name,
                    is_active,
                    created_at,
                    updated_at,
                    voice_preferences,
                    settings_updated_at,
                    plan_name,
                    max_daily_requests,
                ) = row

                # Create plan object
                plan = Plan(name=plan_name or "free", max_daily_requests=max_daily_requests or 100)

                # Create user profile
                profile = UserProfile(
                    user_id=str(id_val),
                    email=email,
                    display_name=display_name,
                    is_active=is_active,
                    voice_preferences=voice_preferences
                    or {"tone": "professional", "speed": "normal"},
                    plan=plan,
                    created_at=created_at,
                    updated_at=updated_at,
                )

                logger.info("User profile retrieved successfully", user_id=user_id, plan=plan_name)
                return profile

    except psycopg.Error as e:
        logger.error("Database error retrieving user profile", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error retrieving user profile", user_id=user_id, error=str(e))
        return None
