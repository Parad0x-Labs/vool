"""MILESTONE 0 — the machine-readable current boundary map. Rerunnable.

Produces `workspace/ARCH-M0-BOUNDARY-MAP.json`: the turn entrypoints, the claimant and
finality sites, the obligation minters, the permission deciders, the effect executors,
the state stores, the retry owners, the final-byte authors, and the core->apps import
edges (the M1 target), measured by AST over the real tree — never hand-maintained.

Usage:  .venv/bin/python ops/boundary_map.py [--out PATH]
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The production packages in scope (M0 task 3 inventory).
INVENTORY_ROOTS = (
    "apps",
    "core/agent_runtime",
    "core/conductor",
    "core/orchestration",
    "core/execution",
    "core",
    "storage",
    "tools",
    "providers",
)


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
    except Exception:
        return ""


def _imports_of(path: Path) -> set[str]:
    """Top-level module names a file imports (absolute imports only)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _files_under(relative: str) -> list[Path]:
    root = PROJECT_ROOT / relative
    if not root.is_dir():
        return []
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _scan(marker: str, roots: tuple[str, ...] = INVENTORY_ROOTS) -> dict[str, list[str]]:
    """Files whose AST contains the marker text in a def/class/assign name, per root."""
    hits: dict[str, list[str]] = {}
    for root in roots:
        found: list[str] = []
        for path in _files_under(root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and marker in node.name:
                    found.append(str(path.relative_to(PROJECT_ROOT)))
                    break
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and marker in target.id:
                            found.append(str(path.relative_to(PROJECT_ROOT)))
                            break
        if found:
            hits[root] = sorted(set(found))
    return hits


def _string_apps_edges(path: Path) -> bool:
    """Dynamic executable edges — via the ONE shared scanner (M1 corrective).

    The regex this replaces caught only direct import_module spellings; the
    shared scanner (core.architecture_edges) also catches __import__, aliases,
    and any call handing an apps module path to a loader. Gate and map now
    share one instrument, cross-tested against the shipped sabotage corpus.
    """
    from core.architecture_edges import apps_edges_in_file

    return bool(apps_edges_in_file(path))


def build_map() -> dict:
    # --- import direction: core -> apps (the M1 cycle target) -----------------------------
    core_to_apps: dict[str, list[str]] = defaultdict(list)
    for path in _files_under("core"):
        imported = _imports_of(path)
        if "apps" in imported or _string_apps_edges(path):
            core_to_apps[str(path.relative_to(PROJECT_ROOT))] = sorted(imported & {"apps"} or ["apps"])
    apps_to_core = sum(1 for path in _files_under("apps") if "core" in _imports_of(path))

    # --- entrypoints ----------------------------------------------------------------------
    entrypoints = {
        "http_api": ["core/web/api/service.py", "core/web/api/runtime.py"],
        "cli": ["apps/vool_agent.py"],
        "server_main": ["apps/vool_api_server.py"],
    }

    # --- the authority sites, by their own names ------------------------------------------
    authorities = {
        "obligation_minters": _scan("obligation"),
        "claimant_sites": _scan("claim"),
        "finality_sites": _scan("finalize"),
        "permission_deciders": _scan("permission"),
        "effect_executors": _scan("execute"),
        "state_stores": _scan("repository") or _scan("store"),
        "retry_owners": _scan("retry"),
        "final_byte_authors": _scan("canonical_content"),
    }

    return {
        "schema": "v1",
        "generated_at": _git("log", "-1", "--format=%cI"),
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "tree_dirty": bool(_git("status", "--porcelain")),
        "inventory_roots": list(INVENTORY_ROOTS),
        "entrypoints": entrypoints,
        "authorities": authorities,
        "import_direction": {
            "core_imports_apps": dict(sorted(core_to_apps.items())),
            "core_imports_apps_count": len(core_to_apps),
            "apps_files_importing_core": apps_to_core,
        },
        "notes": [
            "authorities are located by name-marker AST scan (def/class/assign); a site "
            "appears under every marker it names — this is a MAP for the convergence "
            "campaign's M1+ decisions, not a claim that each site is a duplicate authority",
            "core_imports_apps is the M1 cycle target; apps->core is the correct direction",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(PROJECT_ROOT / "workspace" / "ARCH-M0-BOUNDARY-MAP.json"))
    args = parser.parse_args()
    mapping = build_map()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mapping, indent=2) + "\n")
    print(json.dumps({
        "head": mapping["head"],
        "core_imports_apps_count": mapping["import_direction"]["core_imports_apps_count"],
        "apps_files_importing_core": mapping["import_direction"]["apps_files_importing_core"],
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
