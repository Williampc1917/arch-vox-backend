"""
Rate Limit Dependencies - Easy-to-use rate limiting for endpoints.

This module provides FastAPI dependencies for adding rate limiting
to endpoints with a single line of code.

Usage:
    from app.middleware.rate_limit_dependencies import rate_limit_user

    @router.get("/my-endpoint")
    async def my_endpoint(
        request: Request,
        claims: dict = Depends(auth_dependency),
        _rate: None = Depends(rate_limit_user),  # ← One line!
    ):
        # Endpoint code here
        pass

Features:
- Per-user rate limiting (authenticated endpoints)
- Per-IP rate limiting (all endpoints)
- Automatic 429 error responses
- Rate limit info added to request.state
- Integration with audit logging
"""

from fastapi import Depends, HTTPException, Request, status

from app.auth.verify import auth_dependency
from app.config import settings
from app.infrastructure.observability.logging import get_logger
from app.middleware.rate_limiter import rate_limiter

logger = get_logger(__name__)


async def rate_limit_user_only(
    request: Request,
    claims: dict = Depends(auth_dependency),
    limit: int | None = None,
) -> None:
    """
    Rate limit dependency for authenticated endpoints (per-user only).

    Args:
        request: FastAPI Request
        claims: JWT claims from auth_dependency
        limit: Custom limit (None = use default from config)

    Raises:
        HTTPException: 429 if rate limit exceeded

    Usage:
        @router.get("/endpoint")
        async def endpoint(
            request: Request,
            claims: dict = Depends(auth_dependency),
            _rate: None = Depends(rate_limit_user_only),
        ):
            pass
    """
    if not settings.RATE_LIMIT_ENABLED:
        return  # Rate limiting disabled

    user_id = claims.get("sub")
    if not user_id:
        logger.warning("Rate limit check skipped - no user_id in claims")
        return

    # Check per-user rate limit
    allowed, info = await rate_limiter.check_user_rate_limit(user_id, limit=limit)

    # Store rate limit info in request state (for headers middleware)
    request.state.rate_limit_info = info

    if not allowed:
        logger.warning(
            "User rate limit exceeded",
            user_id=user_id,
            limit=info["limit"],
            retry_after=info["retry_after"],
            path=request.url.path,
        )

        # Log to audit system as security event
        from app.utils.audit_helpers import audit_security_event

        await audit_security_event(
            request=request,
            event_type="rate_limit_exceeded",
            severity="medium",
            description=f"User exceeded rate limit on {request.url.path}",
            user_id=user_id,
            metadata={
                "limit": info["limit"],
                "retry_after": info["retry_after"],
                "endpoint": request.url.path,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Too many requests. Try again in {info['retry_after']} seconds.",
                "limit": info["limit"],
                "retry_after": info["retry_after"],
            },
            headers={"Retry-After": str(info["retry_after"])},
        )


async def rate_limit_ip_only(
    request: Request,
    limit: int | None = None,
) -> None:
    """
    Rate limit dependency for all endpoints (per-IP only).

    Use this for unauthenticated endpoints or public endpoints.

    Args:
        request: FastAPI Request
        limit: Custom limit (None = use default from config)

    Raises:
        HTTPException: 429 if rate limit exceeded

    Usage:
        @router.get("/public-endpoint")
        async def public_endpoint(
            request: Request,
            _rate: None = Depends(rate_limit_ip_only),
        ):
            pass
    """
    if not settings.RATE_LIMIT_ENABLED:
        return  # Rate limiting disabled

    ip_address = getattr(request.state, "ip_address", None)
    if not ip_address:
        logger.warning("Rate limit check skipped - no IP address")
        return

    # Check per-IP rate limit
    allowed, info = await rate_limiter.check_ip_rate_limit(ip_address, limit=limit)

    # Store rate limit info in request state
    request.state.rate_limit_info = info

    if not allowed:
        logger.warning(
            "IP rate limit exceeded",
            ip_address=ip_address,
            limit=info["limit"],
            retry_after=info["retry_after"],
            path=request.url.path,
        )

        # Log to audit system as security event (no user_id for unauthenticated)
        from app.utils.audit_helpers import audit_security_event

        await audit_security_event(
            request=request,
            event_type="rate_limit_exceeded",
            severity="medium",
            description=f"IP exceeded rate limit on {request.url.path}",
            user_id=None,  # No user for IP-based
            metadata={
                "limit": info["limit"],
                "retry_after": info["retry_after"],
                "endpoint": request.url.path,
                "ip_address": ip_address,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Too many requests from your IP. Try again in {info['retry_after']} seconds.",
                "limit": info["limit"],
                "retry_after": info["retry_after"],
            },
            headers={"Retry-After": str(info["retry_after"])},
        )


