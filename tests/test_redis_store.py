"""Unit tests for RedisStore using fakeredis.

Focused on Redis semantics: hashes, TTLs, Lua atomic claim, reverse keys.
No MinIO, no FastAPI, no VaultService.
"""

from __future__ import annotations

from datetime import datetime, timezone

import fakeredis
import pytest

from atomicvault.models import (
    DownloadReason,
    SecretRecord,
    SecretState,
)
from atomicvault.redis_store import RedisStore


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(version=(7,), decode_responses=False)


@pytest.fixture()
def store(redis_client: fakeredis.FakeRedis) -> RedisStore:
    return RedisStore(redis_client)


def _make_record(
    token: str = "tok123",
    file_id: str = "file456",
    size_bytes: int = 1024,
    filename: str | None = "secret.txt",
    ttl_seconds: int = 300,
) -> SecretRecord:
    return SecretRecord(
        token=token,
        file_id=file_id,
        state=SecretState.AVAILABLE,
        size_bytes=size_bytes,
        filename=filename,
        ttl_seconds=ttl_seconds,
        created_at=datetime.now(timezone.utc),
    )


# ── save() tests ─────────────────────────────────────────────────────


class TestSave:
    """save() must set a Redis hash, EXPIRE it, and create a reverse key."""

    def test_save_creates_hash_with_correct_fields(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record()
        store.save(record)

        key = f"secret:{record.token}"
        assert redis_client.exists(key) == 1

        # Verify individual hash fields
        assert redis_client.hget(key, "state") == b"AVAILABLE"
        assert redis_client.hget(key, "file_id") == record.file_id.encode()
        assert redis_client.hget(key, "size_bytes") == str(record.size_bytes).encode()
        assert redis_client.hget(key, "filename") == record.filename.encode()
        assert redis_client.hget(key, "created_at") is not None

    def test_save_sets_ttl_on_secret_key(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record(ttl_seconds=600)
        store.save(record)

        ttl = redis_client.ttl(f"secret:{record.token}")
        assert 0 < ttl <= 600

    def test_save_creates_reverse_key_with_extended_ttl(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record(ttl_seconds=300)
        store.save(record)

        reverse_key = f"file:{record.file_id}"
        assert redis_client.exists(reverse_key) == 1
        assert redis_client.get(reverse_key) == record.token.encode()

        # Reverse key TTL should be ttl + 60 (operational safety window)
        reverse_ttl = redis_client.ttl(reverse_key)
        assert 300 < reverse_ttl <= 360

    def test_save_handles_none_filename(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record(filename=None)
        store.save(record)

        key = f"secret:{record.token}"
        # filename should be stored as empty string or omitted
        val = redis_client.hget(key, "filename")
        assert val is None or val == b""


# ── try_claim() tests ────────────────────────────────────────────────


class TestTryClaim:
    """try_claim uses Lua script for atomic AVAILABLE → CLAIMED flip."""

    def test_claim_available_returns_ok_and_flips_state(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record()
        store.save(record)

        result = store.try_claim(record.token)

        assert result.got_it is True
        assert result.file_id == record.file_id
        assert result.reason == DownloadReason.OK

        # State flipped in Redis
        state = redis_client.hget(f"secret:{record.token}", "state")
        assert state == b"CLAIMED"

    def test_claim_already_taken_returns_already_taken(
        self, store: RedisStore
    ) -> None:
        record = _make_record()
        store.save(record)

        # First claim wins
        store.try_claim(record.token)

        # Second claim loses
        result = store.try_claim(record.token)
        assert result.got_it is False
        assert result.file_id is None
        assert result.reason == DownloadReason.ALREADY_TAKEN

    def test_claim_missing_key_returns_not_found(
        self, store: RedisStore
    ) -> None:
        result = store.try_claim("nonexistent-token")

        assert result.got_it is False
        assert result.file_id is None
        assert result.reason == DownloadReason.NOT_FOUND

    def test_atomicity_exactly_one_winner(
        self, store: RedisStore
    ) -> None:
        record = _make_record()
        store.save(record)

        results = [store.try_claim(record.token) for _ in range(50)]

        winners = [r for r in results if r.got_it]
        losers = [r for r in results if not r.got_it]

        assert len(winners) == 1
        assert len(losers) == 49
        assert all(r.reason == DownloadReason.ALREADY_TAKEN for r in losers)


# ── delete() tests ───────────────────────────────────────────────────


class TestDelete:
    """delete() removes both the secret hash and the reverse key."""

    def test_delete_removes_both_keys(
        self, store: RedisStore, redis_client: fakeredis.FakeRedis
    ) -> None:
        record = _make_record()
        store.save(record)

        store.delete(record.token, record.file_id)

        assert redis_client.exists(f"secret:{record.token}") == 0
        assert redis_client.exists(f"file:{record.file_id}") == 0

    def test_delete_idempotent(self, store: RedisStore) -> None:
        record = _make_record()
        store.save(record)

        # Delete twice — should not raise
        store.delete(record.token, record.file_id)
        store.delete(record.token, record.file_id)


# ── exists_by_file_id() tests ───────────────────────────────────────


class TestExistsByFileId:
    """exists_by_file_id checks reverse key — used by cleanup janitor."""

    def test_returns_true_when_reverse_key_exists(
        self, store: RedisStore
    ) -> None:
        record = _make_record()
        store.save(record)

        assert store.exists_by_file_id(record.file_id) is True

    def test_returns_false_when_reverse_key_missing(
        self, store: RedisStore
    ) -> None:
        assert store.exists_by_file_id("nonexistent-file-id") is False

    def test_returns_false_after_delete(
        self, store: RedisStore
    ) -> None:
        record = _make_record()
        store.save(record)
        store.delete(record.token, record.file_id)

        assert store.exists_by_file_id(record.file_id) is False
