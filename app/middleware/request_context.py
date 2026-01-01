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
    """

    async def dispatch(self, request: Request, call_next):
        """Process request and add context."""

        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Extract client IP address
        # Check X-Forwarded-For header first (for proxies/load balancers)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # X-Forwarded-For can contain multiple IPs: "client, proxy1, proxy2"
            # The first one is the original client
            ip_address = forwarded_for.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
        else:
            ip_address = None

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
