from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Env-based configuration. All values have local-dev defaults."""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "atomicvault"
    minio_secure: bool = False

    # Upload limits
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    # Janitor
    janitor_enabled: bool = True
    janitor_interval_seconds: float = 60.0
    janitor_older_than_seconds: int = 3600

    model_config = {"env_prefix": "", "case_sensitive": False}
