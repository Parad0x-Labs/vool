"""DRY RUN ONLY: stands in for `claude -p --output-format json`. Reads nothing but the file list."""
from __future__ import annotations

import json
import os
import sys

if "--version" in sys.argv[1:]:
    print("0.0.0 (dry-run stand-in for Claude Code)")
    sys.exit(0)
files = " ".join(sorted(os.listdir(".")))
print(json.dumps({
    "type": "result", "is_error": False, "session_id": "dryrun-session", "num_turns": 1, "duration_api_ms": 1,
    "result": f"Dry-run answer from the Claude Code stand-in (not a model). Files here: {files}",
    "total_cost_usd": 0.0, "usage": {"input_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 5},
}))
