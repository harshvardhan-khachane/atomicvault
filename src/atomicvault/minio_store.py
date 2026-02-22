from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from typing import BinaryIO

logger = logging.getLogger(__name__)


class MinioStore:
    """All MinIO operations for AtomicVault secrets."""

    def __init__(self, client, bucket: str, *, chunk_size: int = 64 * 1024) -> None:
        self._client = client
        self._bucket = bucket
        self._chunk_size = chunk_size

    def save(self, file_id: str, data_stream: BinaryIO, size_bytes: int) -> None:
        """Upload a blob. Passes the stream directly — no pre-buffering."""
        if size_bytes < 0:
            raise ValueError(f"size_bytes must be >= 0, got {size_bytes}")

        self._client.put_object(self._bucket, file_id, data_stream, size_bytes)

    def read(self, file_id: str) -> Iterator[bytes]:
        """Stream an object in chunks. Always cleans up the response."""
        resp = self._client.get_object(self._bucket, file_id)
        try:
            yield from resp.stream(self._chunk_size)
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                if hasattr(resp, "release_conn"):
                    resp.release_conn()
            except Exception:
                pass

    def delete(self, file_id: str) -> None:
        """Remove an object. Idempotent — swallows not-found errors."""
        try:
            self._client.remove_object(self._bucket, file_id)
        except Exception as exc:
            exc_name = type(exc).__name__
            exc_msg = str(exc)
            if "NoSuchKey" in exc_name or "NotFound" in exc_name \
               or "NoSuchKey" in exc_msg or "NotFound" in exc_msg:
                logger.debug("delete ignored (already gone) file_id=%s", file_id)
                return
            raise

    def list_old_files(self, older_than: datetime) -> list[str]:
        """Return file_ids of objects older than the given threshold."""
        old: list[str] = []
        for obj in self._client.list_objects(self._bucket, recursive=True):
            name = getattr(obj, "object_name", None) or getattr(obj, "name", None)
            if name is not None and obj.last_modified < older_than:
                old.append(name)
        return old
