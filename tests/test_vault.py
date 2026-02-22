"""Unit tests for VaultService using fake in-memory stores.

The fakes here *are* the duck-typed store contracts.
No FastAPI, no Redis, no MinIO containers.
"""

from __future__ import annotations

import io
from typing import Iterator

from atomicvault.models import (
    DownloadReason,
    DownloadResult,
    SecretRecord,
)


# ── Fake Stores (these define the implicit contracts) ────────────────


class FakeMinioStore:
    """In-memory blob store. Contract: save, read, delete."""

    def __init__(self, *, call_log: list[str] | None = None) -> None:
        self.blobs: dict[str, bytes] = {}
        self.delete_calls: list[str] = []
        self.read_calls: list[str] = []
        self._call_log = call_log  # shared cross-store ordering log

    def save(self, file_id: str, data_stream: io.BytesIO, size_bytes: int) -> None:
        self.blobs[file_id] = data_stream.read()

    def read(self, file_id: str) -> Iterator[bytes]:
        self.read_calls.append(file_id)
        if self._call_log is not None:
            self._call_log.append("minio.read")
        data = self.blobs[file_id]
        return iter([data])

    def delete(self, file_id: str) -> None:
        self.delete_calls.append(file_id)
        self.blobs.pop(file_id, None)


class FakeRedisStore:
    """In-memory metadata store. Contract: save, try_claim, delete."""

    def __init__(self, *, call_log: list[str] | None = None) -> None:
        self.records: dict[str, SecretRecord] = {}
        self.delete_calls: list[tuple[str, str]] = []
        self._call_log = call_log  # shared cross-store ordering log

    def save(self, record: SecretRecord) -> None:
        self.records[record.token] = record

    def try_claim(self, token: str) -> DownloadResult:
        if self._call_log is not None:
            self._call_log.append("redis.try_claim")

        record = self.records.get(token)

        if record is None:
            return DownloadResult(
                got_it=False, file_id=None, reason=DownloadReason.NOT_FOUND
            )

        if record.state != "AVAILABLE":
            return DownloadResult(
                got_it=False, file_id=None, reason=DownloadReason.ALREADY_TAKEN
            )

        # Simulate atomic flip AVAILABLE → CLAIMED
        from dataclasses import replace

        self.records[token] = replace(record, state="CLAIMED")

        return DownloadResult(
            got_it=True, file_id=record.file_id, reason=DownloadReason.OK
        )

    def delete(self, token: str, file_id: str) -> None:
        self.delete_calls.append((token, file_id))
        self.records.pop(token, None)


# ── Helpers ──────────────────────────────────────────────────────────


def _upload_secret(vault, content: bytes = b"one-time secret",
                   filename: str = "file.bin", ttl: int = 60):
    """Shortcut: upload content and return (receipt, content)."""
    return vault.upload(io.BytesIO(content), filename, len(content), ttl=ttl), content


# ── Tests ────────────────────────────────────────────────────────────


class TestUpload:
    """upload stores blob first, then metadata. If Redis fails, MinIO is undone."""

    def test_upload_happy_path_minio_then_redis(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)

        content = b"top secret payload"
        stream = io.BytesIO(content)
        receipt = vault.upload(stream, "secret.txt", len(content), ttl=300)

        # Blob landed in MinIO
        assert len(minio.blobs) == 1

        # Metadata landed in Redis
        assert len(redis.records) == 1
        record = next(iter(redis.records.values()))
        assert record.token == receipt.token
        assert record.filename == "secret.txt"
        assert record.size_bytes == len(content)

    def test_upload_too_large_raises_and_does_not_call_stores(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)

        over_limit = 10 * 1024 * 1024 + 1  # 10 MB + 1 byte

        from atomicvault.errors import FileTooLargeError

        try:
            vault.upload(io.BytesIO(b"x"), "huge.bin", over_limit, ttl=300)
            assert False, "should have raised FileTooLargeError"
        except FileTooLargeError:
            pass

        # Neither store was touched
        assert len(minio.blobs) == 0
        assert len(redis.records) == 0

    def test_upload_redis_failure_triggers_minio_delete(self) -> None:
        class FailingRedisStore(FakeRedisStore):
            def save(self, record: SecretRecord) -> None:
                raise RuntimeError("Redis is down")

        redis = FailingRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService
        from atomicvault.errors import StorageError

        vault = VaultService(redis, minio)

        content = b"doomed payload"
        stream = io.BytesIO(content)

        try:
            vault.upload(stream, "doomed.txt", len(content), ttl=300)
        except StorageError:
            pass

        # MinIO blob should have been cleaned up (best-effort undo)
        assert len(minio.blobs) == 0
        assert len(minio.delete_calls) == 1


# ── Download Tests ───────────────────────────────────────────────────


