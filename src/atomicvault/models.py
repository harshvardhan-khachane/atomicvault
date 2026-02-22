from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SecretState(str, Enum):
    """Lifecycle state of a secret (stored in Redis metadata)."""

    AVAILABLE = "AVAILABLE"
    CLAIMED = "CLAIMED"


class DownloadReason(str, Enum):
    """Business outcome of a try_download attempt."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_TAKEN = "ALREADY_TAKEN"


@dataclass(frozen=True, slots=True)
class SecretRecord:
    """Full metadata stored in the Redis hash for a secret."""

    token: str
    file_id: str
    state: SecretState
    size_bytes: int
    filename: str | None
    ttl_seconds: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Outcome of a try_download attempt (e.g., returned by RedisStore Lua script)."""

    got_it: bool
    file_id: str | None
    reason: DownloadReason


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    """Returned to the client after a successful upload."""

    token: str
    expires_at: datetime