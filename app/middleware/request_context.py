"""
RequestContext Middleware - Adds request tracking to all requests.

This middleware automatically adds the following to every request:
- request_id: Unique ID for request tracing
- ip_address: Client IP address
- user_agent: Client user agent string

These values are stored in request.state and can be accessed by:
- Audit logging
- Error tracking
- Security monitoring
- Request debugging

Usage:
    In endpoints:
        request.state.request_id
        request.state.ip_address
        request.state.user_agent

    In audit logging:
        await audit_logger.log(
            user_id=user_id,
            action="something_happened",
            ip_address=request.state.ip_address,  # Automatic!
            user_agent=request.state.user_agent,  # Automatic!
            request_id=request.state.request_id,  # Automatic!
        )
"""

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Add request context to all incoming requests.

    Adds to request.state:
    - request_id: UUID for tracing this request
    - ip_address: Client IP address
    - user_agent: Client user agent string

    Also adds X-Request-ID header to responses for client-side tracing.

    Request.state Namespace Convention:
    - request_id, ip_address, user_agent: Set by RequestContextMiddleware
    - rate_limit_info: Set by rate limit dependencies
    - Do not add other attributes without updating this documentation
    """

    async def dispatch(self, request: Request, call_next):
        """Process request and add context."""

        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Extract client IP address with proxy protection
        ip_address = self._extract_client_ip(request)
        request.state.ip_address = ip_address

        # Extract user agent
        user_agent = request.headers.get("user-agent")
        request.state.user_agent = user_agent

        # Log request started (useful for debugging)
        logger.debug(
            "Request started",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Process request
        response = await call_next(request)

        # Add request ID to response headers (for client-side tracing)
        response.headers["X-Request-ID"] = request_id

        return response

    def _extract_client_ip(self, request: Request) -> str | None:
        """
        Extract client IP address with proxy spoofing protection.

        Only trusts X-Forwarded-For header if:
        1. TRUST_X_FORWARDED_FOR is enabled (production with load balancer)
        2. Request comes from a trusted proxy IP

        This prevents attackers from spoofing their IP to bypass rate limiting.

        Args:
            request: FastAPI Request

        Returns:
            Client IP address or None
        """
        # If not trusting X-Forwarded-For (local dev), use direct connection IP
        if not settings.TRUST_X_FORWARDED_FOR:
            return request.client.host if request.client else None

        # In production with load balancer: validate proxy is trusted
        if request.client and request.client.host in settings.TRUSTED_PROXY_IPS:
            # Request came from trusted proxy - trust X-Forwarded-For header
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                # X-Forwarded-For format: "client, proxy1, proxy2"
                # First IP is the original client
                ip_address = forwarded_for.split(",")[0].strip()
                logger.debug(
                    "Using X-Forwarded-For from trusted proxy",
                    proxy_ip=request.client.host,
                    client_ip=ip_address,
                )
                return ip_address

        # Fallback to direct connection IP
        return request.client.host if request.client else None
