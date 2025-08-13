from pathlib import Path
from urllib.parse import urlparse
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent / ".env.local"

class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_JWKS_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_DB_URL: str
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_JWT_SECRET: str | None = None  # <-- Add this

    UPSTASH_REDIS_REST_URL: str
    UPSTASH_REDIS_REST_TOKEN: str

    VAPI_PRIVATE_KEY: str | None = None

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

settings = Settings()
