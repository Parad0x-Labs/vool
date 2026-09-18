from __future__ import annotations

import json
from pathlib import Path

TEMPLATE_PY = """\
import json
import sys


def run(inp: dict) -> dict:
    # TODO: implement
    return {"ok": True, "echo": inp}


if __name__ == "__main__":
    inp = json.loads(sys.stdin.read() or "{}")
    out = run(inp)
    sys.stdout.write(json.dumps(out))
"""


def scaffold_tool(tool_name: str, home: str) -> str:
    """Create a sandbox-first custom tool scaffold."""

    base = Path(home) / "tools" / "custom" / tool_name
    base.mkdir(parents=True, exist_ok=True)
    (base / "tool.py").write_text(TEMPLATE_PY, encoding="utf-8")
    manifest = {
        "name": tool_name,
        "entry": "tool.py",
        "stdin_json": True,
        "stdout_json": True,
        "network": False,
        "notes": "Run via sandbox. No network by default.",
    }
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(base)
