"""AtomicVault HTTP Client library."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx

from atomicvault import settings
from atomicvault.crypto import decrypt_bytes_with_key, encrypt_bytes_with_key

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 60.0
_TIMEOUT = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)


class ClientError(Exception):
    """Raised for any HTTP or cryptographic errors encountered by the client."""


class AtomicVaultClient:

    def __init__(
        self,
        url: str | None = None,
        encrypt: bool | None = None,
        key_b64: str | None = None,
    ) -> None:
        self.url = url or settings.ATOMICVAULT_URL
        self.encrypt = encrypt if encrypt is not None else settings.ATOMICVAULT_CLIENT_ENCRYPT
        self.key_b64 = key_b64 or settings.ATOMICVAULT_CLIENT_KEY_B64

    def upload_file(self, path: Path, ttl: int = 300) -> str:
        if not path.exists():
            raise ClientError(f"File not found: {path}")
        if not path.is_file():
            raise ClientError(f"Not a file: {path}")

        fh = None
        try:
            if self.encrypt:
                if not self.key_b64:
                    raise ClientError("encryption enabled but no key set (use 'set key' or 'keygen')")
                try:
                    key = base64.b64decode(self.key_b64)
                except Exception:
                    raise ClientError("invalid base64 key string")
                    
                if len(key) != 32:
                    raise ClientError(f"key must be 32 bytes, got {len(key)}")
                    
                plaintext = path.read_bytes()
                ciphertext = encrypt_bytes_with_key(key, plaintext)
                upload_file = (path.name, io.BytesIO(ciphertext), "application/octet-stream")
            else:
                fh = open(path, "rb")
                upload_file = (path.name, fh, "application/octet-stream")

            try:
                resp = httpx.post(
                    f"{self.url}/secrets",
                    params={"ttl": ttl},
                    files={"file": upload_file},
                    timeout=_TIMEOUT,
                )
            except httpx.RequestError as exc:
                raise ClientError(f"HTTP request failed: {exc}") from exc

            if resp.status_code == 201:
                return resp.json()["token"]
            
            error_detail = self._extract_error(resp)
            raise ClientError(f"API Error ({resp.status_code}): {error_detail}")
        finally:
            if fh is not None:
                fh.close()

    def download_file(self, token: str, out_path: Path, inline_key_b64: str | None = None) -> None:
        """Download a secret and write it to `out_path`.
        
        Raises ClientError on failure.
        """
        is_encrypted = self.encrypt or bool(inline_key_b64)
        active_key_b64 = inline_key_b64 or self.key_b64
        endpoint = f"{self.url}/secrets/{token}"

        if is_encrypted:
            if not active_key_b64:
                raise ClientError("encryption enabled but no key provided. Pass key=<b64> with the get command)")
            
            try:
                key = base64.b64decode(active_key_b64)
                if len(key) != 32:
                    raise ClientError(f"key must be 32 bytes, got {len(key)}")
            except Exception:
                raise ClientError("invalid base64 key string")

            try:
                resp = httpx.get(endpoint, timeout=_TIMEOUT)
            except httpx.RequestError as exc:
                raise ClientError(f"{exc}") from exc

            if resp.status_code == 200:
                try:
                    plaintext = decrypt_bytes_with_key(key, resp.content)
                except ValueError as exc:
                    raise ClientError(f"decryption failed — {exc}") from exc
                out_path.write_bytes(plaintext)
                return
            
            error_detail = self._extract_error(resp)
            raise ClientError(f"({resp.status_code}): {error_detail}")
        else:
            try:
                with httpx.stream("GET", endpoint, timeout=_TIMEOUT) as resp:
                    if resp.status_code == 200:
                        with open(out_path, "wb") as fh:
                            for chunk in resp.iter_bytes():
                                fh.write(chunk)
                        return
                    resp.read()
                    error_detail = self._extract_error(resp)
                    raise ClientError(f"({resp.status_code}): {error_detail}")
            except httpx.RequestError as exc:
                raise ClientError(f"{exc}") from exc

    def _extract_error(self, resp: httpx.Response) -> str:
        try:
            return resp.json().get("detail", resp.text)
        except Exception:
            return resp.text
