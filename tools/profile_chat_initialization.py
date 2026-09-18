"""Read-side latency probe on a private COPY of an existing runtime profile.

No provider calls. Output contains timings/counts only; profile bytes stay local.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-home", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vool-startup-profile-") as temp:
        home = Path(temp)
        data = home / "data"
        data.mkdir()
        source = args.source_home / "data"
        with sqlite3.connect((source / "vool_web0_v2.db").as_uri() + "?mode=ro", uri=True) as old:
            with sqlite3.connect(data / "vool_web0_v2.db") as new:
                old.backup(new)
        for name in ("conversation_log.jsonl", "session_summaries.jsonl", "session_meta.json", "a8_digest_key.hex"):
            if (source / name).is_file():
                shutil.copy2(source / name, data / name)
        os.environ.update(VOOL_HOME=str(home), VOOL_KEY_STORAGE_MODE="file",
                          VOOL_CREDENTIAL_STORE="vault", VOOL_DISABLE_MESH_DAEMON="1",
                          VOOL_REGISTER_INSTALLED_OLLAMA_MODELS="0",
                          VOOL_READER_TOOLS_DIR=str(home / "empty-tools"))
        from core.chat_attachments import limits_payload
        from core.memory.entries import list_conversation_sessions
        from core.runtime_continuity import configure_runtime_continuity_db_path, list_runtime_sessions
        from core.runtime_paths import configure_runtime_home
        from storage.db import configure_default_db_path

        configure_runtime_home(home)
        configure_default_db_path(data / "vool_web0_v2.db")
        configure_runtime_continuity_db_path(data / "vool_web0_v2.db")
        # The speech probe is intentionally excluded from an old-tree run: its
        # 162s cold compile is already measured and need not be repeated here.
        from core.artifact_readers import speech_tool
        speech_tool._PROBE_CACHE["en_US"] = (time.monotonic(), {"authorization": "notDetermined"})
        for label, call in (
            ("attachment_limits", limits_payload),
            ("chat_sessions", lambda: list_conversation_sessions(limit=1_000_000)),
            ("runtime_sessions", lambda: list_runtime_sessions(limit=100)),
        ):
            start = time.monotonic()
            result = call()
            print(json.dumps({"operation": label, "seconds": round(time.monotonic() - start, 4),
                              "rows": len(result)}), flush=True)


if __name__ == "__main__":
    main()
