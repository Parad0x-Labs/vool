"""Patch OpenClaw's dashboard reply path to retry a conflicted session commit.

OpenClaw initializes each reply's session state with an optimistic-concurrency
commit. On a conflict its dashboard path (dist/get-reply-*.js) retries EXACTLY ONCE
and then throws "reply session initialization conflicted for <key>" straight into
the chat. Its Telegram path, by contrast, retries the same conflict with backoff
(TELEGRAM_SPOOLED_SESSION_INIT_CONFLICT_RETRY_BASE/MAX_MS). This restores that
parity for the dashboard: retry up to 6 times with exponential backoff (~120ms →
~3.8s, ~7.5s total) before giving up, which absorbs the transient races that fire
when a second message lands while the previous turn's session is still settling.

Surgical, idempotent, and reversible (--remove restores the original). OpenClaw npm
upgrades overwrite dist, so the launcher re-applies this every start. If OpenClaw's
code changes shape (the target strings no longer match), the patch is a safe no-op.

Usage:
    python -m installer.patch_openclaw_session_retry [--file <path>] [--remove]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The dashboard entry point passes a boolean "already retried" flag; we turn it into
# a numeric attempt counter so the conflict handler can retry more than once.
_ENTRY_ORIG = "return await initSessionStateAttempt(params, false);"
_ENTRY_PATCHED = "return await initSessionStateAttempt(params, 0);"

_CONFLICT_ORIG = "if (!staleSnapshotRetried) return await initSessionStateAttempt(params, true);"
# Marker (/*vool-session-retry*/) makes the patched state detectable + reversible.
_CONFLICT_PATCHED = (
    "if (staleSnapshotRetried < 6) { await new Promise((r) => setTimeout(r, 120 * Math.pow(2, staleSnapshotRetried))); "
    "return await initSessionStateAttempt(params, staleSnapshotRetried + 1); } /*vool-session-retry*/"
)


def patch_text(js: str) -> str:
    out = js
    if _ENTRY_ORIG in out:
        out = out.replace(_ENTRY_ORIG, _ENTRY_PATCHED)
    if _CONFLICT_ORIG in out:
        out = out.replace(_CONFLICT_ORIG, _CONFLICT_PATCHED)
    return out


def unpatch_text(js: str) -> str:
    out = js
    if _CONFLICT_PATCHED in out:
        out = out.replace(_CONFLICT_PATCHED, _CONFLICT_ORIG)
    if _ENTRY_PATCHED in out:
        out = out.replace(_ENTRY_PATCHED, _ENTRY_ORIG)
    return out


def is_patched(js: str) -> bool:
    return "/*vool-session-retry*/" in js


def is_patchable(js: str) -> bool:
    return _CONFLICT_ORIG in js or is_patched(js)


def _openclaw_dist_dir() -> Path | None:
    roots: list[Path] = []
    for cmd in (["npm", "root", "-g"], ["npm.cmd", "root", "-g"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                roots.append(Path(out.stdout.strip()))
                break
        except Exception:
            continue
    which = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if which:
        roots.append(Path(which).resolve().parent / "node_modules")
    for root in roots:
        dist = root / "openclaw" / "dist"
        if dist.is_dir():
            return dist
    return None


def locate_get_reply_js() -> Path | None:
    env = os.environ.get("OPENCLAW_GET_REPLY_JS")
    if env and Path(env).is_file():
        return Path(env)
    dist = _openclaw_dist_dir()
    if dist is None:
        return None
    # Filename carries a build hash; match the one that actually holds the conflict site.
    for candidate in sorted(dist.glob("get-reply-*.js")):
        try:
            if is_patchable(candidate.read_text(encoding="utf-8")):
                return candidate
        except Exception:
            continue
    return None


def apply(js_path: Path, *, remove: bool = False) -> str:
    text = js_path.read_text(encoding="utf-8")
    new_text = unpatch_text(text) if remove else patch_text(text)
    if new_text == text:
        return "already-correct"
    js_path.write_text(new_text, encoding="utf-8")
    return "removed" if remove else "patched"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patch_openclaw_session_retry")
    parser.add_argument("--file", default="", help="path to get-reply-*.js (auto-detected if omitted)")
    parser.add_argument("--remove", action="store_true", help="restore OpenClaw's original single-retry behavior")
    args = parser.parse_args(argv)

    js_path = Path(args.file) if args.file else locate_get_reply_js()
    if js_path is None or not js_path.is_file():
        print("OpenClaw get-reply bundle not found; skipping session-retry patch.", file=sys.stderr)
        return 1
    result = apply(js_path, remove=args.remove)
    print(f"OpenClaw session-retry {result}: {js_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
