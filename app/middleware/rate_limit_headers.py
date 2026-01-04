"""
Rate Limit Headers Middleware - Add rate limit info to responses.

This middleware automatically adds standard rate limit headers to ALL
responses, informing clients about their rate limit status.

Headers added:
- X-RateLimit-Limit: Maximum requests allowed in the window
- X-RateLimit-Remaining: Remaining requests in current window
- Retry-After: Seconds to wait before retrying (if rate limited)

These are industry-standard headers used by GitHub, Twitter, Stripe, etc.

Usage:
    from app.middleware.rate_limit_headers import RateLimitHeadersMiddleware

    app.add_middleware(RateLimitHeadersMiddleware)

Design:
- Reads rate_limit_info from request.state (set by rate limit dependencies)
- Adds headers to all responses (including 429 errors)
- Graceful if rate_limit_info is missing (no headers added)
"""

from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add rate limit headers to all responses.

    This middleware reads rate limit information from request.state.rate_limit_info
    (populated by rate limit dependencies) and adds standard headers to the response.

    Headers:
    - X-RateLimit-Limit: str - Maximum requests allowed
    - X-RateLimit-Remaining: str - Remaining requests in window
    - Retry-After: str - Seconds until can retry (only if rate limited)
    """

    async def dispatch(self, request, call_next):
        """
        Process request and add rate limit headers to response.

        Args:
            request: Incoming request
            call_next: Next middleware/endpoint

        Returns:
            Response with rate limit headers added
        """
        response = await call_next(request)

        # Check if rate limit info is available
        rate_limit_info = getattr(request.state, "rate_limit_info", None)

        if rate_limit_info:
            # Add X-RateLimit-Limit header
            if "limit" in rate_limit_info:
                response.headers["X-RateLimit-Limit"] = str(rate_limit_info["limit"])

            # Add X-RateLimit-Remaining header
            if "remaining" in rate_limit_info:
                response.headers["X-RateLimit-Remaining"] = str(rate_limit_info["remaining"])

            # Add Retry-After header (if rate limited)
            if not rate_limit_info.get("allowed", True) and "retry_after" in rate_limit_info:
                response.headers["Retry-After"] = str(rate_limit_info["retry_after"])

            # Add X-RateLimit-Reset header (timestamp when limit resets)
            # Calculate as current_time + retry_after
            if "retry_after" in rate_limit_info:
                import time

                reset_timestamp = int(time.time()) + rate_limit_info["retry_after"]
                response.headers["X-RateLimit-Reset"] = str(reset_timestamp)

        return response
