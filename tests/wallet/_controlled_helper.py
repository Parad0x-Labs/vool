"""Test harness: a controlled stand-in for the `vool-devauth` helper binary.

Same wire contract as the Swift helper (protocol 2: command in argv, one JSON request on stdin, one JSON reply on
stdout), no Keychain, no prompt. It journals every invocation -- argv, environment, stdin -- so a test can prove
where a secret did and did not travel, and a control file scripts denial, unavailability or a hostile reply that
echoes the request back. A served daemon finds it with no environment variable: `install_for_runtime` writes it
to the exact cache path `core.wallet.device_auth` resolves under the daemon's VOOL_HOME.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core.wallet import device_auth

_SCRIPT = """#!{python}
import json, os, sys, pathlib
here = pathlib.Path(__file__).resolve().parent
state, journal, control = here / "controlled_state.json", here / "controlled_journal.jsonl", here / "controlled_control.json"
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
raw = "" if cmd == "selftest" else sys.stdin.read()
with journal.open("a") as fh:
    fh.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ), "stdin": raw}}) + "\\n")
mode = json.loads(control.read_text()).get("mode", "ok") if control.exists() else "ok"
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
if cmd == "selftest":
    out({{"ok": True, "device_owner_authentication": True, "protocol": 2, "controlled": True}}); sys.exit(0)
if mode == "unavailable":
    out({{"ok": False, "error": "helper_failed", "detail": "controlled: unavailable"}}); sys.exit(1)
if mode == "hostile_echo":
    out({{"ok": False, "error": "hostile <" + raw + ">", "detail": "request was: " + raw}}); sys.exit(1)
try:
    req = json.loads(raw or "{{}}")
except ValueError:
    out({{"ok": False, "error": "bad_request"}}); sys.exit(1)
secrets = json.loads(state.read_text()) if state.exists() else {{}}
key = str(req.get("service", "")) + "|" + str(req.get("account", ""))
if cmd == "store":
    secrets[key] = str(req.get("secret_b64", "")); state.write_text(json.dumps(secrets)); out({{"ok": True}}); sys.exit(0)
if cmd == "read":
    if mode == "deny":
        out({{"ok": False, "error": "denied", "detail": "controlled: denied"}}); sys.exit(1)
    if key not in secrets:
        out({{"ok": False, "error": "not_found"}}); sys.exit(1)
    out({{"ok": True, "secret_b64": secrets[key]}}); sys.exit(0)
if cmd == "forget":
    secrets.pop(key, None); state.write_text(json.dumps(secrets)); out({{"ok": True}}); sys.exit(0)
out({{"ok": False, "error": "usage"}}); sys.exit(1)
"""


class ControlledHelper:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_SCRIPT.format(python=sys.executable), encoding="utf-8")
        os.chmod(self.path, 0o700)

    @property
    def directory(self) -> Path:
        return self.path.parent

    def set_mode(self, mode: str) -> None:
        (self.directory / "controlled_control.json").write_text(json.dumps({"mode": mode}))

    def journal(self) -> list[dict]:
        path = self.directory / "controlled_journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def reset_journal(self) -> None:
        path = self.directory / "controlled_journal.jsonl"
        if path.exists():
            path.unlink()

    def authority(self) -> device_auth.SwiftHelperAuthority:
        """The REAL runtime class, pinned to this helper in-process (no environment involved)."""
        return device_auth.SwiftHelperAuthority(binary=self.path)


def runtime_cache_path(vool_home: Path) -> Path:
    """Where a daemon with VOOL_HOME=`vool_home` (a source checkout, no shipped helper) looks for its compiled helper."""
    return Path(vool_home).resolve() / "data" / "wallet_tools" / f"{device_auth.TOOL_NAME}-{device_auth.TOOL_VERSION}-{device_auth._source_digest()}"


def install_for_runtime(vool_home: Path) -> ControlledHelper:
    return ControlledHelper(runtime_cache_path(vool_home))
