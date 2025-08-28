"""
verify.py
---------
Purpose:
    JWT verification using Supabase JWKS (ES256).

Notes:
    - Replaces legacy HS256 verification.
    - Fetches JWKS from Supabase and caches keys.
    - Provides `auth_dependency` for protected routes.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

SUPABASE_AUDIENCE = "authenticated"

_jwk_client = PyJWKClient(settings.jwks_url())
_security = HTTPBearer()


def verify_jwt(token: str) -> dict:
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],  # Supabase now uses ES256
            audience=SUPABASE_AUDIENCE,
            options={"verify_exp": True},
        )
        return decoded
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def auth_dependency(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> dict:
    token = credentials.credentials
    return verify_jwt(token)
