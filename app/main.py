# Updated app/main.py
# Updated app/main.py
"""
Updated main.py with database pool lifecycle management.
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import settings
from app.db.pool import db_pool  # Import the pool manager
from app.infrastructure.observability.logging import get_logger, setup_logging
from app.routes import calendar, gmail_auth, health, onboarding, protected
from app.services.redis_client import fast_redis

# Setup logging before creating the app
setup_logging(log_level="INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown with proper resource management."""

    # Startup sequence
    logger.info("Application starting", environment=settings.environment, debug=settings.debug)

    startup_tasks = []

    try:
        # Initialize database pool first
        logger.info("Initializing database pool")
        await db_pool.initialize()
        startup_tasks.append("database_pool")

        # Initialize Redis second
        logger.info("Initializing Redis connection")
        await fast_redis.initialize()
        startup_tasks.append("redis")

        logger.info("All services initialized successfully", services=startup_tasks)

    except Exception as e:
        logger.error("Failed to initialize services", error=str(e), completed_tasks=startup_tasks)

        # Clean up any successfully initialized services in reverse order
        if "redis" in startup_tasks:
            try:
                await fast_redis.close()
            except Exception as cleanup_error:
                logger.error("Error cleaning up Redis", error=str(cleanup_error))

        if "database_pool" in startup_tasks:
            try:
                await db_pool.close()
            except Exception as cleanup_error:
                logger.error("Error cleaning up database pool", error=str(cleanup_error))

        raise

    yield

    # Shutdown sequence (reverse order)
    logger.info("Application shutting down")

    shutdown_errors = []

    # Close Redis first (faster)
    try:
        logger.info("Closing Redis connection")
        await fast_redis.close()
    except Exception as e:
        logger.error("Error closing Redis", error=str(e))
        shutdown_errors.append(f"Redis: {e}")

    # Close database pool last (may have active connections)
    try:
        logger.info("Closing database pool")
        await db_pool.close()
    except Exception as e:
        logger.error("Error closing database pool", error=str(e))
        shutdown_errors.append(f"Database: {e}")

    if shutdown_errors:
        logger.warning("Some services had shutdown errors", errors=shutdown_errors)
    else:
        logger.info("All services closed successfully")


app = FastAPI(
    title="Voice Gmail Assistant",
    description="Voice-first Gmail assistant with connection pooling",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(health.router)
app.include_router(protected.router)
app.include_router(onboarding.router)
app.include_router(gmail_auth.router)
app.include_router(calendar.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log HTTP requests with timing."""
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000

    logger.info(
        "HTTP request completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(process_time, 2),
    )
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
