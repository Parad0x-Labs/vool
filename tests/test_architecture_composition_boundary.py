"""MILESTONE 1 — the composition boundary, as an executable conformance gate.

THE INVARIANT
-------------
`core/` owns runtime behavior; `apps/` is process composition (CLI parsing, server
entrypoints, facades). The dependency direction is ONE-WAY: anything under core may
never import `apps` — not at module level, not lazily inside a function, because a lazy
`from apps.x import y` inside a core seam is the same reversed edge with a smaller
blast radius (the core↔apps cycle this milestone removed was exactly that shape on its
return leg: the agent's lazy `from core.web.api.runtime import enforce_url_grounding`).

WHAT THIS FILE PINS
-------------------
* No file under `core/` imports `apps` (AST scan over every import node, lazy ones
  included).
* The `apps/vool_agent.py` and `apps/vool_daemon.py` facades stay THIN: they contain
  no class definitions and no agent/daemon methods — composition only. A facade that
  grows business decisions is a second authority, which the campaign forbids.
* `ops/boundary_map.py` (the rerunnable M0 measurement) agrees with the test's own
  scan — the map and the gate can never silently disagree.

SABOTAGE DISCIPLINE
-------------------
Add `import apps` (module-level or lazy) to any core file and
`test_no_core_module_imports_apps` goes red naming the file; define a class in the
agent facade and `test_the_agent_facade_stays_thin` goes red. Verified at M1 landing;
see ATTEMPTED_FIXES §M1.
"""
from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = PROJECT_ROOT / "core"

#: The facades M1 left behind, with their written removal conditions in-file.
THIN_FACADES = ("apps/vool_agent.py", "apps/vool_daemon.py")


def _core_files() -> list[Path]:
    return sorted(p for p in CORE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _apps_edges(path: Path) -> list[int]:
    """The ONE shared scanner (core.architecture_edges) — every executable shape.

    M1 CORRECTIVE: the checkpoint cf410ae0 claimed zero core->apps edges while
    two adapters executed `import_module("apps.vool_agent")` — invisible to the
    AST-import-only scan both this gate and the boundary map used. The regex
    patch that followed caught only direct import_module spellings. Both private
    scans are now REPLACED by the shared scanner so gate and measurement cannot
    agree on a blind spot; the corpus test below proves the shapes it catches.
    """
    from core.architecture_edges import apps_edges_in_file

    return apps_edges_in_file(path)


def test_no_core_module_imports_apps() -> None:
    """The one-way composition boundary: core never depends on apps — at any
    depth, in any spelling (static, lazy, aliased, dynamic, __import__)."""
    offenders = {}
    for path in _core_files():
        hits = _apps_edges(path)
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits
    assert not offenders, f"core files import apps (composition boundary broken): {offenders}"


def test_the_shared_scanner_catches_every_dynamic_shape() -> None:
    """The sabotage corpus (operator amendment): static imports, lazy
    from-imports, direct/attr/ALIASED import_module, and __import__ are ALL
    rejected; clean core and prose-only mentions are NOT. This is the same
    corpus the scanner module ships, so the gate and any future instrument are
    cross-tested against one corpus, never two disagreeing scans."""
    from core.architecture_edges import (
        CORPUS_EXPECTED_FLAGGED,
        SABOTAGE_CORPUS,
        apps_edges_in_source,
    )

    for name, source in SABOTAGE_CORPUS:
        flagged = bool(apps_edges_in_source(source))
        expected = name in CORPUS_EXPECTED_FLAGGED
        assert flagged == expected, (
            f"scanner verdict wrong for {name}: flagged={flagged} expected={expected}"
        )


def test_the_gate_flags_a_planted_dynamic_import() -> None:
    """Tree-level sabotage: a file planted in core/ with an aliased
    import_module of apps IS flagged by the tree scan — proving the instrument
    end-to-end, not just the pure function."""
    from core.architecture_edges import files_with_apps_edges

    planted = CORE_ROOT / "_sabotage_planted_dynamic.py"
    planted.write_text(
        "from importlib import import_module as _im\n"
        "def load():\n    return _im('apps.vool_agent')\n",
        encoding="utf-8",
    )
    try:
        offenders = files_with_apps_edges(CORE_ROOT)
        assert any(name.endswith("_sabotage_planted_dynamic.py") for name in offenders), offenders
    finally:
        planted.unlink()


def test_the_agent_facade_stays_thin() -> None:
    """A facade is composition only — no class definitions, no method bodies."""
    for facade in THIN_FACADES:
        path = PROJECT_ROOT / facade
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert not classes, f"{facade} grew class definitions ({classes}) — a facade must not own behavior"
        # And it must remain small: composition, not a second implementation.
        assert len(path.read_text().splitlines()) < 200, (
            f"{facade} grew past facade size — business decisions belong in core"
        )


def test_the_boundary_map_agrees_with_this_gate() -> None:
    """`ops/boundary_map.py` (the M0 measurement) and this gate see the same edges.

    A map that drifts from the enforced truth is a stale doc with a script attached;
    this pins the two to each other.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from ops.boundary_map import build_map

    measured = build_map()["import_direction"]["core_imports_apps_count"]
    actual = sum(1 for path in _core_files() if _apps_edges(path))
    assert measured == actual == 0, (measured, actual)
