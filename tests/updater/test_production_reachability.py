"""Source reachability: every major updater module must have a PRODUCTION caller.

Builds the transitive import closure from the real product entry points (the API
server, the CLI, the wrapper window host, the served web API) and fails if any
core/updater module is missing from it — the anti-"parallel subsystem" test the
2026-09-01 amendment demands. Also pins the RETIREMENT of the legacy detached updater:
installer.self_update must have NO production caller anymore (one install authority).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("core", "apps", "installer", "storage", "network", "tools", "relay", "channels", "ops")
ENTRY_POINTS = (
    "apps/vool_api_server.py",
    "apps/vool_cli.py",
    "installer/update_cli.py",
    "installer/bundle/vool_window.py",
    "core/web/api/service.py",
    "core/runtime_backbone.py",
    "core/self_update_offer.py",
)
UPDATER_PACKAGE = REPO / "core" / "updater"


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _production_py_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


def _imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefix = ("." * (node.level or 0)) + node.module
            found.add(prefix)
            for alias in node.names:
                found.add(f"{prefix}.{alias.name}")
    return found


def _transitive_closure(entry_modules: list[str], graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(entry_modules)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        stack.extend(graph.get(module, set()))
    return seen


def _build_graph() -> tuple[dict[str, set[str]], dict[str, Path]]:
    by_module: dict[str, Path] = {}
    graph: dict[str, set[str]] = {}
    files = _production_py_files()
    for path in files:
        name = _module_name(path)
        by_module[name] = path
    for path in files:
        name = _module_name(path)
        targets: set[str] = set()
        for imported in _imports_of(path):
            candidate = imported.lstrip(".")
            while candidate:
                if candidate in by_module:
                    targets.add(candidate)
                    break
                if "." not in candidate:
                    break
                candidate = candidate.rsplit(".", 1)[0]
        graph[name] = targets
    return graph, by_module


class TestProductionReachability:
    def test_every_updater_module_is_reachable_from_production_entries(self):
        graph, by_module = _build_graph()
        entries = [_module_name(REPO / rel) for rel in ENTRY_POINTS]
        for entry in entries:
            assert entry in by_module, f"entry point missing from production tree: {entry}"
        closure = _transitive_closure(entries, graph)
        missing = []
        for path in sorted(UPDATER_PACKAGE.glob("*.py")):
            if path.name == "__init__.py":
                continue
            name = _module_name(path)
            if name not in closure:
                missing.append(name)
        assert not missing, (
            "core/updater modules with NO production caller (parallel-subsystem risk): "
            f"{missing} — wire them through core.updater.runtime or another entry, or delete them"
        )

    def test_the_retired_detached_updater_has_no_production_caller(self):
        """One install authority: installer/self_update.spawn_detached_update (the
        fail-open-while-unpinned detached lane) must not be reachable from any
        production entry any more."""
        graph, by_module = _build_graph()
        entries = [_module_name(REPO / rel) for rel in ENTRY_POINTS]
        closure = _transitive_closure(entries, graph)
        legacy = "installer.self_update"
        assert legacy in by_module  # the module still exists (its pure helpers + tests)
        assert legacy not in closure, (
            "installer.self_update is reachable from a production entry — the legacy "
            "detached updater was re-wired instead of the signed authority"
        )

    def test_the_chat_shell_carries_the_update_chip_contract(self):
        """The UI surface must expose the status poll + both presses; a chat page that
        lost them fails here, not in a manual browser check."""
        from core.vool_chat_page import render_vool_chat_html

        html = render_vool_chat_html(build_commit="reachability-test")
        for needle in (
            "updateChip",
            "/api/update/status",
            "/api/update/install",
            "/api/update/restart",
            "data-testid=\"update-chip\"",
            "data-testid=\"update-action\"",
        ):
            assert needle in html, f"chat shell lost the update surface: {needle}"

    def test_the_bundle_build_ships_the_external_helper(self):
        """The wrapper build pipeline must package the out-of-process swap helper."""
        script = (REPO / "installer/bundle/build_macos_app.sh").read_text(encoding="utf-8")
        assert "mac_update_helper.sh" in script
        assert "Contents/Resources/update" in script
        assert "NULLAUpdateHelper" in script
        helper = REPO / "installer/update/mac_update_helper.sh"
        assert helper.exists() and helper.stat().st_mode & 0o111, "helper must stay executable"
