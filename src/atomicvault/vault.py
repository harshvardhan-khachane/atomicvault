from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, BinaryIO

from atomicvault import settings
from atomicvault.models import (
    DownloadResult,
    SecretRecord,
    SecretState,
    UploadReceipt,
)

if TYPE_CHECKING:
    from typing import Iterator

from atomicvault.errors import FileTooLargeError, InvalidTTLError, StorageError

logger = logging.getLogger(__name__)


class VaultService:
    """The entire brain. Routes call this; this calls stores."""

    def __init__(  # noqa: ANN001
        self,
        redis_store,
        minio_store,
        *,
        max_file_size: int = 10 * 1024 * 1024,
    ) -> None:
        self._redis = redis_store
        self._minio = minio_store
        self._max_file_size = max_file_size

    def upload(
        self,
        data_stream: BinaryIO,
        filename: str | None,
        size_bytes: int,
        *,
        ttl: int,
    ) -> UploadReceipt:
        if size_bytes > self._max_file_size:
            raise FileTooLargeError(size_bytes, self._max_file_size)

        if ttl <= 0 or ttl > settings.MAX_TTL_SECONDS:
            raise InvalidTTLError(ttl, settings.MAX_TTL_SECONDS)

        token = uuid.uuid4().hex
        file_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)

        self._minio.save(file_id, data_stream, size_bytes)
        
        record = SecretRecord(
            token=token,
            file_id=file_id,
            state=SecretState.AVAILABLE,
            size_bytes=size_bytes,
            filename=filename,
            ttl_seconds=ttl,
            created_at=now,
        )

        try:
            self._redis.save(record)
        except Exception as exc:
            logger.error(
                "redis save failed, undoing minio save "
                "token=%s file_id=%s error=%s",
                token,
                file_id,
                exc,
            )
            try:
                self._minio.delete(file_id)
            except Exception:
                logger.warning(
                    "minio undo-delete also failed (janitor will clean) "
                    "file_id=%s",
                    file_id,
                )
            raise StorageError(
                f"redis save failed during upload for token={token}"
            ) from exc

        expires_at = now + timedelta(seconds=ttl)
        return UploadReceipt(token=token, expires_at=expires_at)

    def try_download(self, token: str) -> tuple[DownloadResult, Iterator[bytes] | None]:
        result = self._redis.try_claim(token)

        if not result.got_it:
            return result, None

        file_id = result.file_id

        if file_id is None:
            raise StorageError(
                f"redis returned got_it=True but file_id is None "
                f"for token={token}"
            )

        # Claim succeeded — attempt MinIO read
        try:
            raw_stream = self._minio.read(file_id)
        except Exception as exc:
            # Secret is burned — one-time guarantee means we can't un-claim.
            logger.error(
                "minio read failed after claim, destroying "
                "token=%s file_id=%s error=%s",
                token,
                file_id,
                exc,
            )
            self.destroy(token, file_id)
            raise StorageError(
                f"minio read failed after claim for token={token}"
            ) from exc

        return result, self._stream_then_destroy(raw_stream, token, file_id)

    def _stream_then_destroy(
        self, raw_stream: Iterator[bytes], token: str, file_id: str
    ) -> Iterator[bytes]:
        try:
            yield from raw_stream
        finally:
            self.destroy(token, file_id)

    def destroy(self, token: str, file_id: str) -> None:
        try:
            self._redis.delete(token, file_id)
        except Exception:
            logger.warning("redis delete failed (janitor will clean) ""token=%s file_id=%s", token, file_id )

        try:
            self._minio.delete(file_id)
        except Exception:
            logger.warning(
                "minio delete failed (janitor will clean) "
                "file_id=%s",
                file_id,
            )
