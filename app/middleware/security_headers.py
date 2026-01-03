"""
Security Headers Middleware - Add security headers to all responses.

Security headers protect against common web vulnerabilities like:
- XSS (Cross-Site Scripting)
- Clickjacking
- MIME sniffing attacks
- Protocol downgrade attacks

These headers are required for:
- OWASP security compliance
- Gmail API security audit
- General security best practices

Headers added:
1. Content-Security-Policy (CSP) - Prevents XSS attacks
2. X-Frame-Options - Prevents clickjacking
3. X-Content-Type-Options - Prevents MIME sniffing
4. Strict-Transport-Security (HSTS) - Forces HTTPS (production only)
5. X-XSS-Protection - Legacy XSS protection (for old browsers)
6. Referrer-Policy - Controls referrer information
7. Permissions-Policy - Controls browser features

Usage:
    from app.middleware.security_headers import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware, enforce_https=True)
"""

from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all HTTP responses.

    This middleware adds industry-standard security headers to protect
    against common web vulnerabilities.
    """

    def __init__(self, app, enforce_https: bool = False):
        """
        Initialize security headers middleware.

        Args:
            app: FastAPI application
            enforce_https: Whether to add HSTS header (production only)
        """
        super().__init__(app)
        self.enforce_https = enforce_https

        logger.info(
            "Security headers middleware initialized",
            enforce_https=self.enforce_https,
        )

    async def dispatch(self, request, call_next):
        """
        Process request and add security headers to response.

        Args:
            request: Incoming request
            call_next: Next middleware/endpoint

        Returns:
            Response with security headers added
        """
        response = await call_next(request)

        # ============================================================
        # 1. Content-Security-Policy (CSP)
        # ============================================================
        # Prevents XSS attacks by controlling which resources can be loaded
        # This is a restrictive policy for API-only backends
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "  # Block everything by default
            "frame-ancestors 'none'; "  # Don't allow framing (redundant with X-Frame-Options)
            "base-uri 'none'; "  # Prevent base tag injection
            "form-action 'none'"  # No forms allowed (API only)
        )

        # ============================================================
        # 2. X-Frame-Options
        # ============================================================
        # Prevents clickjacking attacks by blocking iframe embedding
        response.headers["X-Frame-Options"] = "DENY"

        # ============================================================
        # 3. X-Content-Type-Options
        # ============================================================
        # Prevents MIME sniffing attacks (browser guessing content type)
        response.headers["X-Content-Type-Options"] = "nosniff"

        # ============================================================
        # 4. Strict-Transport-Security (HSTS) - Production only
        # ============================================================
        # Forces HTTPS for all future requests (prevents protocol downgrade)
        # Only add in production when HTTPS is enforced
        if self.enforce_https:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; "  # 1 year
                "includeSubDomains; "  # Apply to all subdomains
                "preload"  # Allow inclusion in browser HSTS preload lists
            )

        # ============================================================
        # 5. X-XSS-Protection
        # ============================================================
        # Legacy XSS protection for older browsers
        # Modern browsers rely on CSP instead
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # ============================================================
        # 6. Referrer-Policy
        # ============================================================
        # Controls how much referrer information is sent with requests
        # "strict-origin-when-cross-origin" is a good balance:
        # - Same-origin: Full URL
        # - Cross-origin HTTPS→HTTPS: Origin only
        # - Cross-origin HTTPS→HTTP: Nothing (security downgrade)
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # ============================================================
        # 7. Permissions-Policy (formerly Feature-Policy)
        # ============================================================
        # Controls which browser features/APIs can be used
        # For API-only backend, disable everything
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "  # No geolocation
            "microphone=(), "  # No microphone
            "camera=(), "  # No camera
            "payment=(), "  # No payment APIs
            "usb=(), "  # No USB access
            "magnetometer=(), "  # No magnetometer
            "accelerometer=(), "  # No accelerometer
            "gyroscope=()"  # No gyroscope
        )

        # ============================================================
        # 8. X-Permitted-Cross-Domain-Policies
        # ============================================================
        # Prevents Adobe Flash and PDF from loading data cross-domain
        # (mostly legacy, but still recommended)
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        return response
