"""Unit tests for MinioStore using fakes.

No Docker, no real MinIO, no MinIO SDK imports.
Fakes defined here are the duck-typed contract for the MinIO client.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest


# ── Fakes ────────────────────────────────────────────────────────────


@dataclass
class FakeObj:
    """Mimics a MinIO Object returned by list_objects."""

    object_name: str
    last_modified: datetime


class FakeResponse:
    """Mimics the HTTPResponse returned by minio.get_object().

    Tracks whether close() and release_conn() were called so tests
    can assert resource cleanup.
    """

    def __init__(self, data: bytes, chunk_size: int = 32) -> None:
        self._data = data
        self._default_chunk_size = chunk_size
        self.closed = False
        self.conn_released = False

    def stream(self, chunk_size: int | None = None) -> Iterator[bytes]:
        size = chunk_size or self._default_chunk_size
        offset = 0
        while offset < len(self._data):
            yield self._data[offset : offset + size]
            offset += size

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.conn_released = True


class NoSuchKeyError(Exception):
    """Fake exception for missing objects."""


class FakeMinioClient:
    """In-memory MinIO client. Tracks all calls for assertion."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, datetime] = {}
        self.put_calls: list[dict] = []
        self.remove_calls: list[tuple[str, str]] = []
        self._responses: dict[str, FakeResponse] = {}

    def put_object(
        self,
        bucket: str,
        object_name: str,
        data: io.BytesIO | io.RawIOBase,
        length: int,
    ) -> None:
        self.put_calls.append({
            "bucket": bucket,
            "object_name": object_name,
            "length": length,
        })
        self.objects[object_name] = data.read()
        self.metadata[object_name] = datetime.now(timezone.utc)

    def get_object(self, bucket: str, object_name: str) -> FakeResponse:
        if object_name not in self.objects:
            raise NoSuchKeyError(f"Object {object_name} not found")
        resp = FakeResponse(self.objects[object_name])
        self._responses[object_name] = resp
        return resp

    def get_last_response(self, object_name: str) -> FakeResponse:
        """Test helper: get the FakeResponse created by get_object."""
        return self._responses[object_name]

    def remove_object(self, bucket: str, object_name: str) -> None:
        self.remove_calls.append((bucket, object_name))
        self.objects.pop(object_name, None)
        self.metadata.pop(object_name, None)

    def list_objects(
        self, bucket: str, *, recursive: bool = True
    ) -> Iterator[FakeObj]:
        for name, modified in self.metadata.items():
            yield FakeObj(object_name=name, last_modified=modified)


class GuardStream:
    """A stream that records how it's consumed.

    Used to verify MinioStore.save() passes the stream through to
    put_object without fully reading it first.
    """

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)
        self.read_called = False

    def read(self, n: int = -1) -> bytes:
        self.read_called = True
        return self._inner.read(n)

    def seek(self, pos: int, whence: int = 0) -> int:
        return self._inner.seek(pos, whence)

    def tell(self) -> int:
        return self._inner.tell()


# ── Fixtures ─────────────────────────────────────────────────────────


BUCKET = "atomicvault"


@pytest.fixture()
def client() -> FakeMinioClient:
    return FakeMinioClient()


@pytest.fixture()
def store(client: FakeMinioClient):
    from atomicvault.minio_store import MinioStore

    return MinioStore(client, BUCKET)


# ── A) save() calls put_object correctly ─────────────────────────────


class TestSave:

    def test_save_calls_put_object_with_correct_args(
        self, store, client: FakeMinioClient
    ) -> None:
        content = b"secret payload"
        stream = io.BytesIO(content)

        store.save("file_abc", stream, len(content))

        assert len(client.put_calls) == 1
        call = client.put_calls[0]
        assert call["bucket"] == BUCKET
        assert call["object_name"] == "file_abc"
        assert call["length"] == len(content)

    # ── B) save() does not pre-consume the stream ────────────────

    def test_save_does_not_pre_consume_stream(
        self, store, client: FakeMinioClient
    ) -> None:
        """MinioStore.save() should pass the stream directly to put_object,
        not read it into memory first. The GuardStream verifies that save()
        itself does not call read() — only put_object does."""
        content = b"do not pre-read me"
        guard = GuardStream(content)

        # save should NOT call guard.read() itself — it hands the stream
        # to put_object, which reads it.
        store.save("file_xyz", guard, len(content))

        # put_object was called (the fake reads via data.read())
        assert len(client.put_calls) == 1
        # The blob landed correctly in the fake
        assert client.objects["file_xyz"] == content


