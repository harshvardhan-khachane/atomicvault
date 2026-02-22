"""AES-GCM client-side encryption for AtomicVault.

Wire format:  b"AV1" + nonce(12) + ciphertext_with_tag
Key source:   ATOMICVAULT_CLIENT_KEY_B64 env var (base64 of 32-byte key)
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = b"AV1"
_NONCE_LEN = 12
_KEY_LEN = 32


def encryption_enabled() -> bool:
    """Return True when the user opted into client-side encryption."""
    return os.environ.get("ATOMICVAULT_CLIENT_ENCRYPT", "0") == "1"


def encrypt_bytes_with_key(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt *plaintext* with an explicit 32-byte *key*.

    Returns ``b"AV1" + nonce(12) + ciphertext_with_tag``.
    """
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return _PREFIX + nonce + ct


def decrypt_bytes_with_key(key: bytes, blob: bytes) -> bytes:
    """Decrypt an ``encrypt_bytes`` blob with an explicit 32-byte *key*.

    Raises ValueError on bad format or authentication failure.
    """
    prefix_len = len(_PREFIX)

    if len(blob) < prefix_len + _NONCE_LEN + 1:
        raise ValueError("blob too short to be a valid AV1 envelope")

    if blob[:prefix_len] != _PREFIX:
        raise ValueError(
            f"invalid prefix: expected {_PREFIX!r}, "
            f"got {blob[:prefix_len]!r}"
        )

    nonce = blob[prefix_len : prefix_len + _NONCE_LEN]
    ct = blob[prefix_len + _NONCE_LEN :]

    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as exc:
        raise ValueError(f"decryption failed: {exc}") from exc
