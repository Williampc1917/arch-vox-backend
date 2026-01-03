from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent / ".env.local"


class Settings(BaseSettings):
    # Environment settings
    environment: str = "development"
    debug: bool = False

    # Supabase settings
    SUPABASE_URL: str
    SUPABASE_JWKS_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_DB_URL: str
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_JWT_SECRET: str | None = None

    # Redis settings
    UPSTASH_REDIS_REST_URL: str
    UPSTASH_REDIS_REST_TOKEN: str

    # VAPI settings
    VAPI_PRIVATE_KEY: str | None = None

    # Gmail OAuth settings
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None

    ENCRYPTION_KEY: str | None = None
    HASHING_SECRET: str

    # OpenAI settings
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TEMPERATURE: float = 0.0
    OPENAI_MAX_TOKENS: int = 1000

    # Email Style Configuration
    EMAIL_STYLE_MAX_RETRIES: int = 3
    EMAIL_STYLE_TIMEOUT_SECONDS: int = 30
    EMAIL_STYLE_REDIS_CACHE_ENABLED: bool = True

    # Background job toggles
    TOKEN_REFRESH_ENABLED: bool = False
    VIP_BACKFILL_ENABLED: bool = False
    VIP_BACKFILL_QUEUE_NAME: str = "vip_backfill:pq"

    # =================================================================
    # RATE LIMITING SETTINGS - Anti-abuse and compliance
    # =================================================================
    # Enable/disable rate limiting globally
    RATE_LIMIT_ENABLED: bool = True

    # Default rate limits (per-user, per minute)
    RATE_LIMIT_USER_PER_MINUTE: int = 1000  # Generous for development
    RATE_LIMIT_IP_PER_MINUTE: int = 2000    # Higher for IP-based

    # Endpoint-specific rate limits
    RATE_LIMIT_VIP_ENDPOINTS: int = 500      # VIP endpoints (PII access)
    RATE_LIMIT_WRITE_ENDPOINTS: int = 300    # Write operations
    RATE_LIMIT_READ_ENDPOINTS: int = 1000    # Read operations

    # Rate limiter behavior
    RATE_LIMIT_WINDOW_SECONDS: int = 60      # Time window (1 minute)
    RATE_LIMIT_FAIL_OPEN: bool = True        # Allow requests if Redis fails

    # =================================================================
    # SECURITY SETTINGS - CORS, Headers, HTTPS
    # =================================================================
    # CORS (Cross-Origin Resource Sharing)
    CORS_ENABLED: bool = True
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_MAX_AGE: int = 600  # 10 minutes

    # Security Headers
    SECURITY_HEADERS_ENABLED: bool = True

    # HTTPS Enforcement (production only)
    HTTPS_ENFORCE: bool = False  # Disabled in dev, enabled in prod
    HTTPS_REDIRECT_STATUS_CODE: int = 308  # Permanent redirect

    # Trusted Proxies (for X-Forwarded-For validation)
    # In local dev: Trust localhost
    # In GCP: Trust internal load balancer IPs (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    TRUSTED_PROXY_IPS: list[str] = ["127.0.0.1", "::1"]  # Localhost only in dev
    TRUST_X_FORWARDED_FOR: bool = False  # Disabled in dev (no proxy), enabled in prod

    # Request Size Limits (prevent DOS via huge payloads)
    MAX_REQUEST_SIZE_MB: int = 10  # Maximum request body size in MB
    MAX_REQUEST_SIZE_BYTES: int = 10 * 1024 * 1024  # 10MB in bytes

    # =================================================================
    # DATA RETENTION & GDPR SETTINGS
    # =================================================================
    # Data retention periods (in days)
    DATA_RETENTION_CACHED_DATA_DAYS: int = 90  # OAuth tokens, email cache, VIP data
    DATA_RETENTION_AUDIT_LOGS_DAYS: int = 365  # 1 year for audit logs (compliance)
    DATA_RETENTION_GRACE_PERIOD_DAYS: int = 30  # Soft delete grace period

    # Data cleanup job
    DATA_CLEANUP_ENABLED: bool = False  # Disabled in dev, enabled in prod
    DATA_CLEANUP_SCHEDULE_HOUR: int = 2  # Run at 2 AM (low traffic)

    # GDPR features
    GDPR_DATA_EXPORT_ENABLED: bool = True  # Allow users to export their data
    GDPR_DATA_DELETION_ENABLED: bool = True  # Allow users to delete their data
    GDPR_REVOKE_OAUTH_ON_DELETE: bool = True  # Revoke OAuth tokens with Google

    # =================================================================
    # DATABASE POOL SETTINGS - Simple and configurable
    # =================================================================
    DB_POOL_MIN_SIZE: int = 3
    DB_POOL_MAX_SIZE: int = 12
    DB_POOL_TIMEOUT: float = 30.0
    DB_POOL_MAX_IDLE: float = 600.0  # 10 minutes
    DB_POOL_MAX_LIFETIME: float = 3600.0  # 1 hour

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # derive sensible defaults if not provided
    def jwks_url(self) -> str:
        if self.SUPABASE_JWKS_URL:
            return self.SUPABASE_JWKS_URL
        base = self.SUPABASE_URL.rstrip("/")
        return f"{base}/auth/v1/.well-known/jwks.json"

    def project_ref(self) -> str | None:
        """
        Extract the Supabase project ref from SUPABASE_URL host, e.g.
        https://ykvceus...supabase.co -> ykvceus...
        """
        try:
            host = urlparse(self.SUPABASE_URL).hostname or ""
            return host.split(".")[0]
        except Exception:
            return None

    def gmail_redirect_uri(self) -> str:
        """Get Gmail OAuth redirect URI with fallback."""
        if self.GOOGLE_REDIRECT_URI:
            return self.GOOGLE_REDIRECT_URI
        # Default for local development
        return "http://localhost:8000/auth/gmail/callback"

    def get_db_pool_config(self) -> dict:
        """
        Get database pool configuration.
        Adjust environment-specific settings based on self.environment.
        """
        # Base configuration
        config = {
            "min_size": self.DB_POOL_MIN_SIZE,
            "max_size": self.DB_POOL_MAX_SIZE,
            "timeout": self.DB_POOL_TIMEOUT,
            "max_idle": self.DB_POOL_MAX_IDLE,
            "max_lifetime": self.DB_POOL_MAX_LIFETIME,
        }

        # Adjust for environment if needed
        if self.environment == "development":
            # More conservative for local development
            config.update(
                {
                    "min_size": 4,  # Cap at 2 for dev
                    "max_size": 8,  # Cap at 5 for dev
                    "timeout": 15.0,  # Shorter timeout for dev
                }
            )
        elif self.environment == "production":
            # Use the configured values as-is for production
            pass

        return config

    def get_rate_limits(self) -> dict:
        """
        Get rate limits adjusted for environment.

        Development: Very generous (won't block during testing)
        Production: Strict (protect against abuse)
        """
        if self.environment == "development":
            return {
                "user_per_minute": 1000,    # Very generous
                "ip_per_minute": 2000,
                "vip_endpoints": 500,
                "write_endpoints": 300,
                "read_endpoints": 1000,
            }
        elif self.environment == "production":
            return {
                "user_per_minute": 100,     # Stricter for production
                "ip_per_minute": 200,
                "vip_endpoints": 60,
                "write_endpoints": 30,
                "read_endpoints": 100,
            }
        else:
            # Default to configured values
            return {
                "user_per_minute": self.RATE_LIMIT_USER_PER_MINUTE,
                "ip_per_minute": self.RATE_LIMIT_IP_PER_MINUTE,
                "vip_endpoints": self.RATE_LIMIT_VIP_ENDPOINTS,
                "write_endpoints": self.RATE_LIMIT_WRITE_ENDPOINTS,
                "read_endpoints": self.RATE_LIMIT_READ_ENDPOINTS,
            }

    def get_cors_origins(self) -> list[str]:
        """
        Get CORS allowed origins based on environment.

        Development: Allow localhost (iOS simulator, web testing)
        Production: Lock down to specific domains only
        """
        if self.environment == "development":
            # Allow all common localhost ports for development
            return [
                "http://localhost:3000",      # React/Next.js default
                "http://localhost:8000",      # Backend (self)
                "http://localhost:8080",      # Alternative web port
                "http://127.0.0.1:3000",      # IPv4 localhost
                "http://127.0.0.1:8000",
                "http://127.0.0.1:8080",
                "capacitor://localhost",      # iOS Capacitor (if using)
                "ionic://localhost",          # Ionic (if using)
            ]
        elif self.environment == "production":
            # Production: Lock down to specific domains
            # TODO: Replace with your actual production domains
            return [
                "https://your-app-domain.com",  # Your web dashboard (if any)
                # iOS native apps don't need CORS (they're not browsers)
                # Add any web frontends here
            ]
        else:
            # Staging/test: Similar to dev but with HTTPS
            return [
                "https://staging.your-app-domain.com",
                "http://localhost:3000",
            ]

    def get_security_config(self) -> dict:
        """
        Get security configuration based on environment.

        Returns headers, HTTPS enforcement, etc.
        """
        if self.environment == "production":
            return {
                "cors_enabled": self.CORS_ENABLED,
                "security_headers_enabled": self.SECURITY_HEADERS_ENABLED,
                "https_enforce": True,  # Always enforce HTTPS in production
                "max_request_size_bytes": self.MAX_REQUEST_SIZE_BYTES,
            }
        else:
            # Development: Relaxed security (HTTP allowed)
            return {
                "cors_enabled": self.CORS_ENABLED,
                "security_headers_enabled": self.SECURITY_HEADERS_ENABLED,
                "https_enforce": False,  # Allow HTTP in dev
                "max_request_size_bytes": self.MAX_REQUEST_SIZE_BYTES,
            }

    def get_data_retention_config(self) -> dict:
        """
        Get data retention configuration based on environment.

        Returns retention periods, cleanup settings, GDPR features.
        """
        if self.environment == "production":
            return {
                "cached_data_days": self.DATA_RETENTION_CACHED_DATA_DAYS,
                "audit_logs_days": self.DATA_RETENTION_AUDIT_LOGS_DAYS,
                "grace_period_days": self.DATA_RETENTION_GRACE_PERIOD_DAYS,
                "cleanup_enabled": True,  # Always enable cleanup in production
                "cleanup_schedule_hour": self.DATA_CLEANUP_SCHEDULE_HOUR,
                "data_export_enabled": self.GDPR_DATA_EXPORT_ENABLED,
                "data_deletion_enabled": self.GDPR_DATA_DELETION_ENABLED,
                "revoke_oauth_on_delete": self.GDPR_REVOKE_OAUTH_ON_DELETE,
            }
        else:
            # Development: Manual cleanup only
            return {
                "cached_data_days": self.DATA_RETENTION_CACHED_DATA_DAYS,
                "audit_logs_days": self.DATA_RETENTION_AUDIT_LOGS_DAYS,
                "grace_period_days": self.DATA_RETENTION_GRACE_PERIOD_DAYS,
                "cleanup_enabled": False,  # Disable automatic cleanup in dev
                "cleanup_schedule_hour": self.DATA_CLEANUP_SCHEDULE_HOUR,
                "data_export_enabled": self.GDPR_DATA_EXPORT_ENABLED,
                "data_deletion_enabled": self.GDPR_DATA_DELETION_ENABLED,
                "revoke_oauth_on_delete": self.GDPR_REVOKE_OAUTH_ON_DELETE,
            }


settings = Settings()

# =================================================================
# QUICK CONFIGURATION REFERENCE
# =================================================================
"""
To change pool settings, just edit the values above:

CONSERVATIVE (shared free tier):
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 6

BALANCED (recommended for free tier):
    DB_POOL_MIN_SIZE: int = 3
    DB_POOL_MAX_SIZE: int = 12

AGGRESSIVE (single app on free tier):
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 20

LOAD TESTING (temporary high limits):
    DB_POOL_MIN_SIZE: int = 10
    DB_POOL_MAX_SIZE: int = 30

"""
