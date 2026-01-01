"""
Middleware components for request processing.

This package contains middleware for:
- Request context (request ID, IP address, user agent)
- Security (future: CORS, security headers, HTTPS)
- Rate limiting (future)
"""

from app.middleware.request_context import RequestContextMiddleware

__all__ = ["RequestContextMiddleware"]
