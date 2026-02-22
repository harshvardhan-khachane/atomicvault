"""E2E integration tests — upload → download → gone.

Skip unless ATOMICVAULT_E2E=1. Assumes the server is already running.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ATOMICVAULT_URL", "http://localhost:8000")

pytestmark = pytest.mark.skipif(
    os.environ.get("ATOMICVAULT_E2E") != "1",
    reason="set ATOMICVAULT_E2E=1 to run integration tests",
)

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
PAYLOAD = b"hello atomicvault integration test"


def _upload(client: httpx.Client) -> str:
    """Upload a small payload and return the token."""
    resp = client.post(
        f"{BASE_URL}/secrets",
        params={"ttl": 300},
        files={"file": ("test.bin", PAYLOAD, "application/octet-stream")},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 201, f"upload failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert "token" in data
    assert "expires_at" in data
    return data["token"]


def test_upload_download_gone() -> None:
    """Upload → first GET returns 200 with correct body → second GET returns 410."""
    with httpx.Client() as client:
        token = _upload(client)

        # ── First download — should succeed ───────────────────
        resp = client.get(f"{BASE_URL}/secrets/{token}", timeout=TIMEOUT)
        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )
        assert resp.content == PAYLOAD, (
            f"body mismatch: got {len(resp.content)} bytes"
        )

        # ── Second download — should be gone ──────────────────
        resp = client.get(f"{BASE_URL}/secrets/{token}", timeout=TIMEOUT)
        assert resp.status_code == 410, (
            f"expected 410 Gone, got {resp.status_code}: {resp.text}"
        )


def test_not_found() -> None:
    """GET with a bogus token returns 404."""
    with httpx.Client() as client:
        resp = client.get(
            f"{BASE_URL}/secrets/does_not_exist_token",
            timeout=TIMEOUT,
        )
        assert resp.status_code == 404, (
            f"expected 404, got {resp.status_code}: {resp.text}"
        )


def test_upload_returns_201() -> None:
    """POST /secrets returns 201 with token and expires_at."""
    with httpx.Client() as client:
        token = _upload(client)
        assert len(token) > 0
