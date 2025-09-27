# app/db/helpers.py
# app/db/helpers.py
"""
Database helper functions for common patterns.
Reduces boilerplate in service layer.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from app.db.pool import get_db_connection, get_db_transaction
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class DatabaseError(Exception):
    """Custom exception for database operations."""

    def __init__(self, message: str, operation: str = "unknown", recoverable: bool = True):
        super().__init__(message)
        self.operation = operation
        self.recoverable = recoverable


async def fetch_one(
    query: str, params: tuple = (), *, connection: psycopg.AsyncConnection | None = None
) -> dict[str, Any] | None:
    """
    Execute query and return single row as dict.

    Args:
        query: SQL query with %s placeholders
        params: Query parameters
        connection: Optional existing connection

    Returns:
        Dict with row data or None if no results
    """
    try:
        if connection:
            async with connection.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
                return row if row else None
        else:
            async with await get_db_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    row = await cur.fetchone()
                    return row if row else None

    except psycopg.Error as e:
        logger.error("Database fetch_one error", query=query[:100], error=str(e))
        raise DatabaseError(f"Query failed: {e}", operation="fetch_one") from e


async def fetch_all(
    query: str, params: tuple = (), *, connection: psycopg.AsyncConnection | None = None
) -> list[dict[str, Any]]:
    """
    Execute query and return all rows as list of dicts.

    Args:
        query: SQL query with %s placeholders
        params: Query parameters
        connection: Optional existing connection

    Returns:
        List of dicts with row data
    """
    try:
        if connection:
            async with connection.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
                return rows
        else:
            async with await get_db_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    rows = await cur.fetchall()
                    return rows

    except psycopg.Error as e:
        logger.error("Database fetch_all error", query=query[:100], error=str(e))
        raise DatabaseError(f"Query failed: {e}", operation="fetch_all") from e


async def fetch_val(
    query: str, params: tuple = (), *, connection: psycopg.AsyncConnection | None = None
) -> Any:
    """
    Execute query and return single value.

    Args:
        query: SQL query with %s placeholders
        params: Query parameters
        connection: Optional existing connection

    Returns:
        Single value from first column of first row
    """
    try:
        if connection:
            async with connection.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
                return list(row.values())[0] if row else None
        else:
            async with await get_db_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    row = await cur.fetchone()
                    return list(row.values())[0] if row else None

    except psycopg.Error as e:
        logger.error("Database fetch_val error", query=query[:100], error=str(e))
        raise DatabaseError(f"Query failed: {e}", operation="fetch_val") from e


async def execute_query(
    query: str, params: tuple = (), *, connection: psycopg.AsyncConnection | None = None
) -> int:
    """
    Execute query and return number of affected rows.

    Args:
        query: SQL query with %s placeholders
        params: Query parameters
        connection: Optional existing connection

    Returns:
        Number of affected rows
    """
    try:
        if connection:
            cursor = await connection.execute(query, params)
            return cursor.rowcount
        else:
            async with await get_db_connection() as conn:
                cursor = await conn.execute(query, params)
                return cursor.rowcount

    except psycopg.Error as e:
        logger.error("Database execute error", query=query[:100], error=str(e))
        raise DatabaseError(f"Query failed: {e}", operation="execute") from e


async def execute_transaction(queries_and_params: list[tuple]) -> bool:
    """
    Execute multiple queries in a single transaction.

    Args:
        queries_and_params: List of (query, params) tuples

    Returns:
        True if all queries succeeded, False otherwise

    Example:
        success = await execute_transaction([
            ("DELETE FROM oauth_tokens WHERE user_id = %s", (user_id,)),
            ("UPDATE users SET gmail_connected = false WHERE id = %s", (user_id,))
        ])
    """
    try:
        async with await get_db_transaction() as conn:
            for query, params in queries_and_params:
                await conn.execute(query, params)

        logger.debug("Transaction completed successfully", query_count=len(queries_and_params))
        return True

    except psycopg.Error as e:
        logger.error("Transaction failed", query_count=len(queries_and_params), error=str(e))
        raise DatabaseError(f"Transaction failed: {e}", operation="transaction") from e


# Decorator for automatic retry on temporary failures
def with_db_retry(max_retries: int = 3, base_delay: float = 0.1):
    """
    Decorator to retry database operations on temporary failures.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries (exponential backoff)
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)

                except psycopg.OperationalError as e:
                    # Temporary failures - retry
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2**attempt)  # Exponential backoff
                        logger.warning(
                            "Database operation failed, retrying",
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay=delay,
                            error=str(e),
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "Database operation failed after all retries",
                            attempts=max_retries + 1,
                            error=str(e),
                        )
                        raise DatabaseError(
                            f"Operation failed after {max_retries} retries: {e}",
                            operation=func.__name__,
                            recoverable=False,
                        ) from e

                except (psycopg.IntegrityError, psycopg.DataError) as e:
                    # Permanent failures - don't retry
                    logger.error("Database operation failed with permanent error", error=str(e))
                    raise DatabaseError(
                        f"Permanent database error: {e}", operation=func.__name__, recoverable=False
                    ) from e

                except Exception as e:
                    # Unknown errors - don't retry
                    logger.error("Database operation failed with unknown error", error=str(e))
                    raise DatabaseError(
                        f"Unknown database error: {e}", operation=func.__name__, recoverable=False
                    ) from e

            # This shouldn't be reached, but just in case
            raise last_exception

        return wrapper

    return decorator


