from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from atomicvault.models import DownloadReason, DownloadResult, SecretRecord

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_CLAIM_SCRIPT = (_LUA_DIR / "try_download.lua").read_text()

# Operational safety window: reverse key outlives primary key by this many
# seconds, preventing the janitor from deleting a file mid-stream.
_REVERSE_KEY_BUFFER = 60


class RedisStore:
    """All Redis operations for AtomicVault secrets."""

    def __init__(self, client: Redis) -> None:
        self._r = client
        self._claim_script = self._r.register_script(_CLAIM_SCRIPT)

    def save(self, record: SecretRecord) -> None:
        secret_key = f"secret:{record.token}"
        reverse_key = f"file:{record.file_id}"

        mapping: dict[str, str] = {
            "state": record.state.value,
            "file_id": record.file_id,
            "size_bytes": str(record.size_bytes),
            "created_at": record.created_at.isoformat(),
        }

        if record.filename is not None:
            mapping["filename"] = record.filename

        pipe = self._r.pipeline()
        pipe.hset(secret_key, mapping=mapping)
        pipe.expire(secret_key, record.ttl_seconds)
        pipe.setex(reverse_key, record.ttl_seconds + _REVERSE_KEY_BUFFER, record.token)
        pipe.execute()

    def try_claim(self, token: str) -> DownloadResult:
        secret_key = f"secret:{token}"
        raw = self._claim_script(keys=[secret_key])

        # Lua returns [int, string]: [1, file_id] or [0, reason]
        got_it = raw[0] == 1
        payload = raw[1].decode() if isinstance(raw[1], bytes) else raw[1]

        if got_it:
            return DownloadResult(
                got_it=True,
                file_id=payload,
                reason=DownloadReason.OK,
            )

        return DownloadResult(
            got_it=False,
            file_id=None,
            reason=DownloadReason(payload),
        )

    def delete(self, token: str, file_id: str) -> None:
        self._r.delete(f"secret:{token}", f"file:{file_id}")

    def exists_by_file_id(self, file_id: str) -> bool:
        return bool(self._r.exists(f"file:{file_id}"))
