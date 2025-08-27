"""
verify.py
---------
Purpose:
    This module provides helper functions to verify Supabase-issued JWTs (JSON Web Tokens)
    in a FastAPI backend.

    - Designed for projects using Supabase Auth with HS256 ("Legacy JWT Secret") signing.
    - Verifies tokens using the `SUPABASE_JWT_SECRET` from your environment variables.
    - Provides a FastAPI dependency (`auth_dependency`) for securing protected routes.

Usage:
    1. Set SUPABASE_JWT_SECRET in your `.env.local` to match the secret in Supabase Dashboard.
    2. Protect any route by adding:
         from app.auth.verify import auth_dependency
         @router.get("/secure-endpoint")
         def secure(data = Depends(auth_dependency)): ...
    3. Clients must send:
         Authorization: Bearer <access_token>
       where <access_token> is from Supabase Auth sign-in.
"""


import jwt
from fastapi import Header, HTTPException

from app.config import settings


def verify_supabase_jwt(token: str) -> dict:
    """Decode and verify a Supabase-issued JWT using the HS256 secret."""
    if not settings.SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=500, detail="Server missing SUPABASE_JWT_SECRET")
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase tokens vary 'aud'
        )
        return payload
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")


async def auth_dependency(authorization: str | None = Header(None)) -> dict:
    """
    FastAPI dependency for protecting routes.
    Validates Authorization header as 'Bearer <token>'.
    Returns the decoded token claims if valid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    return verify_supabase_jwt(token)
