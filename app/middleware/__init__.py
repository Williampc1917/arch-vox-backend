"""
Middleware components for request processing.

This package contains middleware for:
- Request context (request ID, IP address, user agent)
- Rate limiting (anti-abuse protection)
- Security (CORS, security headers, HTTPS enforcement)
"""

from app.middleware.cors import CORSMiddleware
from app.middleware.https_enforcement import HTTPSEnforcementMiddleware
from app.middleware.rate_limit_dependencies import (
    rate_limit_combined,
    rate_limit_ip,
    rate_limit_user,
)
from app.middleware.rate_limit_headers import RateLimitHeadersMiddleware
from app.middleware.rate_limiter import rate_limiter
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware

__all__ = [
    "RequestContextMiddleware",
    "RateLimitHeadersMiddleware",
    "SecurityHeadersMiddleware",
    "CORSMiddleware",
    "HTTPSEnforcementMiddleware",
    "rate_limiter",
    "rate_limit_user",
    "rate_limit_ip",
    "rate_limit_combined",
]
