"""The model sees the tools its task needs, read turns see nothing that mutates, the set is stable.

Since the bounded capability catalog (f4614c95), discovery resolved candidates through registered
implementations that nothing registered in the served runtime, so every turn -- a file write, a code
edit, a hive request -- offered the same eight navigation tools and no write tool at all. The live
runtime catalog is now synced into the graph before discovery, the task's own capability is offered
first, a read turn is never offered a mutating tool, and the bound is applied in a deterministic
order instead of hash-dependent set order.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core.capability_graph import capability_hint_from_task_class, family_hint_from_task_class
from core.prompt_normalizer import _tool_intent_catalog_text

_INTENT_RE = re.compile(r"- ([a-z_0-9]+\.[a-z_0-9]+)\(")
_MUTATING = {"machine.write_file", "machine.ensure_directory", "machine.move_path", "workspace.write_file",
             "workspace.replace_in_file", "workspace.apply_unified_diff", "workspace.ensure_directory",
             "workspace.rollback_last_change", "operator.cleanup_temp_files", "operator.move_path"}


def catalog(task_class: str) -> set[str]:
    text = _tool_intent_catalog_text(
        family_hint=family_hint_from_task_class(task_class),
        capability_hint=capability_hint_from_task_class(task_class),
    )
    return set(_INTENT_RE.findall(text))


# The two write-offer tests of the v14 line are not carried: they seat write tools from the v14 task
# classes "file_write"/"file_edit" through capability_hint, while main seats write tools from the
# user's own demand signals in core.tool_offer_assembly (pinned by main's
# tests/test_p0_tool_offer_authority.py). The read-only and bounded-catalog laws below still hold.


@pytest.mark.parametrize("task_class", ["file_read", "code_review", "debugging"])
def test_a_read_turn_is_never_offered_a_mutating_tool(task_class):
    offered = catalog(task_class)
    assert offered, f"{task_class} offered nothing"
    assert not offered & _MUTATING, f"{task_class} offered {sorted(offered & _MUTATING)}"


def test_a_file_read_turn_sees_its_reader():
    assert "machine.read_file" in catalog("file_read")


def test_an_untyped_turn_keeps_the_navigation_bound():
    offered = catalog("unknown")
    assert len(offered) <= 8 and {"respond.direct", "operator.list_tools"} <= offered


def test_a_tool_the_runtime_does_not_offer_is_never_exposed():
    # With no hive bridge configured, hive research is not offered by the live catalog.
    assert "hive.research_topic" not in catalog("hive")


def test_the_bounded_catalog_does_not_depend_on_hash_order(tmp_path):
    probe = (
        "import json,os,re,sys;sys.path.insert(0,os.getcwd());"
        "from pathlib import Path;from core.runtime_paths import configure_runtime_home;"
        "from storage.db import configure_default_db_path;home=Path(sys.argv[1]);"
        "configure_runtime_home(home);configure_default_db_path(home/'d.db');"
        "from core.prompt_normalizer import _tool_intent_catalog_text as t;"
        "from core.capability_graph import family_hint_from_task_class as f, capability_hint_from_task_class as c;"
        "print(json.dumps({k:re.findall(r'- ([a-z_0-9]+\\.[a-z_0-9]+)\\(',t(family_hint=f(k),capability_hint=c(k))) for k in ('file_read','file_edit','file_write')}))"
    )
    seen = []
    for seed in ("1", "2", "3"):
        home = tmp_path / f"home-{seed}"
        home.mkdir()
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", probe, str(home)], cwd=str(Path(__file__).resolve().parents[1]),
                             capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        seen.append(json.loads(out.stdout.strip().splitlines()[-1]))
    assert seen[0] == seen[1] == seen[2]
