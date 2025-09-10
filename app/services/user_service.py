"""
User service for database operations.
Handles fetching user profile data from the database.
"""

import psycopg

from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.models.domain.user_domain import Plan, UserProfile

logger = get_logger(__name__)


async def get_user_profile(user_id: str) -> UserProfile | None:
    """
    Fetch complete user profile (user + settings + plan) from database.
    """
    query = """
    SELECT
        u.id, u.email, u.display_name, u.is_active,
        u.timezone, u.onboarding_completed, u.gmail_connected, u.onboarding_step,
        u.created_at, u.updated_at,
        us.voice_preferences,
        p.name as plan_name, p.max_daily_requests
    FROM users u
    LEFT JOIN user_settings us ON u.id = us.user_id
    LEFT JOIN user_subscriptions sub ON u.id = sub.user_id
    LEFT JOIN plans p ON sub.plan_name = p.name
    WHERE u.id = %s AND u.is_active = true
    """

    try:
        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))
                row = cur.fetchone()

                if not row:
                    logger.info("User not found or inactive", user_id=user_id)
                    return None

                # UPDATE unpacking to include new fields:
                (
                    id_val,
                    email,
                    display_name,
                    is_active,
                    timezone,  # NEW
                    onboarding_completed,  # NEW
                    gmail_connected,  # NEW
                    onboarding_step,  # NEW
                    created_at,
                    updated_at,
                    voice_preferences,
                    plan_name,
                    max_daily_requests,
                ) = row

                # Build domain objects
                plan = Plan(
                    name=plan_name or "free",
                    max_daily_requests=max_daily_requests or 100,
                )

                profile = UserProfile(
                    user_id=str(id_val),
                    email=email,
                    display_name=display_name,
                    is_active=is_active,
                    timezone=timezone,  # NEW
                    onboarding_completed=onboarding_completed,  # NEW
                    gmail_connected=gmail_connected,  # NEW
                    onboarding_step=onboarding_step,  # NEW
                    voice_preferences=voice_preferences
                    or {"tone": "professional", "speed": "normal"},
                    plan=plan,
                    created_at=created_at,
                    updated_at=updated_at,
                )

                logger.info("User profile retrieved", user_id=user_id, plan=plan.name)
                return profile

    except psycopg.Error as e:
        logger.error("Database error", user_id=user_id, error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error", user_id=user_id, error=str(e))
        return None
