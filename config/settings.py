"""
Application-wide settings.

All configuration is loaded from environment variables with sensible defaults.
The free IMDB API does not require an API key, so IMDB_API_KEY is optional.
Rate limiting is enforced at 1 request/second to avoid being blocked.
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Central configuration container.

    All values can be overridden via environment variables.
    Defaults are tuned for local development with a local MySQL/MariaDB instance.
    """

    # ----------------------------------------------------------------
    # Database configuration
    # ----------------------------------------------------------------
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "imdb_cache")
    db_charset: str = "utf8mb4"

    # ----------------------------------------------------------------
    # IMDB API configuration (free tier, no key required)
    # ----------------------------------------------------------------
    imdb_api_key: str = os.getenv("IMDB_API_KEY", "")
    imdb_api_base: str = os.getenv("IMDB_API_BASE", "https://imdb-api.com")
    # Max requests per second — kept at 1 to avoid rate-limit bans
    imdb_rate_limit: int = int(os.getenv("IMDB_RATE_LIMIT", "1"))
    # How many times to retry a failed request before giving up
    imdb_max_retries: int = int(os.getenv("IMDB_MAX_RETRIES", "3"))
    # Exponential backoff multiplier between retries
    imdb_backoff_factor: float = float(os.getenv("IMDB_BACKOFF_FACTOR", "1.5"))

    # ----------------------------------------------------------------
    # Cache configuration
    # ----------------------------------------------------------------
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    cache_default_ttl: int = int(os.getenv("CACHE_DEFAULT_TTL", "86400"))

    # ----------------------------------------------------------------
    # Storage configuration
    # ----------------------------------------------------------------
    storage_strategy: str = os.getenv("STORAGE_STRATEGY", "hybrid")
    cache_dir: str = os.getenv("CACHE_DIR", "cache/imdbapi")
    max_cache_size_mb: int = int(os.getenv("MAX_CACHE_SIZE_MB", "500"))

    # ----------------------------------------------------------------
    # Logging configuration
    # ----------------------------------------------------------------
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "logs/app.log")
    log_error_file: str = os.getenv("LOG_ERROR_FILE", "logs/errors.json")
    log_json: bool = os.getenv("LOG_JSON", "true").lower() == "true"

    # ----------------------------------------------------------------
    # App metadata
    # ----------------------------------------------------------------
    app_title: str = "IMDB Cache UI"
    app_version: str = "1.0.0"

    @property
    def db_url(self) -> str:
        """Full SQLAlchemy connection URL including the database name."""
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?charset={self.db_charset}"
        )

    @property
    def db_url_no_db(self) -> str:
        """Connection URL without database name — used for CREATE DATABASE."""
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}"
            f"?charset={self.db_charset}"
        )
