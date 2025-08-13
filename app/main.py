"""
main.py
-------
Purpose:
    The main FastAPI application entry point.
    Registers both public (health) and protected routes.

Notes:
    - Protected routes under `app.routes.protected` require a valid Supabase JWT.
    - Health routes (`/healthz`, `/readyz`) are public and used for liveness/readiness checks.
"""

from fastapi import FastAPI
from app.routes import health
from app.routes import protected

app = FastAPI()

# Public routes (no auth required)
app.include_router(health.router)

# Protected routes (require valid Supabase JWT)
app.include_router(protected.router)
