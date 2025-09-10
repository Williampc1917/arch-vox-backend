"""
main.py
-------
Purpose:
    The main FastAPI application entry point.
    Registers both public (health) and protected routes.
    Sets up structured logging for production monitoring.

Notes:
    - Protected routes under `app.routes.protected` require a valid Supabase JWT.
    - Onboarding routes under `app.routes.onboarding` require a valid Supabase JWT.
    - Health routes (`/healthz`, `/readyz`) are public and used for liveness/readiness checks.
    - All requests are logged in JSON format for observability.
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import settings
from app.infrastructure.observability.logging import get_logger, setup_logging
from app.routes import health, onboarding, protected

# Setup logging before creating the app
setup_logging(log_level="INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown."""
    # Startup
    logger.info("Application starting", environment=settings.environment, debug=settings.debug)

    yield

    # Shutdown
    logger.info("Application shutting down")


app = FastAPI(
    title="Voice Gmail Assistant",
    description="Voice-first Gmail assistant for hands-free email management",
    version="0.1.0",
    lifespan=lifespan,
)

# Public routes (no auth required)
app.include_router(health.router)

# Protected routes (require valid Supabase JWT)
app.include_router(protected.router)
app.include_router(onboarding.router)  # Added onboarding endpoints


@app.middleware("http")
async def log_requests(request: Request, call_next):
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
