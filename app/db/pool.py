# app/db/pool.py
# app/db/pool.py
"""
PostgreSQL connection pool manager using psycopg_pool.
Optimized for Supabase free tier (60 connection limit).
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class DatabasePoolManager:
    """
    Production-ready database connection pool manager.

    Optimized for Supabase free tier with proper error handling,
    health monitoring, and graceful shutdown.
    """

    def __init__(self):
        self.pool: AsyncConnectionPool | None = None
        self._initialized = False
        self._closed = False

    async def initialize(self) -> None:
        """Initialize the connection pool on application startup."""
        if self._initialized:
            logger.warning("Database pool already initialized")
            return

        if self._closed:
            raise RuntimeError("Cannot reinitialize closed pool")

        try:
            logger.info("Initializing database connection pool")

            # Pool configuration optimized for Supabase free tier
            pool_config = self._get_pool_config()

            # Create the async connection pool (FIX: Use open=False to avoid deprecation warning)
            self.pool = AsyncConnectionPool(
                conninfo=settings.SUPABASE_DB_URL,
                open=False,  # Don't open in constructor - we'll open manually
                **pool_config,
            )

            # Open the pool manually (new recommended way)
            await self.pool.open()

            # Wait for pool to be ready
            await self.pool.wait()

            # Mark as initialized BEFORE testing connections (FIX: Avoid chicken-and-egg)
            self._initialized = True

            # Now test that connections work
            await self._test_pool_connections()

            logger.info(
                "Database pool initialized successfully",
                min_size=pool_config["min_size"],
                max_size=pool_config["max_size"],
                timeout=pool_config["timeout"],
            )

        except Exception as e:
            logger.error("Failed to initialize database pool", error=str(e))
            # Clean up on failure
            self._initialized = False  # Reset state
            if self.pool:
                try:
                    await self.pool.close()
                except Exception:
                    pass  # Ignore errors during cleanup
                self.pool = None
            raise RuntimeError(f"Database pool initialization failed: {e}") from e

    def _get_pool_config(self) -> dict[str, Any]:
        """
        Get database pool configuration from settings.
        Uses the centralized configuration from config.py with environment-specific adjustments.
        """
        # ✅ USE YOUR CONFIG.PY SETTINGS INSTEAD OF HARDCODED VALUES
        config = settings.get_db_pool_config()

        # Add psycopg-specific settings that aren't in your config
        config.update(
            {
                "check": AsyncConnectionPool.check_connection,  # Health check function
                "configure": self._configure_connection,  # Connection setup
            }
        )

        logger.debug(
            "Pool configuration loaded",
            min_size=config["min_size"],
            max_size=config["max_size"],
            timeout=config["timeout"],
            environment=settings.environment,
        )

        return config

    async def _configure_connection(self, conn: psycopg.AsyncConnection) -> None:
        """Configure each new connection from the pool."""
        try:
            # per-connection setting
            conn.row_factory = dict_row

            app_name = f"voice-gmail-{settings.environment}"

            # Enable autocommit to avoid leaving connections in INTRANS state
            # Use the async method for psycopg async connections
            await conn.set_autocommit(True)

            # Don't parameterize SET; inline safely with Literal
            await conn.execute(sql.SQL("SET application_name = {}").format(sql.Literal(app_name)))

            # These are fine as plain SQL literals
            await conn.execute("SET timezone = 'UTC'")
            await conn.execute("SET statement_timeout = '60s'")

            logger.debug("Database connection configured successfully")
        except Exception:
            # Preserve full traceback for debugging
            logger.exception("Failed to configure database connection")

    async def _test_pool_connections(self) -> None:
        """Test that pool connections work properly."""
        try:
            # Simple test query - don't use parameterized query to avoid syntax issues
            async with self.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    row = await cur.fetchone()
                    result = list(row.values())[0] if isinstance(row, dict) else row[0]
                if result != 1:
                    raise RuntimeError("Database connection test failed - got unexpected result")

            logger.debug("Database pool connection test passed")

        except Exception as e:
            logger.error("Database pool connection test failed", error=str(e))
            raise

    async def close(self) -> None:
        """Close the connection pool gracefully."""
        if not self._initialized or self._closed:
            return

        try:
            logger.info("Closing database connection pool")

            if self.pool:
                # Close the pool gracefully (max 30 seconds)
                await asyncio.wait_for(self.pool.close(), timeout=30.0)

            self._initialized = False
            self._closed = True

            logger.info("Database pool closed successfully")

        except TimeoutError:
            logger.warning("Database pool close timed out, forcing shutdown")
            # Pool should handle forced shutdown gracefully
        except Exception as e:
            logger.error("Error closing database pool", error=str(e))

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[psycopg.AsyncConnection, None]:
        """
        Get a connection from the pool.

        Usage:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    row = await cur.fetchone()
                    _ = list(row.values())[0] if isinstance(row, dict) else row[0]
        """
        if not self._initialized:
            raise RuntimeError("Database pool not initialized. Call initialize() first.")

        if self._closed:
            raise RuntimeError("Database pool is closed")

        try:
            async with self.pool.connection() as conn:
                yield conn

        except Exception as e:
            logger.error("Database connection error", error=str(e), error_type=type(e).__name__)
            raise

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[psycopg.AsyncConnection, None]:
        """
        Get a connection with automatic transaction management.

        Usage:
            async with db_pool.transaction() as conn:
                await conn.execute("INSERT ...")
                await conn.execute("UPDATE ...")
                # Automatic commit on success, rollback on exception
        """
        async with self.connection() as conn:
            async with conn.transaction():
                yield conn

    async def health_check(self) -> dict[str, Any]:
        """
        Comprehensive health check for the database pool.

        Returns:
            dict: Health status with metrics and diagnostics
        """
        try:
            if not self._initialized:
                return {
                    "healthy": False,
                    "error": "Pool not initialized",
                    "service": "database_pool",
                }

            if self._closed:
                return {"healthy": False, "error": "Pool is closed", "service": "database_pool"}

            # FIXED: Handle pool stats properly regardless of return type
            try:
                stats = self.pool.get_stats()

                # Handle both object and dict return types
                if hasattr(stats, "pool_size"):
                    # Stats is an object
                    pool_size = stats.pool_size
                    pool_available = stats.pool_available
                    requests_waiting = stats.requests_waiting
                    requests_num = stats.requests_num
                    requests_queued = getattr(stats, "requests_queued", 0)
                    requests_errors = getattr(stats, "requests_errors", 0)
                    connections_num = getattr(stats, "connections_num", 0)
                elif isinstance(stats, dict):
                    # Stats is a dict
                    pool_size = stats.get("pool_size", 0)
                    pool_available = stats.get("pool_available", 0)
                    requests_waiting = stats.get("requests_waiting", 0)
                    requests_num = stats.get("requests_num", 0)
                    requests_queued = stats.get("requests_queued", 0)
                    requests_errors = stats.get("requests_errors", 0)
                    connections_num = stats.get("connections_num", 0)
                else:
                    # Fallback if stats format is unexpected
                    return {
                        "healthy": False,
                        "error": f"Unexpected stats format: {type(stats)}",
                        "service": "database_pool",
                    }

            except Exception as stats_error:
                return {
                    "healthy": False,
                    "error": f"Failed to get pool stats: {stats_error}",
                    "service": "database_pool",
                }

            # Test connection with timing
            import time

            start_time = time.time()

            try:
                async with self.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1")
                        result = await cur.fetchone()

                        # Handle both dict and tuple results
                        if isinstance(result, dict):
                            test_value = list(result.values())[0]
                        else:
                            test_value = result[0] if result else None

                        if test_value != 1:
                            raise RuntimeError(
                                f"Database test failed - got {test_value} instead of 1"
                            )

            except Exception as conn_error:
                return {
                    "healthy": False,
                    "error": f"Connection test failed: {conn_error}",
                    "service": "database_pool",
                }

            connection_time_ms = (time.time() - start_time) * 1000

            # Calculate health metrics
            pool_utilization = (
                (pool_size - pool_available) / pool_size * 100 if pool_size > 0 else 0
            )

            # Get pool config for comparison
            pool_config = self._get_pool_config()
            min_size = pool_config.get("min_size", 0)
            max_size = pool_config.get("max_size", 0)
            timeout = pool_config.get("timeout", 0)

            is_healthy = (
                pool_utilization < 90  # Pool not overwhelmed
                and connection_time_ms < 100  # Fast connection acquisition
                and pool_size >= min_size  # Minimum connections available
            )

            health_data = {
                "healthy": is_healthy,
                "service": "database_pool",
                "connection_time_ms": round(connection_time_ms, 2),
                "pool_stats": {
                    "pool_size": pool_size,
                    "pool_available": pool_available,
                    "pool_utilization_percent": round(pool_utilization, 2),
                    "requests_waiting": requests_waiting,
                    "requests_num": requests_num,
                    "requests_queued": requests_queued,
                    "requests_errors": requests_errors,
                    "connections_num": connections_num,
                },
                "pool_config": {
                    "min_size": min_size,
                    "max_size": max_size,
                    "timeout": timeout,
                },
            }

            # Add warnings for concerning metrics
            warnings = []
            if pool_utilization > 80:
                warnings.append(f"High pool utilization: {pool_utilization:.1f}%")
            if connection_time_ms > 50:
                warnings.append(f"Slow connection acquisition: {connection_time_ms:.1f}ms")
            if requests_waiting > 0:
                warnings.append(f"Requests waiting for connections: {requests_waiting}")

            if warnings:
                health_data["warnings"] = warnings

            return health_data

        except Exception as e:
            logger.error("Database pool health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "database_pool",
                "error": str(e),
                "error_type": type(e).__name__,
            }


# Global pool instance
db_pool = DatabasePoolManager()


# Convenience functions for easy imports
async def get_db_connection():
    """Get database connection from pool."""
    return db_pool.connection()


async def get_db_transaction():
    """Get database connection with transaction."""
    return db_pool.transaction()


async def db_health_check() -> dict[str, Any]:
    """Get database pool health status."""
    return await db_pool.health_check()
