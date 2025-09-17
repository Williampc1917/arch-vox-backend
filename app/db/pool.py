# app/db/pool.py
# app/db/pool.py
"""
PostgreSQL connection pool manager using psycopg_pool.
Optimized for Supabase free tier (60 connection limit).
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

import psycopg
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

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
                **pool_config
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
                timeout=pool_config["timeout"]
            )
            
        except Exception as e:
            logger.error("Failed to initialize database pool", error=str(e))
            # Clean up on failure
            self._initialized = False  # Reset state
            if self.pool:
                try:
                    await self.pool.close()
                except:
                    pass  # Ignore errors during cleanup
                self.pool = None
            raise RuntimeError(f"Database pool initialization failed: {e}") from e
    
    def _get_pool_config(self) -> dict[str, Any]:
        """Get pool configuration optimized for environment."""
        # Base configuration for Supabase free tier
        config = {
            "min_size": 3,          # Always keep 3 connections warm
            "max_size": 12,         # Max 12 connections (20% of free tier limit)
            "timeout": 30.0,        # Wait 30s for connection
            "max_idle": 600.0,      # Close idle connections after 10 minutes
            "max_lifetime": 3600.0, # Recycle connections after 1 hour
            "check": AsyncConnectionPool.check_connection,  # Health check function
            "configure": self._configure_connection,  # Connection setup
        }
        
        # Adjust for environment
        if settings.environment == "development":
            config.update({
                "min_size": 2,
                "max_size": 5,
                "timeout": 10.0,
            })
        elif settings.environment == "production":
            config.update({
                "min_size": 5,
                "max_size": 15,  # More aggressive for production
                "timeout": 60.0,
            })
        
        return config
    
    async def _configure_connection(self, conn: psycopg.AsyncConnection) -> None:
        """Configure each new connection from the pool."""
        try:
            # IMPORTANT: Use a transaction to ensure connection state is clean
            async with conn.transaction():
                # Set connection to use dict rows for easier data handling
                conn.row_factory = dict_row
                
                # Set application name for monitoring (FIX: Use %s not $1)
                await conn.execute(
                    "SET application_name = %s", 
                    (f"voice-gmail-{settings.environment}",)
                )
                
                # Optimize connection settings (FIX: Use proper SQL strings)
                await conn.execute("SET timezone = 'UTC'")
                await conn.execute("SET statement_timeout = '60s'")
            
            # After transaction completes, connection should be in clean state
            logger.debug("Database connection configured successfully")
            
        except Exception as e:
            logger.error("Failed to configure database connection", error=str(e))
            # Don't re-raise - let the pool handle the failed connection
            # The pool will discard this connection and try again
    
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
            
        except asyncio.TimeoutError:
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
                    "service": "database_pool"
                }
            
            if self._closed:
                return {
                    "healthy": False,
                    "error": "Pool is closed",
                    "service": "database_pool"
                }
            
            # Get pool statistics
            stats = self.pool.get_stats()
            
            # Test connection with timing
            import time
            start_time = time.time()
            
            async with self.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
            
            connection_time_ms = (time.time() - start_time) * 1000
            
            # Calculate health metrics
            pool_utilization = (stats.pool_size - stats.pool_available) / stats.pool_size * 100
            is_healthy = (
                pool_utilization < 90 and  # Pool not overwhelmed
                connection_time_ms < 100 and  # Fast connection acquisition
                stats.pool_size >= self._get_pool_config()["min_size"]  # Minimum connections available
            )
            
            health_data = {
                "healthy": is_healthy,
                "service": "database_pool",
                "connection_time_ms": round(connection_time_ms, 2),
                "pool_stats": {
                    "pool_size": stats.pool_size,
                    "pool_available": stats.pool_available,
                    "pool_utilization_percent": round(pool_utilization, 2),
                    "requests_waiting": stats.requests_waiting,
                    "requests_num": stats.requests_num,
                    "requests_queued": stats.requests_queued,
                    "requests_errors": stats.requests_errors,
                    "connections_num": stats.connections_num,
                },
                "pool_config": {
                    "min_size": self._get_pool_config()["min_size"],
                    "max_size": self._get_pool_config()["max_size"],
                    "timeout": self._get_pool_config()["timeout"],
                },
            }
            
            # Add warnings for concerning metrics
            warnings = []
            if pool_utilization > 80:
                warnings.append(f"High pool utilization: {pool_utilization:.1f}%")
            if connection_time_ms > 50:
                warnings.append(f"Slow connection acquisition: {connection_time_ms:.1f}ms")
            if stats.requests_waiting > 0:
                warnings.append(f"Requests waiting for connections: {stats.requests_waiting}")
            
            if warnings:
                health_data["warnings"] = warnings
            
            return health_data
            
        except Exception as e:
            logger.error("Database pool health check failed", error=str(e))
            return {
                "healthy": False,
                "service": "database_pool",
                "error": str(e),
                "error_type": type(e).__name__
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