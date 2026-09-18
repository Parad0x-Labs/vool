"""The historical-gold vault (``recovery/historical-gold/``) is PRESERVATION, not runtime.

It must be impossible for vaulted code to be imported or registered by the live runtime — the vault
carries whole re-homed trees (``.../src/core/kernel``, ``.../src/core/school`` …) that would SHADOW
canonical modules if any of them ever landed on ``sys.path``. These tests fail closed if that
containment is ever weakened.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VAULT = REPO / "recovery" / "historical-gold"
RUNTIME_DIRS = ("core", "apps", "tools", "relay", "storage")


def test_vault_exists_and_is_not_a_python_package() -> None:
    assert VAULT.is_dir(), "the historical-gold vault must exist"
    # No __init__.py at the vault roots — it must never be importable as a package.
    assert not (REPO / "recovery" / "__init__.py").exists(), "recovery/ must not be a package"
    assert not (VAULT / "__init__.py").exists(), "recovery/historical-gold/ must not be a package"


def test_no_runtime_source_imports_the_vault() -> None:
    """No file under the runtime dirs may import a top-level ``recovery`` module (AST-checked, so a
    'recovery' substring in a string/comment does not count)."""
    offenders: list[str] = []
    for d in RUNTIME_DIRS:
        root = REPO / d
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        if n.name.split(".")[0] == "recovery":
                            offenders.append(f"{py}: import {n.name}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "recovery" or (node.level and "recovery" in (node.module or "")):
                        offenders.append(f"{py}: from {node.module} import ...")
    assert not offenders, "runtime code must never import the vault:\n" + "\n".join(offenders)


def test_pytest_does_not_collect_the_vault() -> None:
    """The vault preserves recovered TEST files too (``.../src/tests/test_*.py``). pytest must not
    collect them — they import the vaulted package, which is not on sys.path, so collecting them
    breaks the whole suite's collection. Guarded at the config level so the regression cannot recur.
    """
    import tomllib

    ini = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    ini = ini.get("tool", {}).get("pytest", {}).get("ini_options", {})
    # norecursedirs is the LOAD-BEARING guard: the shard manifest builder (ops/pytest_manifest.py)
    # runs pytest with `-o addopts=`, which clears addopts, so an --ignore in addopts does NOT reach
    # the gate's collection. norecursedirs prunes the dir during recursion and survives that.
    assert "recovery" in ini.get("norecursedirs", []), (
        "pyproject [tool.pytest.ini_options].norecursedirs must include 'recovery' — the shard "
        "manifest builder clears addopts, so only norecursedirs keeps the vault out of collection"
    )
    assert "--ignore=recovery" in ini.get("addopts", []), (
        "keep --ignore=recovery in addopts too, so a direct `pytest` invocation is explicit"
    )


def test_vault_is_not_under_any_scanner_root() -> None:
    """The plugin/skill/native-skill scanners must not walk the vault. Assert their configured roots
    resolve outside recovery/ (guards against a scanner picking up vaulted SKILL.md / plugins)."""
    import importlib

    suspects = []
    for mod_name, attrs in (
        ("core.plugin_skills", ("SKILLS_DIRS", "PLUGIN_DIRS", "_skill_roots")),
        ("core.native_skill_library", ("NATIVE_SKILL_ROOT", "_roots")),
        ("core.plugin_catalog", ("PLUGIN_ROOT", "_plugin_roots")),
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for attr in attrs:
            val = getattr(mod, attr, None)
            if val is None:
                continue
            paths = val if isinstance(val, (list, tuple, set)) else [val]
            for p in paths:
                if "recovery" in str(p):
                    suspects.append(f"{mod_name}.{attr} -> {p}")
    assert not suspects, "a scanner root points into the vault:\n" + "\n".join(suspects)
