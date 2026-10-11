"""Local IPC to the unchanged MIT session candidate. UNREVIEWED: not for real messages."""

from __future__ import annotations

import base64
import json
import os
import secrets
import select
import stat
import subprocess
from pathlib import Path

from core.drop_fit import DropFitError, require_enabled


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text: str) -> bytes:
    try:
        result = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        if b64(result) != text:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise DropFitError("INVALID_BASE64") from exc


class SessionBridge:
    def __init__(self, root: Path, node: str):
        require_enabled()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key_path = root / "vault.key"
        if not key_path.exists():
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(secrets.token_bytes(32))
                out.flush()
                os.fsync(out.fileno())
        info = key_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            raise DropFitError("VAULT_KEY_PERMISSIONS")
        if info.st_size != 32:
            raise DropFitError("VAULT_KEY_SIZE")
        self.process = subprocess.Popen(
            [node, "--disable-warning=ExperimentalWarning", str(Path(__file__).with_name("bridge.mjs")),
             str(root / "sessions"), str(key_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        try:
            self.fingerprint = self.call("identity")["fingerprint"]
        except BaseException:
            self.close()
            raise

    def call(self, op: str, **args):
        if self.process.poll() is not None:
            raise DropFitError("CRYPTO_UNAVAILABLE")
        self.process.stdin.write(json.dumps({"op": op, **args}) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 15)
        if not ready:
            raise DropFitError("CRYPTO_TIMEOUT")
        line = self.process.stdout.readline(200001)
        if not line or len(line) > 200000:
            raise DropFitError("CRYPTO_UNAVAILABLE")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise DropFitError(reply.get("code", "CRYPTO_REFUSED"))
        return reply["out"]

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.process.stdout and not self.process.stdout.closed:
            self.process.stdout.close()
        self.process.wait(timeout=20)
