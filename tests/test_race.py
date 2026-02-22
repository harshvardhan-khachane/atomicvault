"""E2E race-condition test — 50 concurrent downloads, exactly 1 winner.

Skip unless ATOMICVAULT_E2E=1. Assumes the server is already running.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

BASE_URL = os.environ.get("ATOMICVAULT_URL", "http://localhost:8000")

pytestmark = pytest.mark.skipif(
    os.environ.get("ATOMICVAULT_E2E") != "1",
    reason="set ATOMICVAULT_E2E=1 to run integration tests",
)

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
PAYLOAD = b"race condition payload"
CONCURRENCY = 50


def _upload_sync() -> str:
    """Upload a payload and return the token (sync helper)."""
    with httpx.Client() as client:
        resp = client.post(
            f"{BASE_URL}/secrets",
            params={"ttl": 300},
            files={"file": ("race.bin", PAYLOAD, "application/octet-stream")},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 201, f"upload failed: {resp.status_code} {resp.text}"
        return resp.json()["token"]


async def _download(
    client: httpx.AsyncClient, token: str
) -> int:
    """Attempt a download, return the HTTP status code."""
    resp = await client.get(
        f"{BASE_URL}/secrets/{token}", timeout=TIMEOUT
    )
    return resp.status_code


async def _race(token: str) -> list[int]:
    """Fire CONCURRENCY downloads concurrently and collect status codes."""
    async with httpx.AsyncClient() as client:
        tasks = [_download(client, token) for _ in range(CONCURRENCY)]
        return await asyncio.gather(*tasks)


def test_exactly_one_winner() -> None:
    """50 concurrent GET /secrets/{token} → exactly 1×200, rest are 410."""
    token = _upload_sync()
    codes = asyncio.run(_race(token))

    wins = codes.count(200)
    gone = codes.count(410)
    others = [c for c in codes if c not in (200, 410)]

    assert wins == 1, (
        f"expected exactly 1 winner (200), got {wins}. "
        f"codes breakdown: 200×{wins}, 410×{gone}, others={others}"
    )
    assert gone == CONCURRENCY - 1, (
        f"expected {CONCURRENCY - 1}×410, got {gone}. "
        f"codes breakdown: 200×{wins}, 410×{gone}, others={others}"
    )
    assert others == [], (
        f"unexpected status codes: {others}. "
        f"codes breakdown: 200×{wins}, 410×{gone}, others={others}"
    )
