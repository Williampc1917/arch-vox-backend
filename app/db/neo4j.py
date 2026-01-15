"""
Neo4j driver manager for Aura connectivity.

Owns driver lifecycle and exposes session/health helpers for services.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from neo4j import AsyncGraphDatabase, basic_auth

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class Neo4jDriverManager:
    """Manage a shared Neo4j driver instance for async usage."""

    def __init__(self) -> None:
        self._driver = None
        self._initialized = False
        self._closed = False

    async def initialize(self) -> None:
        """Initialize the Neo4j driver and verify connectivity."""
        if self._initialized:
            return
        if self._closed:
            raise RuntimeError("Cannot reinitialize closed Neo4j driver")
        if not settings.NEO4J_URI or not settings.NEO4J_PASSWORD:
            raise RuntimeError("Neo4j config missing: set NEO4J_URI and NEO4J_PASSWORD")

        logger.info("Initializing Neo4j driver", uri=settings.NEO4J_URI)

        self._driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=basic_auth(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
        )

        await self._driver.verify_connectivity()
        self._initialized = True

        logger.info(
            "Neo4j driver initialized",
            database=settings.NEO4J_DATABASE,
        )

    async def close(self) -> None:
        """Close the driver cleanly."""
        if not self._initialized or self._closed:
            return

        try:
            if self._driver:
                await self._driver.close()
        finally:
            self._initialized = False
            self._closed = True
            logger.info("Neo4j driver closed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Any, None]:
        """Provide a Neo4j session bound to the configured database."""
        if not self._initialized:
            raise RuntimeError("Neo4j driver not initialized")
        if self._closed:
            raise RuntimeError("Neo4j driver is closed")

        async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
            yield session

    async def health_check(self) -> dict[str, Any]:
        """Return Neo4j driver health status."""
        if not self._initialized:
            return {
                "healthy": False,
                "service": "neo4j",
                "error": "Driver not initialized",
            }
        if self._closed:
            return {
                "healthy": False,
                "service": "neo4j",
                "error": "Driver is closed",
            }

        try:
            await self._driver.verify_connectivity()
            return {
                "healthy": True,
                "service": "neo4j",
                "database": settings.NEO4J_DATABASE,
            }
        except Exception as exc:
            return {
                "healthy": False,
                "service": "neo4j",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }


neo4j_driver = Neo4jDriverManager()


@asynccontextmanager
async def get_neo4j_session() -> AsyncGenerator[Any, None]:
    """Convenience helper for retrieving a Neo4j session."""
    async with neo4j_driver.session() as session:
        yield session


async def neo4j_health_check() -> dict[str, Any]:
    """Convenience wrapper for Neo4j health checks."""
    return await neo4j_driver.health_check()
