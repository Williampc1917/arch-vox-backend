"""
CORS Middleware - Cross-Origin Resource Sharing configuration.

CORS controls which domains (origins) can access your API from browsers.
This is critical for web-based clients but doesn't affect native iOS apps.

Purpose:
- Allow specific domains to make requests to your API
- Prevent unauthorized websites from accessing your API
- Support credentials (cookies, auth headers)

Configuration:
- Development: Allow localhost (for web testing, iOS simulator)
- Production: Lock down to specific trusted domains

Important Notes:
- iOS native apps don't need CORS (they're not browsers)
- CORS is only enforced by browsers
- Still required for Gmail API compliance (if you have web dashboard)

Usage:
    from app.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allowed_origins=["http://localhost:3000"],
        allow_credentials=True,
    )

Headers added:
- Access-Control-Allow-Origin: Which origin is allowed
- Access-Control-Allow-Methods: Which HTTP methods allowed
- Access-Control-Allow-Headers: Which headers allowed
- Access-Control-Allow-Credentials: Whether cookies/auth allowed
- Access-Control-Max-Age: How long to cache preflight responses
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class CORSMiddleware(BaseHTTPMiddleware):
    """
    CORS (Cross-Origin Resource Sharing) middleware.

    Handles preflight OPTIONS requests and adds CORS headers to responses.
    """

    def __init__(
        self,
        app,
        allowed_origins: list[str] | None = None,
        allow_credentials: bool = True,
        allow_methods: list[str] | None = None,
        allow_headers: list[str] | None = None,
        max_age: int = 600,
    ):
        """
        Initialize CORS middleware.

        Args:
            app: FastAPI application
            allowed_origins: List of allowed origins (e.g., ["http://localhost:3000"])
            allow_credentials: Whether to allow credentials (cookies, auth headers)
            allow_methods: Allowed HTTP methods (default: common methods)
            allow_headers: Allowed request headers (default: common headers)
            max_age: How long (seconds) to cache preflight responses
        """
        super().__init__(app)
        self.allowed_origins = allowed_origins or []
        self.allow_credentials = allow_credentials
        self.allow_methods = allow_methods or [
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "HEAD",
        ]
        self.allow_headers = allow_headers or [
            "Accept",
            "Accept-Language",
            "Content-Type",
            "Content-Language",
            "Authorization",
            "X-Request-ID",
            "X-Requested-With",
        ]
        self.max_age = max_age

        logger.info(
            "CORS middleware initialized",
            allowed_origins=self.allowed_origins,
            allow_credentials=self.allow_credentials,
        )

    async def dispatch(self, request, call_next):
        """
        Process request and add CORS headers.

        Handles:
        1. Preflight OPTIONS requests (return immediately)
        2. Regular requests (add CORS headers to response)
        """
        origin = request.headers.get("origin")

        # Check if origin is allowed
        is_allowed_origin = origin in self.allowed_origins if origin else False

        # Handle preflight OPTIONS request
        if request.method == "OPTIONS":
            if is_allowed_origin:
                return self._preflight_response(origin)
            else:
                # Origin not allowed - return 403 Forbidden
                logger.warning(
                    "CORS preflight rejected - origin not allowed",
                    origin=origin,
                    allowed_origins=self.allowed_origins,
                )
                return Response(status_code=403, content="Origin not allowed")

        # Regular request - process normally
        response = await call_next(request)

        # Add CORS headers if origin is allowed
        if is_allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"
        elif origin:
            # Origin present but not allowed - log warning
            logger.warning(
                "CORS request from disallowed origin",
                origin=origin,
                path=request.url.path,
                allowed_origins=self.allowed_origins,
            )

        return response

    def _preflight_response(self, origin: str) -> Response:
        """
        Create a response for CORS preflight OPTIONS request.

        Args:
            origin: The allowed origin making the request

        Returns:
            Response with CORS preflight and security headers
        """
        headers = {
            # CORS headers
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": ", ".join(self.allow_methods),
            "Access-Control-Allow-Headers": ", ".join(self.allow_headers),
            "Access-Control-Max-Age": str(self.max_age),
            # Security headers (since preflight bypasses SecurityHeadersMiddleware)
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }

        if self.allow_credentials:
            headers["Access-Control-Allow-Credentials"] = "true"

        logger.debug(
            "CORS preflight request handled",
            origin=origin,
            methods=self.allow_methods,
        )

        return Response(status_code=204, headers=headers)
