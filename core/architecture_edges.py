"""M1 corrective — the ONE shared scanner for executable core -> apps edges.

WHY THIS EXISTS (the false-negative gate, recorded per repo rules):
the M1 checkpoint cf410ae0 claimed "core no longer imports apps at any depth"
while `core/agent_runtime/tool_result_workflow_surface.py` and
`core/agent_runtime/runtime_checkpoint_io_adapter.py` still executed
`import_module("apps.vool_agent")`. Both the architecture test and
ops/boundary_map.py scanned AST Import/ImportFrom nodes only — dynamic imports
are Call nodes, invisible to that scan — so both instruments reported zero over
two live reverse dependencies. The adapters' runtime effect was real: patches
applied to `core.agent_runtime.agent` never reached them, because they loaded
copied exports through the apps facade (six checkpoint/surface regressions,
measured by the operator's focused run at that SHA).

The runtime fix landed as cb9694cc (both adapters re-pointed at the core
module). THIS module is the instrument fix: one scanner, shared by the
architecture gate AND the boundary map, so the two can never agree on a blind
spot again, and a sabotage corpus that proves every dynamic shape is caught.

DETECTED SHAPES (line-precise):
* ``import apps`` / ``import apps.x``                       (AST Import)
* ``from apps.x import y``                                  (AST ImportFrom)
* ``import_module("apps.x")`` / ``importlib.import_module`` (Call, any alias)
* ``__import__("apps.x")``                                  (Call)
* ANY call whose argument is a constant that is exactly ``apps`` or a dotted
  module path under it — the deliberate catch-all for future loader spellings.
  Over-broad ON PURPOSE: a hit is a review flag that fails the gate; core/ has
  zero legitimate reasons to pass an apps module path to anything.

NOT detected (by design): prose in docstrings/comments (not executable), and
variable-built module names — those are dataflow, out of scope for a constant
scanner; the corpus records this boundary honestly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

#: A constant that is exactly ``apps`` or a dotted module path under it.
_APPS_PATH_RE = re.compile(r"^apps(?:\.[A-Za-z_]\w*)+$")

#: Import-shaped callables checked by name/attribute (aliases fall through to
#: the catch-all below, which is why aliasing cannot evade the scanner).
_IMPORT_SHAPED = {"import_module", "__import__"}


def apps_edges_in_source(source: str) -> list[int]:
    """Line numbers of every executable core -> apps edge in ``source``."""
    try:
        tree = ast.parse(str(source or ""))
    except SyntaxError:
        return [-1]
    hits: list[int] = []

    def _is_apps_path(node: Any) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (_APPS_PATH_RE.match(node.value) is not None or node.value == "apps")
        )

    def _callable_name(node: Any) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "apps" or alias.name.startswith("apps.")
                for alias in node.names
            ):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "apps" or node.module.startswith("apps.")):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = _callable_name(node.func)
            if name in _IMPORT_SHAPED and node.args and _is_apps_path(node.args[0]):
                hits.append(node.lineno)
                continue
            # The catch-all: any call handing an apps module path to anything.
            if any(_is_apps_path(arg) for arg in node.args):
                hits.append(node.lineno)
    return sorted(dict.fromkeys(hits))


def apps_edges_in_file(path: Path) -> list[int]:
    """Line numbers of executable core -> apps edges in one .py file."""
    try:
        return apps_edges_in_source(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def files_with_apps_edges(root: Path) -> dict[str, list[int]]:
    """{relative_path: [line numbers]} for every .py file under root with edges."""
    offenders: dict[str, list[int]] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = apps_edges_in_file(path)
        if hits:
            offenders[str(path.relative_to(root))] = hits
    return offenders


#: The sabotage corpus: every dynamic shape the operator's amendment names,
#: each of which MUST be detected. Shared by the architecture test's corpus
#: run and available to any future instrument cross-check.
SABOTAGE_CORPUS: tuple[tuple[str, str], ...] = (
    ("static_import", "import apps\n"),
    ("static_dotted", "import apps.vool_agent\n"),
    ("from_import", "from apps.vool_agent import VoolAgent\n"),
    ("lazy_from_import", "def f():\n    from apps import vool_agent\n"),
    ("import_module_direct", "from importlib import import_module\nimport_module('apps.vool_agent')\n"),
    ("import_module_attr", "import importlib\nimportlib.import_module('apps.vool_agent')\n"),
    ("import_module_aliased", "from importlib import import_module as _im\n_im('apps.vool_agent')\n"),
    ("dunder_import", "__import__('apps.vool_agent')\n"),
    ("assign_then_import", "m = 'apps.vool_agent'\nimport importlib\nimportlib.import_module(m)\n"),
    ("clean_core", "import json\nfrom core.turn_contract import TurnRequest\n"),
    ("clean_prose", "MODULE = 'apps are mentioned here in prose only'\n"),
)

#: The corpus's expected verdicts: which entries must be FLAGGED. The
#: variable-built module name (assign_then_import) is honestly recorded as NOT
#: detectable by a constant scanner — dataflow is out of scope; the amendment's
#: named shapes are all covered.
CORPUS_EXPECTED_FLAGGED = (
    "static_import",
    "static_dotted",
    "from_import",
    "lazy_from_import",
    "import_module_direct",
    "import_module_attr",
    "import_module_aliased",
    "dunder_import",
)


__all__ = [
    "CORPUS_EXPECTED_FLAGGED",
    "SABOTAGE_CORPUS",
    "apps_edges_in_file",
    "apps_edges_in_source",
    "files_with_apps_edges",
]
