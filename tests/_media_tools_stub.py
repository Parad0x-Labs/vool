"""A machine's media tools, stood up in a temp dir for tests: a nebula-media worker that speaks the
worker protocol (``core.media_worker_client``) and ffmpeg/ffprobe executables on PATH.

The stub worker answers every request the way the real one answers a probe of a non-media file
(a typed ``invalid_media`` error), which is what ``NebulaWorkerClient.availability`` reads as a
live, healthy worker. It edits nothing; tests that use it prove registration and selection, not
media mechanism.
"""
from __future__ import annotations

import os
from pathlib import Path

_WORKER = '''import json
import sys

for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    reply = {"id": request.get("id"), "ok": False,
             "error": {"code": "invalid_media", "message": "stub worker: not a media file"}}
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
'''


def make_media_tools(root: Path) -> tuple[Path, Path]:
    """(worker home for NEBULA_MEDIA_HOME, bin dir to put first on PATH)."""
    home = root / "nebula-media"
    (home / "nebula").mkdir(parents=True, exist_ok=True)
    (home / "nebula" / "__init__.py").write_text("", encoding="utf-8")
    (home / "nebula" / "worker.py").write_text(_WORKER, encoding="utf-8")
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    return home, bin_dir


def media_tools_env(root: Path) -> dict[str, str]:
    home, bin_dir = make_media_tools(root)
    return {"NEBULA_MEDIA_HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
