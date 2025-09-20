# app/db/helpers.py
# app/db/helpers.py
"""
Database helper functions for common patterns.
Reduces boilerplate in service layer.
"""

import asyncio
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
            cursor = await connection.execute(query, params)  # Fixed: removed *params
            return cursor.rowcount
        else:
            async with await get_db_connection() as conn:
                cursor = await conn.execute(query, params)  # Fixed: removed *params
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
                await conn.execute(query, params)  # Fixed: removed *params

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
