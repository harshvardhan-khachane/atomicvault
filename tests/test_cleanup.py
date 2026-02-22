"""Unit tests for the cleanup janitor using fakes.

No Redis, no MinIO, no Docker. Uses pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


# ── Fakes ────────────────────────────────────────────────────────────


class FakeMinioStore:
    """Records list_old_files results and delete calls."""

    def __init__(
        self,
        old_files: list[str] | None = None,
        *,
        delete_raises: dict[str, Exception] | None = None,
    ) -> None:
        self._old_files = old_files or []
        self._delete_raises = delete_raises or {}
        self.delete_calls: list[str] = []

    def list_old_files(self, older_than: datetime) -> list[str]:
        return list(self._old_files)

    def delete(self, file_id: str) -> None:
        self.delete_calls.append(file_id)
        if file_id in self._delete_raises:
            raise self._delete_raises[file_id]


class FakeRedisStore:
    """Returns pre-configured exists_by_file_id results."""

    def __init__(self, existing_file_ids: set[str] | None = None) -> None:
        self._existing = existing_file_ids or set()

    def exists_by_file_id(self, file_id: str) -> bool:
        return file_id in self._existing


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_janitor_deletes_orphans_once() -> None:
    minio = FakeMinioStore(old_files=["a", "b", "c"])
    redis = FakeRedisStore(existing_file_ids={"b"})  # "b" still referenced

    from atomicvault.cleanup import janitor_loop

    stats = await janitor_loop(
        redis_store=redis,
        minio_store=minio,
        older_than_seconds=3600,
        interval_seconds=0,
        once=True,
    )

    assert sorted(minio.delete_calls) == ["a", "c"]
    assert stats["scanned"] == 3
    assert stats["deleted"] == 2


@pytest.mark.asyncio
async def test_janitor_no_deletes_when_all_referenced() -> None:
    minio = FakeMinioStore(old_files=["x", "y"])
    redis = FakeRedisStore(existing_file_ids={"x", "y"})

    from atomicvault.cleanup import janitor_loop

    stats = await janitor_loop(
        redis_store=redis,
        minio_store=minio,
        older_than_seconds=3600,
        interval_seconds=0,
        once=True,
    )

    assert minio.delete_calls == []
    assert stats["scanned"] == 2
    assert stats["deleted"] == 0


@pytest.mark.asyncio
async def test_janitor_handles_delete_exceptions_and_continues() -> None:
    minio = FakeMinioStore(
        old_files=["a", "b"],
        delete_raises={"a": OSError("MinIO down")},
    )
    redis = FakeRedisStore(existing_file_ids=set())  # both are orphans

    from atomicvault.cleanup import janitor_loop

    stats = await janitor_loop(
        redis_store=redis,
        minio_store=minio,
        older_than_seconds=3600,
        interval_seconds=0,
        once=True,
    )

    # Both deletes attempted
    assert sorted(minio.delete_calls) == ["a", "b"]
    # Only "b" succeeded
    assert stats["scanned"] == 2
    assert stats["deleted"] == 1


@pytest.mark.asyncio
async def test_janitor_loop_cancellable() -> None:
    minio = FakeMinioStore(old_files=[])
    redis = FakeRedisStore()

    from atomicvault.cleanup import janitor_loop

    task = asyncio.create_task(
        janitor_loop(
            redis_store=redis,
            minio_store=minio,
            older_than_seconds=3600,
            interval_seconds=0.01,
            once=False,
        )
    )

    # Let it tick a few times
    await asyncio.sleep(0.05)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
