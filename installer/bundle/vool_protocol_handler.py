"""Windows ``vool://`` protocol handler for the bundled VOOL installation.

The handler validates the callback shape, starts or reuses the bundle supervisor
when necessary, and forwards the two OAuth parameters to the shared desktop
callback endpoint. It never exchanges the code, checks PKCE state itself, or
writes the callback URI to a log or file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

_API_CALLBACK_URL = "http://127.0.0.1:11435/api/auth/openrouter/callback"
_RETRY_COUNT = 30
_RETRY_DELAY_SECONDS = 1.0
_MAX_CODE_LENGTH = 4096
_MAX_STATE_LENGTH = 1024


def parse_callback_uri(uri: str) -> tuple[str, str]:
    """Return decoded ``(code, state)`` from the exact VOOL callback shape."""
    if not isinstance(uri, str) or len(uri) > 16384:
        raise ValueError("callback URI is invalid")
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "vool" or parsed.netloc.lower() != "auth":
        raise ValueError("callback URI must use vool://auth")
    if parsed.path.rstrip("/").lower() != "/openrouter/callback" or parsed.fragment:
        raise ValueError("callback URI path is invalid")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    values: dict[str, str] = {}
    for key, value in pairs:
        key = key.strip().lower()
        if key not in {"code", "state"} or key in values:
            raise ValueError("callback URI query is invalid")
        values[key] = value
    code = values.get("code", "").strip()
    state = values.get("state", "").strip()
    if (
        not code
        or not state
        or len(code) > _MAX_CODE_LENGTH
        or len(state) > _MAX_STATE_LENGTH
        or any(ord(char) < 0x20 for char in code + state)
    ):
        raise ValueError("callback URI must contain valid code and state")
    return code, state


def _post_callback(code: str, state: str) -> bool:
    body = json.dumps({"code": code, "state": state}, separators=(",", ":")).encode("utf-8")
    request = Request(
        _API_CALLBACK_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            return 200 <= int(response.status) < 300
    except (HTTPError, OSError, URLError, ValueError):
        return False


def _start_bundle(root: Path) -> None:
    launcher = root / "VOOL.cmd"
    if not launcher.is_file():
        raise FileNotFoundError("VOOL.cmd is missing")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [os.environ.get("ComSpec", "cmd.exe"), "/c", str(launcher)],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def deliver_callback(uri: str, *, root: Path | None = None, sleep=time.sleep) -> bool:
    """Validate and deliver a callback through the shared desktop runtime."""
    code, state = parse_callback_uri(uri)
    bundle_root = (root or Path(__file__).resolve().parent).resolve()
    started = False
    for attempt in range(_RETRY_COUNT):
        if _post_callback(code, state):
            return True
        if not started:
            _start_bundle(bundle_root)
            started = True
        if attempt + 1 < _RETRY_COUNT:
            sleep(_RETRY_DELAY_SECONDS)
    return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        return 0 if deliver_callback(args[0]) else 1
    except (FileNotFoundError, OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
