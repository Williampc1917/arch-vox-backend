"""
HTTPS Enforcement Middleware - Redirect HTTP to HTTPS in production.

HTTPS (SSL/TLS encryption) is required for:
- Gmail API compliance (MUST use HTTPS in production)
- OAuth security (prevents token theft)
- PII protection (encrypts data in transit)
- OWASP security requirements

Behavior:
- Development: Allow HTTP (localhost doesn't have SSL)
- Production: Redirect HTTP → HTTPS with 308 status

Important Notes for GCP Deployment:
- GCP Load Balancer terminates SSL (handles HTTPS)
- Your app receives HTTP requests with X-Forwarded-Proto header
- This middleware checks X-Forwarded-Proto to detect original protocol
- Redirects if X-Forwarded-Proto=http but HTTPS is required

Usage:
    from app.middleware.https_enforcement import HTTPSEnforcementMiddleware

    # Production only
    if settings.environment == "production":
        app.add_middleware(HTTPSEnforcementMiddleware)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class HTTPSEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Enforce HTTPS by redirecting HTTP requests to HTTPS.

    This middleware checks the request protocol and redirects if HTTP is used
    when HTTPS is required.

    Supports:
    - Direct HTTPS connections (checks request.url.scheme)
    - Proxied HTTPS (checks X-Forwarded-Proto header for GCP/AWS/etc.)
    """

    def __init__(self, app, redirect_status_code: int = 308):
        """
        Initialize HTTPS enforcement middleware.

        Args:
            app: FastAPI application
            redirect_status_code: HTTP status for redirect (308 = permanent)
                - 301: Permanent redirect (cached by browsers, changes POST to GET)
                - 302: Temporary redirect (not cached, changes POST to GET)
                - 307: Temporary redirect (preserves HTTP method)
                - 308: Permanent redirect (preserves HTTP method) ← RECOMMENDED
        """
        super().__init__(app)
        self.redirect_status_code = redirect_status_code

        logger.info(
            "HTTPS enforcement middleware initialized",
            redirect_status_code=self.redirect_status_code,
        )

    async def dispatch(self, request, call_next):
        """
        Process request and enforce HTTPS.

        Checks:
        1. X-Forwarded-Proto header (for load balancer/proxy)
        2. request.url.scheme (for direct connections)

        If HTTP detected → Redirect to HTTPS
        If HTTPS detected → Process normally
        """
        # Check X-Forwarded-Proto header (GCP Load Balancer sets this)
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()

        # Determine if request is HTTPS
        is_https = False

        if forwarded_proto:
            # Behind proxy/load balancer - trust X-Forwarded-Proto
            is_https = forwarded_proto == "https"
        else:
            # Direct connection - check scheme
            is_https = request.url.scheme == "https"

        # If not HTTPS, redirect
        if not is_https:
            # Build HTTPS URL
            https_url = request.url.replace(scheme="https")

            logger.info(
                "HTTP request redirected to HTTPS",
                original_url=str(request.url),
                https_url=str(https_url),
                forwarded_proto=forwarded_proto,
            )

            return RedirectResponse(
                url=str(https_url),
                status_code=self.redirect_status_code,
            )

        # HTTPS - process normally
        response = await call_next(request)
        return response
