from __future__ import annotations
import typer
import base64
import os
import shlex
from pathlib import Path
from atomicvault.client import AtomicVaultClient, ClientError

app = typer.Typer(name="atomicvault", add_completion=False, invoke_without_command=True)


_BANNER = (
    "AtomicVault interactive shell\n"
    "Type 'help' for available commands, 'exit' to quit."
)

_HELP_TEXT = """\
commands:
  help                  show this message
  exit / quit           leave the REPL
  set encrypt on|off    toggle client-side encryption
  set key <b64>         set encryption key (base64)
  keygen                generate a random 32-byte key (sets it too)
  put <path> [ttl=300]  upload a file
  get <token> out=<path> [key=<b64>] download a secret\
"""


def _parse_kv(parts: list[str]) -> dict[str, str]:
    kv: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k] = v
    return kv


def _repl_put(client: AtomicVaultClient, parts: list[str]) -> None:
    if not parts:
        print("usage: put <path> [ttl=300]")
        return

    path = Path(parts[0])
    kv = _parse_kv(parts[1:])
    ttl = int(kv.get("ttl", "300"))

    try:
        token = client.upload_file(path, ttl)
        print(token)
    except ClientError as exc:
        print(f"error: {exc}")


def _repl_get(client: AtomicVaultClient, parts: list[str]) -> None:
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

    try:
        client.download_file(token, out, inline_key)
        print(f"saved {out}")
    except ClientError as exc:
        print(f"error: {exc}")


def _repl_set(client: AtomicVaultClient, parts: list[str]) -> None:
    if len(parts) < 2:
        print("usage: set encrypt|key <value>")
        return
    prop, value = parts[0], parts[1]
    if prop == "encrypt":
        if value in ("on", "1", "true"):
            client.encrypt = True
        elif value in ("off", "0", "false"):
            client.encrypt = False
        else:
            print("error: use 'on' or 'off'")
            return
        print(f"encrypt = {'on' if client.encrypt else 'off'}")
    elif prop == "key":
        try:
            raw = base64.b64decode(value)
        except Exception:
            print("error: invalid base64")
            return
        if len(raw) != 32:
            print(f"error: key must be 32 bytes, got {len(raw)}")
            return
        client.key_b64 = value
        print("key set")
    else:
        print(f"error: unknown property '{prop}'")


def _repl_keygen(client: AtomicVaultClient) -> None:
    key = os.urandom(32)
    client.key_b64 = base64.b64encode(key).decode()
    print(client.key_b64)


def _repl() -> None:
    """Run an interactive REPL loop."""
    print(_BANNER)
    client = AtomicVaultClient()

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
            _repl_set(client, args)
        elif cmd == "keygen":
            _repl_keygen(client)
        elif cmd == "put":
            _repl_put(client, args)
        elif cmd == "get":
            _repl_get(client, args)
        else:
            print(f"error: unknown command '{cmd}' (type 'help')")


@app.command()
def main() -> None:
    _repl()


if __name__ == "__main__":
    app()
