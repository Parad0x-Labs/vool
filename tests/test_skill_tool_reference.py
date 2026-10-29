"""The skill tools page lists exactly the tools VOOL's contracts define; drift fails here.

docs/guides/skill-tools-and-limits.md carries a GENERATED table of the names a skill may put in
`allowed-tools` (tools/skill_tool_reference.py). Outside authors found those names only by reading
`runtime_tool_contract_map()`; a stale table would send them back there.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "guides" / "skill-tools-and-limits.md"


def test_the_page_matches_the_contracts(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("VOOL_")}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(ROOT))
    done = subprocess.run([sys.executable, "-m", "tools.skill_tool_reference", "--check"], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_listed_name_is_a_real_tool_the_validator_accepts():
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    text = PAGE.read_text(encoding="utf-8")
    listed = re.findall(r"^\| `([a-z0-9_.-]+)` \|", text, re.MULTILINE)
    assert len(listed) > 100
    assert set(listed) <= set(contracts)
    assert all(contracts[name].model_visible for name in listed)
    assert "workspace.writefile" not in listed and "workspace.write_file" in listed


def test_the_page_is_linked_where_authors_start():
    for path in ("docs/guides/build-and-test-a-skill.md", "AGENTS.md", "docs/SUMMARY.md"):
        assert "skill-tools-and-limits.md" in (ROOT / path).read_text(encoding="utf-8"), path
