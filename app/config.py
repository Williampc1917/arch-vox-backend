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
