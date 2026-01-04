# Updated app/main.py
"""
Voice Gmail Assistant - Main application with audit logging and compliance.
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import settings
from app.db.pool import db_pool  # Import the pool manager
from app.infrastructure.observability.logging import get_logger, setup_logging
from app.middleware import RateLimitHeadersMiddleware, RequestContextMiddleware
from app.middleware.cors import CORSMiddleware
from app.middleware.https_enforcement import HTTPSEnforcementMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routes import (
    calendar,
    data_management,
    gmail,
    gmail_auth,
    health,
    onboarding,
    onboarding_vip,
    protected,
)
from app.services.redis_client import fast_redis

# Setup logging before creating the app
setup_logging(log_level="INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown with proper resource management."""

    # Startup sequence
    logger.info("Application starting", environment=settings.environment, debug=settings.debug)
    logger.info(
        "Email style Redis cache flag",
        email_style_redis_cache_enabled=settings.EMAIL_STYLE_REDIS_CACHE_ENABLED,
    )

    startup_tasks = []


    try:
        # Initialize database pool first
        logger.info("Initializing database pool")
        await db_pool.initialize()
        startup_tasks.append("database_pool")

        # Initialize Redis second
        logger.info("Initializing Redis connection")
        try:
            await fast_redis.initialize()
            startup_tasks.append("redis")
        except Exception as e:
            if settings.environment != "production":
                logger.warning(
                    "Redis unavailable - continuing without Redis-dependent features",
                    error=str(e),
                )
            else:
                raise

        logger.info("All services initialized successfully", services=startup_tasks)

        # Background jobs (optional, configured in settings)
        retention_config = settings.get_data_retention_config()

        if settings.TOKEN_REFRESH_ENABLED:
            # Enable token refresh job (OAuth cleanup disabled due to race conditions)
            logger.info("Starting token refresh background job")

            # Import token refresh job here, after database pool is ready
            from app.jobs.token_refresh_job import start_token_refresh_scheduler

            # Start token refresh job (runs every 10 minutes)
            asyncio.create_task(start_token_refresh_scheduler())

            logger.info("Token refresh job started successfully")
        else:
            logger.info("Token refresh background job disabled", env_flag="TOKEN_REFRESH_ENABLED")

        if retention_config["cleanup_enabled"]:
            # Enable data cleanup job (GDPR compliance)
            logger.info(
                "Starting data cleanup background job",
                schedule_hour=retention_config["cleanup_schedule_hour"],
            )

            # Import data cleanup job here, after database pool is ready
            from app.jobs.data_cleanup_job import start_data_cleanup_scheduler

            # Start data cleanup job (runs daily at configured hour)
            asyncio.create_task(start_data_cleanup_scheduler())

            logger.info("Data cleanup job started successfully")
        else:
            logger.info(
                "Data cleanup background job disabled",
                environment=settings.environment,
                reason="Manual trigger only in development",
            )

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

    # Close shared HTTP clients
    try:
        logger.info("Closing external HTTP clients")
        from app.services.google_calendar_service import google_calendar_service
        from app.services.google_gmail_service import google_gmail_service

        await google_gmail_service.close()
        await google_calendar_service.close()
    except Exception as e:
        logger.error("Error closing HTTP clients", error=str(e))
        shutdown_errors.append(f"HTTP clients: {e}")

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
    description="Voice-first Gmail assistant with audit logging and compliance",
    version="0.1.0",
    lifespan=lifespan,
)

# ============================================================================
# MIDDLEWARE CONFIGURATION
# ============================================================================
# Middleware order matters! Applied in reverse order (bottom to top execution)
# Execution flow: Request → 1 → 2 → 3 → 4 → 5 → Endpoint → 5 → 4 → 3 → 2 → 1 → Response

# Get security configuration based on environment
security_config = settings.get_security_config()

# 5. HTTPS Enforcement (FIRST in execution - redirect HTTP to HTTPS)
#    Only enabled in production
if security_config["https_enforce"]:
    app.add_middleware(
        HTTPSEnforcementMiddleware,
        redirect_status_code=settings.HTTPS_REDIRECT_STATUS_CODE,
    )

# 4. CORS Middleware (allow specific origins)
#    Development: Allow localhost
#    Production: Lock down to specific domains
if security_config["cors_enabled"]:
    app.add_middleware(
        CORSMiddleware,
        allowed_origins=settings.get_cors_origins(),
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        max_age=settings.CORS_MAX_AGE,
    )

# 3. Security Headers Middleware (add security headers to all responses)
if security_config["security_headers_enabled"]:
    app.add_middleware(
        SecurityHeadersMiddleware,
        enforce_https=security_config["https_enforce"],
    )

# 2. Rate Limit Headers Middleware (reads from request.state.rate_limit_info)
app.add_middleware(RateLimitHeadersMiddleware)

# 1. Request Context Middleware (LAST - adds request ID, IP, user-agent)
#    This must run first in execution order (added last) so request.state
#    is populated for other middleware
app.add_middleware(RequestContextMiddleware)

# Log middleware configuration
middleware_list = ["RequestContextMiddleware", "RateLimitHeadersMiddleware"]
if security_config["security_headers_enabled"]:
    middleware_list.append("SecurityHeadersMiddleware")
if security_config["cors_enabled"]:
    middleware_list.append("CORSMiddleware")
if security_config["https_enforce"]:
    middleware_list.append("HTTPSEnforcementMiddleware")

logger.info(
    "Middleware configured",
    middleware=middleware_list,
    rate_limiting_enabled=settings.RATE_LIMIT_ENABLED,
    cors_enabled=security_config["cors_enabled"],
    security_headers_enabled=security_config["security_headers_enabled"],
    https_enforce=security_config["https_enforce"],
    environment=settings.environment,
)

# ============================================================================
# ROUTER CONFIGURATION
# ============================================================================

# Include routers
app.include_router(health.router)
app.include_router(protected.router)
app.include_router(onboarding.router)
app.include_router(onboarding_vip.router)
app.include_router(gmail_auth.router)
app.include_router(calendar.router)
app.include_router(gmail.router)
app.include_router(data_management.router)  # GDPR compliance endpoints


# ============================================================================
# REQUEST LOGGING (Custom Middleware)
# ============================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Log HTTP requests with timing and request context.

    Enhanced with request ID, IP address from RequestContextMiddleware.
    """
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000

    logger.info(
        "HTTP request completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(process_time, 2),
        request_id=getattr(request.state, "request_id", None),
        ip_address=getattr(request.state, "ip_address", None),
    )
    return response


def main() -> None:
    """Console entrypoint for running the API server."""
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port_raw = os.getenv("PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError:
        port = 8000
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