# Email Style Database Operations


async def get_user_plan_limits(user_id: str) -> dict[str, Any] | None:
    """
    Get user's plan limits including email extraction limits.

    Args:
        user_id: UUID string of the user

    Returns:
        dict with plan limits or None if user not found
    """
    query = """
    SELECT 
        p.name as plan_name,
        p.daily_minutes,
        p.daily_email_extractions
    FROM users u
    JOIN user_subscriptions us ON u.id = us.user_id
    JOIN plans p ON us.plan_name = p.name
    WHERE u.id = %s AND u.is_active = true
    """

    row = await fetch_one(query, (user_id,))

    if row:
        row_values = list(row.values())
        plan_name, daily_minutes, daily_email_extractions = row_values
        return {
            "plan_name": plan_name,
            "daily_minutes": float(daily_minutes) if daily_minutes else 0.0,
            "daily_email_extractions": daily_email_extractions or 0,
        }

    return None


async def get_daily_extraction_usage(user_id: str, usage_date: str = None) -> dict[str, Any]:
    """
    Get user's daily email extraction usage for specific date.

    Args:
        user_id: UUID string of the user
        usage_date: Date string (YYYY-MM-DD) or None for today

    Returns:
        dict with usage info (defaults to 0 if no record exists)
    """
    if usage_date is None:
        usage_date = datetime.now(UTC).date()

    query = """
    SELECT 
        email_extractions_used,
        updated_at
    FROM daily_usage
    WHERE user_id = %s AND usage_date = %s
    """

    row = await fetch_one(query, (user_id, usage_date))

    if row:
        row_values = list(row.values())
        extractions_used, updated_at = row_values
        return {
            "extractions_used": extractions_used or 0,
            "usage_date": str(usage_date),
            "last_updated": updated_at.isoformat() if updated_at else None,
        }

    # No record exists - return defaults
    return {"extractions_used": 0, "usage_date": str(usage_date), "last_updated": None}


async def increment_extraction_counter(user_id: str) -> bool:
    """
    Increment user's daily email extraction counter.
    Creates record if doesn't exist for today.

    Args:
        user_id: UUID string of the user

    Returns:
        bool: True if increment successful, False otherwise
    """
    try:
        query = """
        INSERT INTO daily_usage (
            user_id, usage_date, email_extractions_used, updated_at
        ) VALUES (
            %s, CURRENT_DATE, 1, NOW()
        )
        ON CONFLICT (user_id, usage_date) 
        DO UPDATE SET 
            email_extractions_used = daily_usage.email_extractions_used + 1,
            updated_at = NOW()
        """

        affected_rows = await execute_query(query, (user_id,))
        return affected_rows > 0

    except DatabaseError as e:
        logger.error(
            "Database error incrementing extraction counter", user_id=user_id, error=str(e)
        )
        return False
    except Exception as e:
        logger.error(
            "Unexpected error incrementing extraction counter", user_id=user_id, error=str(e)
        )
        return False