async def rate_limit_combined(
    request: Request,
    claims: dict = Depends(auth_dependency),
    user_limit: int | None = None,
    ip_limit: int | None = None,
) -> None:
    """
    Rate limit dependency with both per-user AND per-IP limits.

    This provides layered protection:
    1. Check IP rate limit first (broader protection)
    2. Then check user rate limit (finer-grained)

    Args:
        request: FastAPI Request
        claims: JWT claims from auth_dependency
        user_limit: Custom user limit (None = use default)
        ip_limit: Custom IP limit (None = use default)

    Raises:
        HTTPException: 429 if either limit exceeded

    Usage:
        @router.get("/endpoint")
        async def endpoint(
            request: Request,
            claims: dict = Depends(auth_dependency),
            _rate: None = Depends(rate_limit_combined),
        ):
            pass
    """
    if not settings.RATE_LIMIT_ENABLED:
        return  # Rate limiting disabled

    # Check IP limit first (broader protection)
    ip_address = getattr(request.state, "ip_address", None)
    if ip_address:
        ip_allowed, ip_info = await rate_limiter.check_ip_rate_limit(ip_address, limit=ip_limit)

        if not ip_allowed:
            request.state.rate_limit_info = ip_info

            logger.warning(
                "IP rate limit exceeded (combined check)",
                ip_address=ip_address,
                limit=ip_info["limit"],
                retry_after=ip_info["retry_after"],
            )

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests from your IP. Try again in {ip_info['retry_after']} seconds.",
                    "limit": ip_info["limit"],
                    "retry_after": ip_info["retry_after"],
                },
                headers={"Retry-After": str(ip_info["retry_after"])},
            )

    # Check user limit second (finer-grained)
    user_id = claims.get("sub")
    if user_id:
        user_allowed, user_info = await rate_limiter.check_user_rate_limit(
            user_id, limit=user_limit
        )

        # Store user rate limit info (takes precedence over IP info)
        request.state.rate_limit_info = user_info

        if not user_allowed:
            logger.warning(
                "User rate limit exceeded (combined check)",
                user_id=user_id,
                limit=user_info["limit"],
                retry_after=user_info["retry_after"],
            )

            # Audit log
            from app.utils.audit_helpers import audit_security_event

            await audit_security_event(
                request=request,
                event_type="rate_limit_exceeded",
                severity="medium",
                description=f"User exceeded rate limit on {request.url.path}",
                user_id=user_id,
                metadata={
                    "limit": user_info["limit"],
                    "retry_after": user_info["retry_after"],
                    "endpoint": request.url.path,
                },
            )

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests. Try again in {user_info['retry_after']} seconds.",
                    "limit": user_info["limit"],
                    "retry_after": user_info["retry_after"],
                },
                headers={"Retry-After": str(user_info["retry_after"])},
            )


# Convenience aliases for common use cases
rate_limit_user = rate_limit_combined  # Default: Both user + IP limits
rate_limit_ip = rate_limit_ip_only  # Only IP limits
