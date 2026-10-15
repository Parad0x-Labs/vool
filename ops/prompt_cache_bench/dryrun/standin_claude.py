"""DRY RUN ONLY: stands in for `claude -p --output-format json`. Reads nothing but the file list.

Like the real CLI, ``total_cost_usd`` is the running total of the session: a ``--resume`` turn
reports what the whole session has cost so far ($0.01 per turn here), so the bench's per-turn
cost is exercised the way the real arm needs it.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

argv = sys.argv[1:]
if "--version" in argv:
    print("0.0.0 (dry-run stand-in for Claude Code)")
    sys.exit(0)
state_path = os.environ.get("STANDIN_SESSIONS", "")
sessions: dict[str, int] = {}
if state_path and os.path.exists(state_path):
    with open(state_path, encoding="utf-8") as handle:
        sessions = json.load(handle)
session = argv[argv.index("--resume") + 1] if "--resume" in argv else f"dryrun-{uuid.uuid4().hex[:12]}"
sessions[session] = sessions.get(session, 0) + 1
if state_path:
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(sessions, handle)
files = " ".join(sorted(os.listdir(".")))
print(json.dumps({
    "type": "result", "is_error": False, "session_id": session, "num_turns": 1, "duration_api_ms": 1,
    "result": f"Dry-run answer from the Claude Code stand-in (not a model). Files here: {files}",
    "total_cost_usd": round(0.01 * sessions[session], 2),
    "usage": {"input_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 5},
}))
