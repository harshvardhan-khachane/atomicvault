import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "atomicvault")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() in ("true", "1", "yes")

# Upload limits
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", str(10 * 1024 * 1024))) # 10 MB
MAX_TTL_SECONDS = int(os.getenv("MAX_TTL_SECONDS", "3600"))

# Janitor
JANITOR_ENABLED = os.getenv("JANITOR_ENABLED", "true").lower() in ("true", "1", "yes")
JANITOR_INTERVAL_SECONDS = float(os.getenv("JANITOR_INTERVAL_SECONDS", "60.0"))
JANITOR_OLDER_THAN_SECONDS = int(os.getenv("JANITOR_OLDER_THAN_SECONDS", "3600"))

# CLI
ATOMICVAULT_URL = os.getenv("ATOMICVAULT_URL", "http://localhost:8000")
ATOMICVAULT_CLIENT_ENCRYPT = os.getenv("ATOMICVAULT_CLIENT_ENCRYPT", "false").lower() in ("true", "1", "yes")
ATOMICVAULT_CLIENT_KEY_B64 = os.getenv("ATOMICVAULT_CLIENT_KEY_B64", "")

# Crypto
CRYPTO_PREFIX = os.getenv("CRYPTO_PREFIX", "AV1").encode()
CRYPTO_NONCE_LEN = int(os.getenv("CRYPTO_NONCE_LEN", "12"))
CRYPTO_KEY_LEN = int(os.getenv("CRYPTO_KEY_LEN", "32"))