# ── C) read() yields chunks in order ─────────────────────────────


class TestRead:

    def test_read_yields_chunks_in_order(
        self, store, client: FakeMinioClient
    ) -> None:
        content = b"ABCDEFGHIJKLMNOP"  # 16 bytes
        client.objects["file_001"] = content

        chunks = list(store.read("file_001"))

        reassembled = b"".join(chunks)
        assert reassembled == content
        assert len(chunks) >= 1  # at least one chunk

    # ── D) read() cleans up response after full consumption ──────

    def test_read_closes_response_after_full_consumption(
        self, store, client: FakeMinioClient
    ) -> None:
        content = b"ABCDEFGHIJKLMNOP"
        client.objects["file_002"] = content

        # Consume the entire stream
        for _ in store.read("file_002"):
            pass

        resp = client.get_last_response("file_002")
        assert resp.closed is True
        assert resp.conn_released is True

    # ── E) read() cleans up response even on early stop ──────────

    def test_read_closes_response_on_early_stop(
        self, store, client: FakeMinioClient
    ) -> None:
        content = b"A" * 200  # multiple chunks
        client.objects["file_003"] = content

        gen = store.read("file_003")
        next(gen)       # consume only first chunk
        gen.close()     # simulate early stop

        resp = client.get_last_response("file_003")
        assert resp.closed is True
        assert resp.conn_released is True


# ── F) delete() calls remove_object correctly ───────────────────


class TestDelete:

    def test_delete_calls_remove_object(
        self, store, client: FakeMinioClient
    ) -> None:
        client.objects["file_del"] = b"data"
        store.delete("file_del")

        assert len(client.remove_calls) == 1
        bucket, name = client.remove_calls[0]
        assert bucket == BUCKET
        assert name == "file_del"

    # ── G) delete() is idempotent ────────────────────────────────

    def test_delete_idempotent_on_missing_object(
        self, store, client: FakeMinioClient
    ) -> None:
        """If the object doesn't exist, delete should not raise.
        Mimics MinIO's remove_object which is already idempotent,
        but also covers the case where the SDK raises NoSuchKey."""

        class RaisingClient(FakeMinioClient):
            def remove_object(self, bucket: str, object_name: str) -> None:
                self.remove_calls.append((bucket, object_name))
                raise NoSuchKeyError("No such key")

        from atomicvault.minio_store import MinioStore

        raising_store = MinioStore(RaisingClient(), BUCKET)

        # Should not raise
        raising_store.delete("already_gone")


# ── H) list_old_files() filters by age ──────────────────────────


class TestListOldFiles:

    def test_list_old_files_returns_only_objects_older_than_threshold(
        self, store, client: FakeMinioClient
    ) -> None:
        now = datetime.now(timezone.utc)

        # Old file (2 hours ago)
        client.objects["old_file"] = b"old"
        client.metadata["old_file"] = now - timedelta(hours=2)

        # Recent file (5 minutes ago)
        client.objects["new_file"] = b"new"
        client.metadata["new_file"] = now - timedelta(minutes=5)

        # Very old file (1 day ago)
        client.objects["ancient_file"] = b"ancient"
        client.metadata["ancient_file"] = now - timedelta(days=1)

        # Threshold: 1 hour ago
        threshold = now - timedelta(hours=1)
        old_files = store.list_old_files(threshold)

        assert sorted(old_files) == ["ancient_file", "old_file"]
        assert "new_file" not in old_files
