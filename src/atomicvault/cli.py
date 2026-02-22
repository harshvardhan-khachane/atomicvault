"""AtomicVault CLI — Typer + httpx thin client."""

from __future__ import annotations

import base64
import io
import os
import shlex
import sys
from pathlib import Path

import httpx
import typer

from atomicvault.crypto import encrypt_bytes_with_key, decrypt_bytes_with_key
from atomicvault import settings
# from atomicvault.crypto import decrypt_bytes_with_key
# from atomicvault.crypto import encrypt_bytes_with_key
# from atomicvault.crypto import decrypt_bytes

app = typer.Typer(name="atomicvault", add_completion=False, invoke_without_command=True)

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 60.0
_TIMEOUT = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)


def _base_url() -> str:
    return settings.ATOMICVAULT_URL


def _encrypt_enabled() -> bool:
    return settings.ATOMICVAULT_CLIENT_ENCRYPT

_BANNER = (
    "AtomicVault interactive shell\n"
    "Type 'help' for available commands, 'exit' to quit."
)

_HELP_TEXT = """\
commands:
  help                  show this message
  exit / quit           leave the REPL
  set url <base_url>    set session base URL
  set encrypt on|off    toggle client-side encryption
  set key <b64>         set encryption key (base64)
  keygen                generate a random 32-byte key (sets it too)
  put <path> [ttl=300]  upload a file
  get <token> out=<path> [key=<b64>] download a secret\
"""


class _Session:
    """Mutable session state for the REPL."""

    def __init__(self) -> None:
        self.url: str = _base_url()
        self.encrypt: bool = _encrypt_enabled()
        self.key_b64: str = settings.ATOMICVAULT_CLIENT_KEY_B64


def _parse_kv(parts: list[str]) -> dict[str, str]:
    """Extract key=value pairs from token list."""
    kv: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k] = v
    return kv


def _repl_put(session: _Session, parts: list[str]) -> None:
    if not parts:
        print("usage: put <path> [ttl=300]")
        return

    path = Path(parts[0])
    if not path.exists():
        print(f"error: {path} does not exist")
        return
    if not path.is_file():
        print(f"error: {path} is not a file")
        return

    kv = _parse_kv(parts[1:])
    ttl = int(kv.get("ttl", "300"))

    fh = None  # file handle for non-encrypted path
    if session.encrypt:
        if not session.key_b64:
            print("error: encryption enabled but no key set (use 'set key' or 'keygen')")
            return
        key = base64.b64decode(session.key_b64)
        plaintext = path.read_bytes()
        ciphertext = encrypt_bytes_with_key(key, plaintext)
        upload_file = (path.name, io.BytesIO(ciphertext), "application/octet-stream")
    else:
        fh = open(path, "rb")  # noqa: SIM115
        upload_file = (path.name, fh, "application/octet-stream")

    try:
        resp = httpx.post(
            f"{session.url}/secrets",
            params={"ttl": ttl},
            files={"file": upload_file},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as exc:
        print(f"error: {exc}")
        return
    finally:
        if fh is not None:
            fh.close()

    if resp.status_code == 201:
        print(resp.json()["token"])
    else:
        _print_resp_error(resp)


def _repl_get(session: _Session, parts: list[str]) -> None:
    if not parts:
        print("usage: get <token> out=<path> [key=<b64>]")
        return

    token = parts[0]
    kv = _parse_kv(parts[1:])
    out_str = kv.get("out")
    if not out_str:
        print("error: out=<path> is required")
        return
    out = Path(out_str)
    
    inline_key = kv.get("key")
    is_encrypted = session.encrypt or bool(inline_key)
    active_key_b64 = inline_key

    endpoint = f"{session.url}/secrets/{token}"

    if is_encrypted:
        if not active_key_b64:
            print("error: encryption enabled but no key provided. Pass key=<b64> with the get command)")
            return
        try:
            resp = httpx.get(endpoint, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            print(f"error: {exc}")
            return

        if resp.status_code == 200:
            try:
                key = base64.b64decode(active_key_b64)
                if len(key) != 32:
                    print(f"error: key must be 32 bytes, got {len(key)}")
                    return
            except Exception:
                print("error: invalid base64 key")
                return
                
            try:
                plaintext = decrypt_bytes_with_key(key, resp.content)
            except ValueError as exc:
                print(f"error: decryption failed — {exc}")
                return
            out.write_bytes(plaintext)
            print(f"saved {out}")
        else:
            _print_resp_error(resp)
    else:
        try:
            with httpx.stream("GET", endpoint, timeout=_TIMEOUT) as resp:
                if resp.status_code == 200:
                    with open(out, "wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                    print(f"saved {out}") 
                    return
                resp.read()
        except httpx.RequestError as exc:
            print(f"error: {exc}")
            return
        _print_resp_error(resp)  # type: ignore[possibly-unbound]


def _repl_set(session: _Session, parts: list[str]) -> None:
    if len(parts) < 2:
        print("usage: set url|encrypt|key <value>")
        return
    prop, value = parts[0], parts[1]
    if prop == "url":
        session.url = value
        print(f"url = {session.url}")
    elif prop == "encrypt":
        if value in ("on", "1", "true"):
            session.encrypt = True
        elif value in ("off", "0", "false"):
            session.encrypt = False
        else:
            print("error: use 'on' or 'off'")
            return
        print(f"encrypt = {'on' if session.encrypt else 'off'}")
    elif prop == "key":
        try:
            raw = base64.b64decode(value)
        except Exception:
            print("error: invalid base64")
            return
        if len(raw) != 32:
            print(f"error: key must be 32 bytes, got {len(raw)}")
            return
        session.key_b64 = value
        print("key set")
    else:
        print(f"error: unknown property '{prop}'")


def _repl_keygen(session: _Session) -> None:
    key = os.urandom(32)
    session.key_b64 = base64.b64encode(key).decode()
    print(session.key_b64)


def _print_resp_error(resp: httpx.Response) -> None:
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    print(f"error ({resp.status_code}): {detail}")


def _repl() -> None:
    """Run an interactive REPL loop."""
    print(_BANNER)
    session = _Session()

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"error: {exc}")
            continue

        cmd, args = parts[0].lower(), parts[1:]

        if cmd == "help":
            print(_HELP_TEXT)
        elif cmd in ("exit", "quit"):
            break
        elif cmd == "set":
            _repl_set(session, args)
        elif cmd == "keygen":
            _repl_keygen(session)
        elif cmd == "put":
            _repl_put(session, args)
        elif cmd == "get":
            _repl_get(session, args)
        else:
            print(f"error: unknown command '{cmd}' (type 'help')")


@app.command()
def main() -> None:
    """AtomicVault — one-time secret file sharing."""
    _repl()


if __name__ == "__main__":
    app()
