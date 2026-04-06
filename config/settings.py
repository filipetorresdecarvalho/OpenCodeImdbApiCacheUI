import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    # Database
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "imdb_cache")
    db_charset: str = "utf8mb4"

    # IMDB API
    imdb_api_key: str = os.getenv("IMDB_API_KEY", "")
    imdb_api_base: str = os.getenv("IMDB_API_BASE", "https://imdb-api.com")
    imdb_rate_limit: int = int(os.getenv("IMDB_RATE_LIMIT", "10"))
    imdb_max_retries: int = int(os.getenv("IMDB_MAX_RETRIES", "3"))
    imdb_backoff_factor: float = float(os.getenv("IMDB_BACKOFF_FACTOR", "1.5"))

    # Cache
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    cache_default_ttl: int = int(os.getenv("CACHE_DEFAULT_TTL", "86400"))

    # Storage
    storage_strategy: str = os.getenv("STORAGE_STRATEGY", "hybrid")
    cache_dir: str = os.getenv("CACHE_DIR", "cache/imdbapi")
    max_cache_size_mb: int = int(os.getenv("MAX_CACHE_SIZE_MB", "500"))

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "logs/app.log")
    log_json: bool = os.getenv("LOG_JSON", "true").lower() == "true"

    # App
    app_title: str = "IMDB Cache UI"
    app_version: str = "1.0.0"

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?charset={self.db_charset}"
        )

    @property
    def db_url_no_db(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}"
            f"?charset={self.db_charset}"
        )