async def store_email_style_preferences(user_id: str, preferences: dict[str, Any]) -> bool:
    """
    Store user's email style preferences in user_settings table.

    Args:
        user_id: UUID string of the user
        preferences: Email style preferences dict

    Returns:
        bool: True if storage successful, False otherwise
    """
    try:
        import json

        query = """
        UPDATE user_settings 
        SET 
            email_style_preferences = %s,
            updated_at = NOW()
        WHERE user_id = %s
        """

        # Convert preferences to JSON string
        preferences_json = json.dumps(preferences)

        affected_rows = await execute_query(query, (preferences_json, user_id))
        return affected_rows > 0

    except DatabaseError as e:
        logger.error(
            "Database error storing email style preferences", user_id=user_id, error=str(e)
        )
        return False
    except Exception as e:
        logger.error(
            "Unexpected error storing email style preferences", user_id=user_id, error=str(e)
        )
        return False


async def get_email_style_preferences(user_id: str) -> dict[str, Any] | None:
    """
    Get user's current email style preferences from user_settings.

    Args:
        user_id: UUID string of the user

    Returns:
        dict with email style preferences or None if not found
    """
    try:
        query = """
        SELECT email_style_preferences
        FROM user_settings
        WHERE user_id = %s
        """

        row = await fetch_one(query, (user_id,))

        if row:
            preferences = list(row.values())[0]
            # preferences is already a dict from JSONB column
            return preferences if preferences else None

        return None

    except DatabaseError as e:
        logger.error(
            "Database error getting email style preferences", user_id=user_id, error=str(e)
        )
        return None
    except Exception as e:
        logger.error(
            "Unexpected error getting email style preferences", user_id=user_id, error=str(e)
        )
        return None


async def get_user_extraction_limit_status(user_id: str) -> dict[str, Any]:
    """
    Get complete rate limit status for user including plan limits and current usage.
    Combines plan limits with daily usage in single query for efficiency.

    Args:
        user_id: UUID string of the user

    Returns:
        dict with complete rate limit status
    """
    try:
        query = """
        SELECT 
            p.daily_email_extractions as daily_limit,
            p.name as plan_name,
            COALESCE(du.email_extractions_used, 0) as used_today,
            du.updated_at as last_extraction_at
        FROM users u
        JOIN user_subscriptions us ON u.id = us.user_id
        JOIN plans p ON us.plan_name = p.name
        LEFT JOIN daily_usage du ON u.id = du.user_id AND du.usage_date = CURRENT_DATE
        WHERE u.id = %s AND u.is_active = true
        """

        row = await fetch_one(query, (user_id,))

        if row:
            row_values = list(row.values())
            daily_limit, plan_name, used_today, last_extraction_at = row_values

            remaining = max(0, (daily_limit or 0) - (used_today or 0))
            can_extract = remaining > 0

            return {
                "can_extract": can_extract,
                "daily_limit": daily_limit or 0,
                "used_today": used_today or 0,
                "remaining": remaining,
                "plan_name": plan_name,
                "last_extraction_at": (
                    last_extraction_at.isoformat() if last_extraction_at else None
                ),
                "reset_time": datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1),
            }

        # User not found or no plan
        return {
            "can_extract": False,
            "daily_limit": 0,
            "used_today": 0,
            "remaining": 0,
            "plan_name": None,
            "last_extraction_at": None,
            "error": "User not found or no active plan",
        }

    except DatabaseError as e:
        logger.error(
            "Database error getting extraction limit status", user_id=user_id, error=str(e)
        )
        return {"can_extract": False, "error": f"Database error: {str(e)}"}
    except Exception as e:
        logger.error(
            "Unexpected error getting extraction limit status", user_id=user_id, error=str(e)
        )
        return {"can_extract": False, "error": f"Unexpected error: {str(e)}"}