class TestDownloadCoreBehavior:
    """Core: first claim gets stream, already-taken and not-found return None."""

    def test_returns_stream_on_first_claim(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, content = _upload_secret(vault)

        result, stream = vault.try_download(receipt.token)

        assert result.got_it is True
        assert result.reason == DownloadReason.OK
        assert stream is not None
        assert b"".join(stream) == content
        # MinIO read called exactly once
        assert len(minio.read_calls) == 1

    def test_already_taken_returns_no_stream_no_minio_read(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, _ = _upload_secret(vault)

        # First claim wins — do NOT consume stream (that triggers destroy)
        _, first_stream = vault.try_download(receipt.token)
        assert first_stream is not None

        minio.read_calls.clear()  # reset for second call

        # Second attempt — token is CLAIMED, not destroyed
        result, stream = vault.try_download(receipt.token)

        assert result.got_it is False
        assert result.reason == DownloadReason.ALREADY_TAKEN
        assert stream is None
        assert len(minio.read_calls) == 0  # no MinIO read on rejection

    def test_not_found_returns_no_stream_no_minio_read(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)

        result, stream = vault.try_download("nonexistent-token")

        assert result.got_it is False
        assert result.reason == DownloadReason.NOT_FOUND
        assert stream is None
        assert len(minio.read_calls) == 0


class TestDownloadOrdering:
    """Invariant: Redis claim always happens before MinIO read."""

    def test_claim_happens_before_minio_read(self) -> None:
        call_log: list[str] = []
        redis = FakeRedisStore(call_log=call_log)
        minio = FakeMinioStore(call_log=call_log)

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, _ = _upload_secret(vault)

        _, stream = vault.try_download(receipt.token)
        # Must consume stream to trigger minio.read (it's a generator)
        if stream is not None:
            for _ in stream:
                pass

        assert call_log.index("redis.try_claim") < call_log.index("minio.read")


class TestDownloadFailureModes:
    """Failure: MinIO read after claim → infra error + cleanup.
    Redis claim fails → infra error, no MinIO read."""

    def test_minio_read_fails_after_claim_triggers_cleanup(self) -> None:
        class FailingReadMinioStore(FakeMinioStore):
            def read(self, file_id: str) -> Iterator[bytes]:
                self.read_calls.append(file_id)
                raise OSError("MinIO is down")

        redis = FakeRedisStore()
        minio = FailingReadMinioStore()

        from atomicvault.vault import VaultService
        from atomicvault.errors import StorageError

        vault = VaultService(redis, minio)
        receipt, _ = _upload_secret(vault)

        try:
            vault.try_download(receipt.token)
            assert False, "should have raised StorageError"
        except StorageError:
            pass  # expected: minio read failed after claim

        # Key assertion: cleanup happened (destroy was called)
        assert len(minio.delete_calls) >= 1
        assert len(redis.delete_calls) >= 1

    def test_redis_claim_fails_no_minio_read(self) -> None:
        class FailingClaimRedisStore(FakeRedisStore):
            def try_claim(self, token: str) -> DownloadResult:
                raise ConnectionError("Redis unreachable")

        redis = FailingClaimRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, _ = _upload_secret(vault)

        try:
            vault.try_download(receipt.token)
            assert False, "should have raised"
        except ConnectionError:
            pass

        assert len(minio.read_calls) == 0


class TestDownloadConcurrency:
    """Simulate two callers: first wins, second loses."""

    def test_second_caller_loses(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, content = _upload_secret(vault)

        # First caller claims
        result1, stream1 = vault.try_download(receipt.token)
        assert result1.got_it is True
        assert stream1 is not None

        # Second caller tries
        result2, stream2 = vault.try_download(receipt.token)
        assert result2.got_it is False
        assert result2.reason == DownloadReason.ALREADY_TAKEN
        assert stream2 is None

        # Only winner triggered MinIO read
        assert len(minio.read_calls) == 1


class TestDownloadStreamingCleanup:
    """After stream is consumed (or closed early), destroy is called."""

    def test_stream_consumed_fully_triggers_cleanup(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        receipt, _ = _upload_secret(vault)

        _, stream = vault.try_download(receipt.token)
        assert stream is not None

        # Consume entire stream
        for _ in stream:
            pass

        # Cleanup should have been triggered
        assert len(minio.delete_calls) >= 1
        assert len(redis.delete_calls) >= 1

    def test_stream_closed_early_triggers_cleanup(self) -> None:
        # Use multi-chunk blob so we can stop mid-stream
        class MultiChunkMinioStore(FakeMinioStore):
            def read(self, file_id: str) -> Iterator[bytes]:
                self.read_calls.append(file_id)
                data = self.blobs[file_id]
                mid = len(data) // 2
                yield data[:mid]
                yield data[mid:]

        redis = FakeRedisStore()
        minio = MultiChunkMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)
        content = b"A" * 100  # enough for two chunks
        receipt = vault.upload(io.BytesIO(content), "big.bin", len(content), ttl=60)

        _, stream = vault.try_download(receipt.token)
        assert stream is not None

        # Read only the first chunk, then close the generator
        next(stream)
        stream.close()

        # Cleanup still invoked via finally
        assert len(minio.delete_calls) >= 1
        assert len(redis.delete_calls) >= 1


class TestDestroy:
    """destroy is idempotent — calling it twice doesn't crash."""

    def test_destroy_idempotent(self) -> None:
        redis = FakeRedisStore()
        minio = FakeMinioStore()

        from atomicvault.vault import VaultService

        vault = VaultService(redis, minio)

        content = b"ephemeral"
        receipt = vault.upload(io.BytesIO(content), "tmp.txt", len(content), ttl=120)

        record = next(iter(redis.records.values()))

        # Destroy once
        vault.destroy(record.token, record.file_id)

        # Destroy again — should not raise
        vault.destroy(record.token, record.file_id)

        # Both calls recorded
        assert len(redis.delete_calls) == 2
        assert len(minio.delete_calls) == 2
