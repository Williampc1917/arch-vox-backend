"""
protected.py OLDDD
------------
Purpose:
    Defines protected API endpoints that require a valid Supabase Auth JWT for access.

    - Uses the `auth_dependency` from app.auth.verify to secure endpoints.
    - Example route `/me` returns the authenticated user's claims.

Usage:
    1. Ensure `SUPABASE_JWT_SECRET` is set in `.env.local`.
    2. Include this router in your `app/main.py` with:
         from app.routes import protected
         app.include_router(protected.router)
    3. Call `/me` with:
         Authorization: Bearer <access_token>
       where <access_token> is from Supabase Auth sign-in.
"""

from fastapi import APIRouter, Depends

from app.auth.verify import auth_dependency

router = APIRouter()


@router.get("/me")
def me(claims: dict = Depends(auth_dependency)):
    """
    Return basic info from the authenticated user's JWT.
    Claims typically include:
      - sub: Supabase user ID
      - email: user's email
      - role: 'authenticated'
    """
    return {
        "user_id": claims.get("sub"),
        "email": claims.get("email"),
        "role": claims.get("role"),
        "claims": claims,
    }
